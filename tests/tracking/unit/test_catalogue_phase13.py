"""Phase 13 portfolio selection and quality-gate tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.builder import build_keyword_catalogue
from data_sources.tracking.catalogue.classify import classify_build
from data_sources.tracking.catalogue.families import derive_families_for_build
from data_sources.tracking.catalogue.policy import CatalogueSelectionPolicy
from data_sources.tracking.catalogue.preselection import preselect_serp_pool
from data_sources.tracking.catalogue.selection import select_portfolio
from data_sources.tracking.catalogue.selection_helpers import (
    incremental_coverage_for_candidate,
    is_broad_head_term,
)
from data_sources.tracking.catalogue.workflow import export_review
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import CandidateDecision, RunStatus, RunType
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _policy(**overrides) -> CatalogueSelectionPolicy:
    base = {
        "selected_limit": 10,
        "alternate_limit": 3,
        "serper_preselection_limit": 20,
        "lane_quotas": {
            "need_state": 3,
            "product_category": 2,
            "use_case_audience": 1,
            "local_store": 1,
            "commercial_discovery": 1,
            "competitor_discovery": 1,
            "strategic_gap": 1,
        },
        "lane_caps": {
            "competitor_discovery": 1,
            "local_store": 1,
            "broad_head_term": 2,
        },
    }
    base.update(overrides)
    return CatalogueSelectionPolicy(**base)


def _config(tmp_path, policy: CatalogueSelectionPolicy) -> TrackingConfig:
    return TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase13.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_selection_policy=policy,
        catalogue_relevance_terms=list(policy.flat_relevance_terms()),
        catalogue_selected_limit=policy.selected_limit,
        catalogue_serper_preselection_limit=policy.serper_preselection_limit,
        catalogue_min_impressions=10,
        catalogue_min_clicks_protect=1,
        daily_cost_cap_usd=Decimal("25"),
    )


def _seed_build(store: TrackingStore, config: TrackingConfig) -> str:
    run = RunLog(
        run_id="run-p13",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 10),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    deep = "https://sunnystep.com/collections/walking-shoes"
    home = "https://sunnystep.com/"
    queries = [
        ("comfortable walking shoes", 4, 80, deep),
        ("best work shoes with arch support", 3, 70, deep),
        ("arch support sandals", 2, 55, deep),
        ("slippers", 2, 40, deep),
        ("mules singapore", 2, 45, deep),
        ("office shoes for standing", 2, 50, deep),
        ("shoe shop near me", 3, 90, home),
        ("birkenstock singapore", 2, 60, deep),
        ("plantar fasciitis shoes", 1, 35, deep),
        ("comfortable shoes for swollen feet", 2, 48, deep),
        ("walking shoes for women", 2, 52, deep),
        ("sandals for wide feet", 1, 30, deep),
        ("best shoes for walking", 1, 33, deep),
        ("nex shoes", 2, 40, deep),
        ("shoes nex", 1, 25, deep),
        ("sunny shoes", 5, 100, home),
        ("walkathon singapore", 1, 20, home),
        ("kids shoes singapore", 1, 22, deep),
        ("loafers for work", 1, 28, deep),
        ("cushioned walking sandals", 1, 27, deep),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run.run_id,
                as_of_date=run.as_of_date,
                date=run.as_of_date,
                query=query,
                page=page,
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=clicks / impr,
                position=7.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for query, clicks, impr, page in queries
        ]
    )
    build = build_keyword_catalogue(
        store, config, gsc_start=run.as_of_date, gsc_end=run.as_of_date, created_by="p13"
    )
    classify_build(store, config, build_id=build["build_id"])
    derive_families_for_build(store, config, build_id=build["build_id"])
    preselect_serp_pool(store, config, build_id=build["build_id"], limit=15)
    return build["build_id"]


def test_broad_head_and_incremental_coverage():
    policy = CatalogueSelectionPolicy()
    assert is_broad_head_term({"normalized_keyword": "slippers"}, policy)
    assert is_broad_head_term({"normalized_keyword": "mules singapore"}, policy)
    assert not is_broad_head_term({"normalized_keyword": "comfortable walking shoes"}, policy)

    covered = set()
    first = incremental_coverage_for_candidate(
        {"normalized_keyword": "arch support sandals", "search_intent": "transactional_category", "strategic_lane": "need_state"},
        policy=policy,
        covered=covered,
    )
    covered |= {"product:sandal", "product:sandals", "need:arch", "need:arch support", "intent:transactional_category", "lane:need_state"}
    second = incremental_coverage_for_candidate(
        {"normalized_keyword": "arch support sandals", "search_intent": "transactional_category", "strategic_lane": "need_state"},
        policy=policy,
        covered=covered,
    )
    assert first > second


def test_select_portfolio_quotas_caps_and_no_family_dupes(tmp_path):
    policy = _policy()
    config = _config(tmp_path, policy)
    store = TrackingStore(config)
    store.migrate()
    build_id = _seed_build(store, config)

    report = select_portfolio(store, config, build_id=build_id)
    assert report["methodology_version"] == "catalogue_selection_v2"
    assert report["selected_count"] <= policy.selected_limit
    assert report["alternate_count"] <= policy.alternate_limit
    assert report["lane_counts"].get("local_store", 0) <= policy.lane_caps["local_store"]
    assert report["lane_counts"].get("competitor_discovery", 0) <= policy.lane_caps["competitor_discovery"]
    assert report["broad_head_term_count"] <= policy.lane_caps["broad_head_term"]

    selected = store.fetchall(
        "SELECT * FROM keyword_candidates WHERE build_id = ? AND decision = ?",
        (build_id, CandidateDecision.SELECTED.value),
    )
    assert all(r["family_role"] == "primary" for r in selected)
    fams = [r["family_id"] for r in selected if r["family_id"]]
    assert len(fams) == len(set(fams))
    assert all((r["brand_status"] or "") not in {"branded", "ambiguous_brand"} for r in selected)
    assert all(r["portfolio_slot"] is not None for r in selected)

    # Ambiguous / location-only must not be selected.
    texts = {r["normalized_keyword"] for r in selected}
    assert "sunny shoes" not in texts
    assert "walkathon singapore" not in texts

    # Deterministic replay
    report2 = select_portfolio(store, config, build_id=build_id)
    assert report2["selected_candidate_ids"] == report["selected_candidate_ids"]
    assert report2["alternate_candidate_ids"] == report["alternate_candidate_ids"]


def test_export_review_includes_v2_fields(tmp_path):
    policy = _policy()
    config = _config(tmp_path, policy)
    store = TrackingStore(config)
    store.migrate()
    build_id = _seed_build(store, config)
    select_portfolio(store, config, build_id=build_id)
    out = tmp_path / "review.csv"
    payload = export_review(store, build_id=build_id, output=str(out))
    assert payload["rows"] > 0
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "portfolio_slot" in header
    assert "selection_score_v2" in header
    assert "review_group" in header
    assert "strategic_lane" in header
