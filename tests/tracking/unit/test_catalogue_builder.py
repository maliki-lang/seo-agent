"""Unit tests for Phase 6 catalogue normalization, scoring, and aggregation."""

from datetime import date

from data_sources.tracking.catalogue.scoring import (
    business_relevance_score,
    gsc_opportunity_score,
    score_candidate,
)
from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue import PROVISIONAL_CATALOGUE_VERSION
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import RunStatus, RunType, Source
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.reports.metrics_calc import impression_weighted_position, weighted_ctr
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import (
    catalogue_transformation_method,
    normalize_catalogue_query,
    utc_now_iso,
)


def test_normalize_catalogue_query_apostrophe_variants():
    assert normalize_catalogue_query("Women's Comfortable Shoes") == "womens comfortable shoes"
    assert normalize_catalogue_query("womens comfortable shoes") == "womens comfortable shoes"
    assert normalize_catalogue_query("  Comfortable   Shoes!! ") == "comfortable shoes"


def test_transformation_method_classification():
    assert catalogue_transformation_method("comfortable shoes", "comfortable shoes", merged=False) == "exact"
    assert (
        catalogue_transformation_method("Women's flats", "womens flats", merged=False) == "normalized"
    )
    assert (
        catalogue_transformation_method("women flats", "womens flats", merged=True) == "merged_variants"
    )


def test_weighted_gsc_metrics_never_average_row_ctr():
    rows = [
        {"impressions": 100, "position": 10.0, "clicks": 2},
        {"impressions": 300, "position": 4.0, "clicks": 30},
    ]
    assert weighted_ctr(32, 400) == 0.08
    assert impression_weighted_position(rows) == (10.0 * 100 + 4.0 * 300) / 400
    assert weighted_ctr(0, 0) is None


def test_scoring_components_deterministic():
    score = score_candidate(
        clicks=5,
        impressions=200,
        weighted_position=8.0,
        weighted_ctr=0.025,
        multi_page=True,
        normalized_keyword="comfortable walking shoes singapore",
        source_row_count=4,
        source_date_count=10,
        relevance_terms=("comfortable", "shoes", "walking", "singapore"),
        min_impressions=10,
    )
    assert 0 < score.gsc_opportunity_score <= 1
    assert score.business_relevance_score > 0
    assert score.evidence_confidence_score > 0
    assert score.final_selection_score > 0
    opp, reasons = gsc_opportunity_score(
        clicks=0,
        impressions=2,
        weighted_position=50.0,
        weighted_ctr=0.0,
        multi_page=False,
        min_impressions=10,
    )
    assert opp == 0.0
    assert "insufficient_evidence" in reasons
    assert business_relevance_score("random widgets", ("shoes",)) == 0.0


