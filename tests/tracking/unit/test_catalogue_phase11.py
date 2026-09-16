"""Phase 11 family derivation and target-page actionability tests."""

from __future__ import annotations

from datetime import date

from data_sources.tracking.catalogue.builder import build_keyword_catalogue
from data_sources.tracking.catalogue.classify import classify_build
from data_sources.tracking.catalogue.families import (
    choose_family_primary,
    derive_families_for_build,
    family_signature,
    unsafe_merge_pairs,
)
from data_sources.tracking.catalogue.policy import CatalogueSelectionPolicy
from data_sources.tracking.catalogue.target_pages import (
    evaluate_target_page,
    evaluate_targets_for_build,
    ga4_confidence_multiplier_for_counts,
)
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    EligibilityStatus,
    FamilyRole,
    ProposedAction,
    RunStatus,
    RunType,
    TargetPageStatus,
)
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _config(tmp_path) -> TrackingConfig:
    policy = CatalogueSelectionPolicy()
    return TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase11.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_selection_policy=policy,
        catalogue_relevance_terms=list(policy.flat_relevance_terms()),
        catalogue_selected_limit=55,
        catalogue_min_impressions=10,
        catalogue_min_clicks_protect=1,
    )


def test_family_signature_merges_safe_variants():
    policy = CatalogueSelectionPolicy()
    assert family_signature("nex shoes", policy) == family_signature("shoes nex", policy)
    assert family_signature("nex shoes shop", policy) == family_signature("shoes nex", policy)
    assert family_signature("shoe shop near me", policy) == family_signature(
        "shoe store near me", policy
    )
    assert family_signature("shoe shops near me", policy) == family_signature(
        "shoe store near me", policy
    )


def test_family_signature_keeps_unsafe_merges_separate():
    policy = CatalogueSelectionPolicy()
    for left, right in unsafe_merge_pairs():
        assert family_signature(left, policy) != family_signature(right, policy)


def test_choose_family_primary_prefers_natural_local_order():
    members = [
        {
            "candidate_id": "a",
            "normalized_keyword": "shoes nex",
            "eligibility_status": EligibilityStatus.ELIGIBLE.value,
            "target_actionability_score": 0.5,
            "evidence_confidence_score": 0.5,
            "gsc_clicks": 5,
            "gsc_impressions": 200,
        },
        {
            "candidate_id": "b",
            "normalized_keyword": "nex shoes",
            "eligibility_status": EligibilityStatus.ELIGIBLE.value,
            "target_actionability_score": 0.5,
            "evidence_confidence_score": 0.5,
            "gsc_clicks": 2,
            "gsc_impressions": 100,
        },
    ]
    primary = choose_family_primary(members, family_key=family_signature("nex shoes", CatalogueSelectionPolicy()))
    assert primary["normalized_keyword"] == "nex shoes"


def test_homepage_specific_intent_unresolved():
    policy = CatalogueSelectionPolicy()
    status, confidence, action, _ = evaluate_target_page(
        {
            "primary_observed_page": "https://sunnystep.com/",
            "proposed_target_page": "https://sunnystep.com/",
            "normalized_keyword": "comfortable walking shoes",
            "search_intent": "problem_solution",
            "multi_page_competition": 0,
            "page_type": "other",
            "gsc_clicks": 1,
            "gsc_weighted_position": 8.0,
        },
        policy=policy,
    )
    assert status == TargetPageStatus.HOMEPAGE_UNRESOLVED.value
    assert action == ProposedAction.CREATE_NEW_PAGE.value
    assert confidence < 0.5


def test_ga4_shared_discount_bands():
    assert ga4_confidence_multiplier_for_counts(
        family_count_on_page=1, is_homepage=False, ga4_match_status="matched"
    ) == 1.0
    assert ga4_confidence_multiplier_for_counts(
        family_count_on_page=3, is_homepage=False, ga4_match_status="matched"
    ) == 0.6
    assert ga4_confidence_multiplier_for_counts(
        family_count_on_page=9, is_homepage=False, ga4_match_status="matched"
    ) == 0.25
    assert ga4_confidence_multiplier_for_counts(
        family_count_on_page=1, is_homepage=True, ga4_match_status="matched"
    ) == 0.0
    assert (
        ga4_confidence_multiplier_for_counts(
            family_count_on_page=1, is_homepage=False, ga4_match_status="unmatched"
        )
        is None
    )


