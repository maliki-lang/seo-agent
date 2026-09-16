"""Phase 10 catalogue selection policy and classification regression tests."""

from __future__ import annotations

import pytest

from data_sources.tracking.catalogue.classify import (
    classify_build,
    classify_query,
)
from data_sources.tracking.catalogue.policy import (
    CatalogueSelectionPolicy,
    policy_from_catalogue_raw,
)
from data_sources.tracking.config import TrackingConfig, load_config
from data_sources.tracking.enums import (
    BrandStatus,
    BusinessRelevanceStatus,
    CandidateDecision,
    CompetitorStatus,
    EligibilityStatus,
    RunStatus,
    RunType,
)
from data_sources.tracking.exceptions import ConfigurationError
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.ai_parser import competitor_catalogue_from_config
from data_sources.tracking.transforms.brand_label import BrandClassifier
from data_sources.tracking.transforms.normalize import utc_now_iso
from data_sources.tracking.catalogue.builder import build_keyword_catalogue


def _classifier_bundle():
    config = load_config()
    policy = config.catalogue_selection_policy
    assert isinstance(policy, CatalogueSelectionPolicy)
    brand = BrandClassifier.from_config(config)
    competitors = competitor_catalogue_from_config("config/competitors.yaml")
    return config, policy, brand, competitors


def test_policy_weights_and_quotas_validate():
    policy = CatalogueSelectionPolicy()
    policy.validate()
    assert abs(sum(policy.score_weights.values()) - 1.0) < 1e-9
    assert sum(policy.lane_quotas.values()) == policy.selected_limit
    assert "singapore" not in policy.flat_relevance_terms()


def test_policy_rejects_bad_weight_sum():
    with pytest.raises(ConfigurationError, match="score_weights"):
        policy_from_catalogue_raw(
            {
                "selected_limit": 55,
                "score_weights": {"intent_fit": 0.5, "product_need_relevance": 0.5},
                "lane_quotas": {
                    "need_state": 15,
                    "product_category": 12,
                    "use_case_audience": 8,
                    "local_store": 8,
                    "commercial_discovery": 5,
                    "competitor_discovery": 4,
                    "strategic_gap": 3,
                },
            }
        )


def test_policy_rejects_quota_mismatch():
    with pytest.raises(ConfigurationError, match="lane_quotas"):
        policy_from_catalogue_raw(
            {
                "selected_limit": 55,
                "lane_quotas": {"need_state": 10, "product_category": 10},
            }
        )


def test_load_config_exposes_selection_policy():
    config = load_config()
    assert config.catalogue_selected_limit == 55
    assert config.catalogue_serper_preselection_limit == 100
    assert config.catalogue_alternate_limit == 15
    assert config.catalogue_selection_policy.prohibited_location_only is True
    assert "slippers" in config.catalogue_selection_policy.product_terms
    assert "mules" in config.catalogue_selection_policy.product_terms


@pytest.mark.parametrize(
    "query,expected_status,match_type",
    [
        ("sunnystep", BrandStatus.BRANDED.value, "exact_configured_term"),
        ("sunny step", BrandStatus.BRANDED.value, "exact_configured_term"),
        ("sunnysteps", BrandStatus.BRANDED.value, "exact_configured_term"),
        ("sunny shoes", BrandStatus.AMBIGUOUS_BRAND.value, "fuzzy_suspect"),
        ("sunny feet shoes", BrandStatus.AMBIGUOUS_BRAND.value, "fuzzy_suspect"),
        ("sunnysteo", BrandStatus.AMBIGUOUS_BRAND.value, "fuzzy_suspect"),
        ("comfortable walking shoes", BrandStatus.NON_BRANDED.value, "no_match"),
    ],
)
def test_brand_three_way_regression(query, expected_status, match_type):
    _, policy, brand, competitors = _classifier_bundle()
    result = classify_query(
        query, policy=policy, brand_classifier=brand, competitor_catalogue=competitors
    )
    assert result.brand_status == expected_status
    assert result.brand_match_type == match_type
    if expected_status == BrandStatus.AMBIGUOUS_BRAND.value:
        assert result.eligibility_status == EligibilityStatus.INELIGIBLE_BRAND.value


@pytest.mark.parametrize(
    "query,relevant",
    [
        ("walkathon singapore", False),
        ("singapore", False),
        ("slippers", True),
        ("mules singapore", True),
        ("comfortable shoes", True),
        ("mao ting", False),
    ],
)
def test_location_only_and_product_relevance(query, relevant):
    _, policy, brand, competitors = _classifier_bundle()
    result = classify_query(
        query, policy=policy, brand_classifier=brand, competitor_catalogue=competitors
    )
    if relevant:
        assert result.business_relevance_status == BusinessRelevanceStatus.RELEVANT.value
        assert result.business_relevance_score > 0
    else:
        assert result.business_relevance_status in {
            BusinessRelevanceStatus.IRRELEVANT.value,
            BusinessRelevanceStatus.LOCATION_ONLY.value,
        }
        assert result.business_relevance_score == 0
        if "singapore" in query and query.strip() in {"walkathon singapore", "singapore"}:
            assert result.business_relevance_status == BusinessRelevanceStatus.LOCATION_ONLY.value