def _seed_gsc(store: TrackingStore, *, include_fixture: bool = True) -> str:
    run = RunLog(
        run_id="gsc-run-1",
        run_type=RunType.MANUAL,
        as_of_date=date(2026, 9, 15),
        started_at=utc_now_iso(),
        status=RunStatus.SUCCEEDED,
    )
    store.insert_run(run)
    rows = [
        GscDailyRow(
            run_id="gsc-run-1",
            as_of_date=date(2026, 9, 15),
            date=date(2026, 9, 10),
            query="comfortable shoes singapore",
            page="https://sunnystep.com/blogs/news/comfortable-shoes-singapore",
            country="sgp",
            clicks=4,
            impressions=80,
            ctr=0.05,
            position=8.0,
            is_brand=False,
            brand_rule_version="brand_rules_v1",
        ),
        GscDailyRow(
            run_id="gsc-run-1",
            as_of_date=date(2026, 9, 15),
            date=date(2026, 9, 11),
            query="Comfortable Shoes Singapore!",
            page="https://sunnystep.com/collections/walking-shoes",
            country="sgp",
            clicks=6,
            impressions=120,
            ctr=0.05,
            position=6.0,
            is_brand=False,
            brand_rule_version="brand_rules_v1",
        ),
        GscDailyRow(
            run_id="gsc-run-1",
            as_of_date=date(2026, 9, 15),
            date=date(2026, 9, 11),
            query="sunnystep shoes",
            page="https://sunnystep.com/",
            country="sgp",
            clicks=20,
            impressions=200,
            ctr=0.1,
            position=1.5,
            is_brand=True,
            brand_rule_version="brand_rules_v1",
        ),
        GscDailyRow(
            run_id="gsc-run-1",
            as_of_date=date(2026, 9, 15),
            date=date(2026, 9, 11),
            query="random insurance quote",
            page="https://sunnystep.com/",
            country="sgp",
            clicks=0,
            impressions=50,
            ctr=0.0,
            position=40.0,
            is_brand=False,
            brand_rule_version="brand_rules_v1",
        ),
    ]
    store.upsert_gsc(rows)
    if include_fixture:
        demo_run = RunLog(
            run_id="demo-run",
            run_type=RunType.DEMO,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
        store.insert_run(demo_run)
        fixture = GscDailyRow(
            run_id="demo-run",
            as_of_date=date(2026, 9, 15),
            date=date(2026, 9, 11),
            query="fixture only shoes",
            page="https://sunnystep.com/",
            country="sgp",
            clicks=99,
            impressions=999,
            ctr=0.1,
            position=1.0,
            is_brand=False,
            brand_rule_version="brand_rules_v1",
            source=Source.GSC,
        )
        # Force non-production source label after model default.
        store.upsert_gsc([fixture])
        store.execute(
            "UPDATE gsc_daily SET source = 'fixture' WHERE run_id = ?",
            ("demo-run",),
        )
    return "gsc-run-1"


def test_build_excludes_fixtures_and_creates_lineage(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'cat.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=10,
        catalogue_selected_limit=10,
    )
    store = TrackingStore(config)
    applied = store.migrate()
    assert "002_catalogue_provenance" in applied or store.count("catalogue_builds") == 0
    _seed_gsc(store)

    result = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 10),
        gsc_end=date(2026, 9, 11),
        status="draft",
        thresholds=CatalogueThresholds(min_impressions=10, selected_limit=10),
    )
    assert result["build_id"]
    assert result["funnel"]["raw_gsc_rows"] == 4  # fixture excluded
    assert result["funnel"]["excluded_fixture_or_non_production_rows"] >= 1
    assert store.count("keyword_candidates") >= 3
    assert store.count("keyword_candidate_sources") >= 4

    comfort = store.fetchall(
        """
        SELECT * FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (result["build_id"], "comfortable shoes singapore"),
    )
    assert len(comfort) == 1
    assert comfort[0]["gsc_clicks"] == 10
    assert comfort[0]["gsc_impressions"] == 200
    assert abs(float(comfort[0]["gsc_weighted_ctr"]) - 0.05) < 1e-9
    sources = store.fetchall(
        "SELECT * FROM keyword_candidate_sources WHERE candidate_id = ?",
        (comfort[0]["candidate_id"],),
    )
    assert len(sources) == 2
    assert {s["raw_query"] for s in sources} == {
        "comfortable shoes singapore",
        "Comfortable Shoes Singapore!",
    }

    branded = store.fetchall(
        """
        SELECT decision FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (result["build_id"], "sunnystep shoes"),
    )
    assert branded[0]["decision"] == "rejected"

    deferred = store.fetchall(
        """
        SELECT decision FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (result["build_id"], "random insurance quote"),
    )
    assert deferred[0]["decision"] == "deferred"

    # Provisional catalogue labeling columns exist with defaults.
    store.executemany(
        """
        INSERT INTO keyword_catalog(
            keyword_id, keyword, cluster, target_page, country, device, language,
            active, valid_from, valid_to, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "k-test",
                "test keyword",
                "cluster",
                "https://sunnystep.com/",
                "sgp",
                "all",
                "en",
                1,
                "2026-01-01",
                None,
                utc_now_iso(),
                utc_now_iso(),
            )
        ],
    )
    labeled = store.fetchall("SELECT catalogue_version, approval_status FROM keyword_catalog WHERE keyword_id = ?", ("k-test",))
    assert labeled[0]["catalogue_version"] == PROVISIONAL_CATALOGUE_VERSION
    assert labeled[0]["approval_status"] == "provisional"