def test_derive_families_persists_and_marks_variants(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    run = RunLog(
        run_id="run-phase11",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 10),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    queries = [
        ("nex shoes", 2, 80),
        ("shoes nex", 4, 120),
        ("nex shoes shop", 1, 40),
        ("shoe shop near me", 1, 50),
        ("shoe store near me", 2, 60),
        ("walking shoes", 3, 70),
        ("best walking shoes for women", 1, 30),
        ("comfortable walking shoes", 2, 45),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run.run_id,
                as_of_date=run.as_of_date,
                date=run.as_of_date,
                query=query,
                page="https://sunnystep.com/collections/walking-shoes"
                if "walking" in query
                else "https://sunnystep.com/",
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=clicks / impr,
                position=6.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for query, clicks, impr in queries
        ]
    )
    build = build_keyword_catalogue(
        store,
        config,
        gsc_start=run.as_of_date,
        gsc_end=run.as_of_date,
        created_by="phase11-test",
    )
    classify_build(store, config, build_id=build["build_id"])
    report = derive_families_for_build(store, config, build_id=build["build_id"])
    assert report["family_count"] >= 3
    assert report["multi_member_family_count"] >= 2

    families = store.fetchall(
        "SELECT * FROM keyword_families WHERE build_id = ? ORDER BY family_key",
        (build["build_id"],),
    )
    assert families
    nex = [f for f in families if "nex" in f["family_key"]][0]
    assert nex["member_count"] >= 2
    assert nex["primary_candidate_id"]
    members = store.fetchall(
        "SELECT normalized_keyword, family_role, eligibility_status FROM keyword_candidates "
        "WHERE family_id = ? ORDER BY normalized_keyword",
        (nex["family_id"],),
    )
    roles = {m["normalized_keyword"]: m["family_role"] for m in members}
    assert roles.get("nex shoes") == FamilyRole.PRIMARY.value or FamilyRole.PRIMARY.value in roles.values()
    assert FamilyRole.VARIANT.value in roles.values()
    # walking shoes vs best walking shoes for women stay separate families
    walk_keys = {
        family_signature(r["normalized_keyword"], CatalogueSelectionPolicy())
        for r in store.fetchall(
            "SELECT normalized_keyword FROM keyword_candidates WHERE build_id = ? AND normalized_keyword LIKE '%walking%'",
            (build["build_id"],),
        )
    }
    assert family_signature("walking shoes", CatalogueSelectionPolicy()) in walk_keys
    assert (
        family_signature("best walking shoes for women", CatalogueSelectionPolicy()) in walk_keys
    )
    assert len(walk_keys) >= 2

    # Target statuses populated
    targets = store.fetchall(
        "SELECT target_page_status, proposed_action, ga4_confidence_multiplier FROM keyword_candidates WHERE build_id = ?",
        (build["build_id"],),
    )
    assert all(t["target_page_status"] for t in targets)
    homepage_rows = [
        t
        for t in store.fetchall(
            "SELECT normalized_keyword, target_page_status FROM keyword_candidates "
            "WHERE build_id = ? AND primary_observed_page LIKE '%sunnystep.com/'",
            (build["build_id"],),
        )
        if t["normalized_keyword"] == "comfortable walking shoes"
        or "nex" in t["normalized_keyword"]
    ]
    assert any(r["target_page_status"] == TargetPageStatus.HOMEPAGE_UNRESOLVED.value for r in homepage_rows)

    builds = store.fetchall(
        "SELECT family_report_json FROM catalogue_builds WHERE build_id = ?",
        (build["build_id"],),
    )
    assert builds[0]["family_report_json"]


def test_evaluate_targets_command_path(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    run = RunLog(
        run_id="run-phase11-targets",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 10),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run.run_id,
                as_of_date=run.as_of_date,
                date=run.as_of_date,
                query="slippers",
                page="https://sunnystep.com/collections/sandals",
                country="sgp",
                clicks=2,
                impressions=40,
                ctr=0.05,
                position=5.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
        ]
    )
    build = build_keyword_catalogue(
        store, config, gsc_start=run.as_of_date, gsc_end=run.as_of_date, created_by="t"
    )
    classify_build(store, config, build_id=build["build_id"])
    report = evaluate_targets_for_build(store, config, build_id=build["build_id"])
    assert report["candidate_count"] >= 1
    row = store.fetchall(
        "SELECT target_page_status, proposed_action, page_type FROM keyword_candidates WHERE build_id = ?",
        (build["build_id"],),
    )[0]
    assert row["target_page_status"] in {
        TargetPageStatus.OBSERVED_PAGE_SUITABLE.value,
        TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
    }