@pytest.mark.parametrize(
    "query,competitor_name",
    [
        ("birkenstock singapore", "Birkenstock"),
        ("scholl shoes singapore", "Scholl"),
        ("on cloud shoes singapore", "On"),
        ("another sole singapore", "Anothersole"),
        ("vivo shoes", "Vivo"),
    ],
)
def test_competitor_aliases(query, competitor_name):
    _, policy, brand, competitors = _classifier_bundle()
    result = classify_query(
        query, policy=policy, brand_classifier=brand, competitor_catalogue=competitors
    )
    assert result.competitor_status == CompetitorStatus.EXPLICIT.value
    assert result.competitor_name == competitor_name
    assert result.routing_bucket in {"competitor_benchmark", "ambiguous_brand", "irrelevant"}


def test_claims_review_flag():
    _, policy, brand, competitors = _classifier_bundle()
    result = classify_query(
        "plantar fasciitis shoes",
        policy=policy,
        brand_classifier=brand,
        competitor_catalogue=competitors,
    )
    assert result.claims_review_required is True
    assert result.eligibility_status == EligibilityStatus.ELIGIBLE_WITH_REVIEW.value


def test_classify_build_persists_fields_and_demotes(tmp_path):
    from datetime import date

    db = tmp_path / "phase10.db"
    policy = CatalogueSelectionPolicy()
    config = TrackingConfig(
        storage_url=f"sqlite:///{db}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_selection_policy=policy,
        catalogue_relevance_terms=list(policy.flat_relevance_terms()),
        catalogue_selected_limit=55,
        catalogue_min_impressions=10,
        catalogue_min_clicks_protect=1,
    )
    store = TrackingStore(config)
    store.migrate()
    run = RunLog(
        run_id="run-phase10",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 10),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    rows = [
        GscDailyRow(
            run_id=run.run_id,
            as_of_date=run.as_of_date,
            date=run.as_of_date,
            query=query,
            page="https://sunnystep.com/",
            country="sgp",
            clicks=clicks,
            impressions=impr,
            ctr=clicks / impr,
            position=5.0,
            is_brand=False,
            brand_rule_version="brand_rules_v1",
        )
        for query, clicks, impr in [
            ("sunny shoes", 5, 100),
            ("walkathon singapore", 3, 72),
            ("slippers", 2, 40),
            ("birkenstock singapore", 3, 50),
            ("comfortable walking shoes", 4, 80),
        ]
    ]
    store.upsert_gsc(rows)
    build = build_keyword_catalogue(
        store,
        config,
        gsc_start=run.as_of_date,
        gsc_end=run.as_of_date,
        created_by="phase10-test",
    )
    # Force sunny shoes into selected so demotion is observable.
    sunny = store.fetchall(
        "SELECT candidate_id FROM keyword_candidates WHERE build_id = ? AND normalized_keyword = ?",
        (build["build_id"], "sunny shoes"),
    )[0]
    store.update_keyword_candidate(
        sunny["candidate_id"],
        {"decision": CandidateDecision.SELECTED.value, "updated_at": utc_now_iso()},
    )

    report = classify_build(store, config, build_id=build["build_id"])
    assert report["candidate_count"] >= 5
    assert report["demoted_selected_to_pending"] >= 1

    by_kw = {
        r["normalized_keyword"]: r
        for r in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?", (build["build_id"],)
        )
    }
    assert by_kw["sunny shoes"]["brand_status"] == BrandStatus.AMBIGUOUS_BRAND.value
    assert by_kw["sunny shoes"]["decision"] == CandidateDecision.PENDING.value
    assert by_kw["walkathon singapore"]["business_relevance_status"] == BusinessRelevanceStatus.LOCATION_ONLY.value
    assert by_kw["slippers"]["business_relevance_status"] == BusinessRelevanceStatus.RELEVANT.value
    assert by_kw["birkenstock singapore"]["competitor_name"] == "Birkenstock"
    assert by_kw["comfortable walking shoes"]["eligibility_status"] == EligibilityStatus.ELIGIBLE.value

    builds = store.fetchall(
        "SELECT selection_policy_version, routing_report_json FROM catalogue_builds WHERE build_id = ?",
        (build["build_id"],),
    )
    assert builds[0]["selection_policy_version"] == "selection_policy_v2"
    assert builds[0]["routing_report_json"]
