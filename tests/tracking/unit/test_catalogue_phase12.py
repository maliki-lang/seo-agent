"""Phase 12 Serper preselection and selection score v2 tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.builder import build_keyword_catalogue
from data_sources.tracking.catalogue.classify import classify_build
from data_sources.tracking.catalogue.families import derive_families_for_build
from data_sources.tracking.catalogue.policy import CatalogueSelectionPolicy
from data_sources.tracking.catalogue.preselection import (
    _allocate_lane_slots,
    preselect_serp_pool,
)
from data_sources.tracking.catalogue.scoring import (
    gsc_opportunity_score_v2,
    gsc_protection_score,
    score_candidate_v2,
    serp_component_scores,
)
from data_sources.tracking.catalogue.validate_serp import validate_serp_for_build
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.costs import CostLedger
from data_sources.tracking.enums import RunStatus, RunType
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _config(tmp_path) -> TrackingConfig:
    policy = CatalogueSelectionPolicy()
    return TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase12.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_selection_policy=policy,
        catalogue_relevance_terms=list(policy.flat_relevance_terms()),
        catalogue_selected_limit=55,
        catalogue_serper_preselection_limit=100,
        catalogue_min_impressions=10,
        catalogue_min_clicks_protect=1,
        daily_cost_cap_usd=Decimal("25"),
    )


def test_protection_vs_opportunity_split():
    prot, _ = gsc_protection_score(
        clicks=5, impressions=200, weighted_position=2.0, min_impressions=10
    )
    opp, reasons = gsc_opportunity_score_v2(
        clicks=5,
        impressions=200,
        weighted_position=2.0,
        weighted_ctr=0.2,
        min_impressions=10,
        target_actionability=0.8,
    )
    assert prot > opp  # top visibility is protection-heavy
    assert "already_top_visibility_low_opportunity" in reasons

    opp2, reasons2 = gsc_opportunity_score_v2(
        clicks=0,
        impressions=80,
        weighted_position=8.0,
        weighted_ctr=0.01,
        min_impressions=10,
        target_actionability=0.7,
        multi_page_class="cannibalization_candidate",
    )
    assert opp2 > 0.4
    assert "cannibalization_optimization" in reasons2
    # No fixed clicks>=1 bonus in v2 opportunity.
    assert "existing_clicks_worth_protecting" not in reasons2


def test_serper_component_separation():
    vis, opp, align, conf, feas, reasons = serp_component_scores(
        position=2, proposed_target_ranks=True, validated=True, target_actionability=0.8
    )
    assert vis is not None and vis > 0.8
    assert opp is not None and opp < 0.4
    assert align == 0.9
    assert conf == 0.85
    assert feas is not None
    assert "top3_high_visibility_low_opportunity" in reasons

    missing = serp_component_scores(
        position=None, proposed_target_ranks=None, validated=False
    )
    assert missing[0] is None


def test_score_v2_missing_evidence_confidence_and_hard_exclude():
    policy = CatalogueSelectionPolicy()
    base = {
        "eligibility_status": "eligible",
        "search_intent": "transactional_category",
        "brand_status": "non_branded",
        "family_role": "primary",
        "business_relevance_score": 0.8,
        "business_relevance_status": "relevant",
        "target_actionability_score": 0.7,
        "target_page_status": "observed_page_suitable",
        "gsc_clicks": 2,
        "gsc_impressions": 50,
        "gsc_weighted_position": 8.0,
        "gsc_weighted_ctr": 0.02,
        "source_row_count": 3,
        "source_date_count": 5,
        "multi_page_class": "normal_page_variation",
        "competitor_status": "none",
        "claims_review_required": 0,
    }
    without_serper = score_candidate_v2(
        base,
        score_weights=policy.score_weights,
        penalties=policy.penalties,
        min_impressions=10,
        require_serper=False,
    )
    assert without_serper.selection_score_v2 is not None
    assert without_serper.score_confidence < 1.0
    assert "serp_feasibility_score" in without_serper.missing_evidence_fields

    branded = dict(base, brand_status="branded")
    excluded = score_candidate_v2(
        branded,
        score_weights=policy.score_weights,
        penalties=policy.penalties,
        min_impressions=10,
    )
    assert excluded.hard_excluded is True
    assert excluded.selection_score_v2 is None


def test_lane_slot_allocation_sums_to_limit():
    policy = CatalogueSelectionPolicy()
    slots = _allocate_lane_slots(policy.lane_quotas, 100, 55)
    assert sum(slots.values()) == 100
    small = _allocate_lane_slots(policy.lane_quotas, 6, 55)
    assert sum(small.values()) == 6


def test_preselect_and_validate_preselected_pool(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    run = RunLog(
        run_id="run-phase12",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 10),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    queries = [
        ("comfortable walking shoes", 3, 60),
        ("slippers", 2, 40),
        ("mules singapore", 1, 35),
        ("nex shoes", 2, 80),
        ("shoes nex", 1, 50),
        ("shoe shop near me", 1, 45),
        ("shoe store near me", 2, 55),
        ("office shoes", 1, 30),
        ("kids shoes singapore", 1, 28),
        ("best shoes for walking", 1, 33),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run.run_id,
                as_of_date=run.as_of_date,
                date=run.as_of_date,
                query=query,
                page=(
                    "https://sunnystep.com/collections/walking-shoes"
                    if "walking" in query or query == "slippers"
                    else "https://sunnystep.com/"
                ),
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=clicks / impr,
                position=7.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for query, clicks, impr in queries
        ]
    )
    build = build_keyword_catalogue(
        store, config, gsc_start=run.as_of_date, gsc_end=run.as_of_date, created_by="p12"
    )
    classify_build(store, config, build_id=build["build_id"])
    derive_families_for_build(store, config, build_id=build["build_id"])
    pre = preselect_serp_pool(store, config, build_id=build["build_id"], limit=6)
    assert pre["preselected_count"] <= 6
    assert pre["preselected_count"] >= 1
    preselected = store.fetchall(
        "SELECT candidate_id, family_id, serp_preselect_rank, selection_score_v2 "
        "FROM keyword_candidates WHERE build_id = ? AND serp_preselected = 1 "
        "ORDER BY serp_preselect_rank",
        (build["build_id"],),
    )
    assert len(preselected) == pre["preselected_count"]
    family_ids = [r["family_id"] for r in preselected if r["family_id"]]
    assert len(family_ids) == len(set(family_ids))

    calls = {"n": 0}

    def fake_search(keyword: str):
        calls["n"] += 1
        return {
            "organic": [
                {"link": "https://example.com/a"},
                {"link": "https://sunnystep.com/collections/walking-shoes"},
            ]
        }

    result = validate_serp_for_build(
        store,
        config,
        build_id=build["build_id"],
        pool="preselected",
        search_fn=fake_search,
        cost_ledger=CostLedger(Decimal("25")),
    )
    assert result["pool"] == "preselected"
    assert result["validated"] == len(preselected)
    assert calls["n"] == len(preselected)
    scored = store.fetchall(
        "SELECT serp_visibility_score, serp_opportunity_score, serp_feasibility_score, "
        "selection_score_v2, score_confidence FROM keyword_candidates "
        "WHERE build_id = ? AND serp_preselected = 1",
        (build["build_id"],),
    )
    assert all(r["serp_visibility_score"] is not None for r in scored)
    assert all(r["selection_score_v2"] is not None for r in scored)
    assert all(r["score_confidence"] is not None and r["score_confidence"] > 0 for r in scored)
