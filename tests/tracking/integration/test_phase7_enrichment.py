"""Integration tests for Phase 7 GA4 enrichment and Serper validation."""

from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue.enrich import enrich_build_with_ga4
from data_sources.tracking.catalogue.validate_serp import validate_serp_for_build
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import ChannelClass, RunStatus, RunType
from data_sources.tracking.models import Ga4DailyRow, GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase7.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=5,
        daily_cost_cap_usd=Decimal("25"),
    )
    store = TrackingStore(config)
    store.migrate()
    return store, config


def _seed(store: TrackingStore) -> None:
    store.insert_run(
        RunLog(
            run_id="gsc-1",
            run_type=RunType.BACKFILL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    store.insert_run(
        RunLog(
            run_id="ga4-1",
            run_type=RunType.BACKFILL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="gsc-1",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 1),
                query="comfortable flats singapore",
                page="https://sunnystep.com/collections/flats",
                country="sgp",
                clicks=5,
                impressions=100,
                ctr=0.05,
                position=8.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            ),
            GscDailyRow(
                run_id="gsc-1",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 1),
                query="office shoes singapore",
                page="https://sunnystep.com/blogs/news/office-shoes-that-dont-hurt",
                country="sgp",
                clicks=2,
                impressions=40,
                ctr=0.05,
                position=12.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            ),
        ]
    )
    store.upsert_ga4(
        [
            Ga4DailyRow(
                run_id="ga4-1",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 2),
                session_source="google",
                session_medium="organic",
                landing_page="/collections/flats",
                channel_class=ChannelClass.ORGANIC_SEARCH,
                sessions=20,
                engaged_sessions=14,
                purchases=1,
                total_revenue=Decimal("49.00"),
            ),
            # Unmatched page intentionally omitted for the office-shoes article.
        ]
    )


def test_ga4_enrichment_matched_and_unmatched(tmp_path):
    store, config = _store(tmp_path)
    _seed(store)
    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 1),
        gsc_end=date(2026, 9, 1),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=5),
    )
    enriched = enrich_build_with_ga4(
        store,
        config,
        build_id=built["build_id"],
        ga4_start=date(2026, 9, 2),
        ga4_end=date(2026, 9, 2),
    )
    report = enriched["match_report"]
    assert report["eligible_pages"] == 2
    assert report["matched_pages"] == 1
    assert report["unmatched_pages"] == 1
    assert report["matched_pages"] + report["unmatched_pages"] == report["eligible_pages"]

    flats = store.fetchall(
        """
        SELECT ga4_match_status, ga4_organic_sessions, ga4_purchases, ga4_revenue,
               ga4_value_score, page_type
        FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (built["build_id"], "comfortable flats singapore"),
    )[0]
    assert flats["ga4_match_status"] == "matched"
    assert flats["ga4_organic_sessions"] == 20
    assert flats["ga4_purchases"] == 1
    assert flats["page_type"] == "collection"
    assert flats["ga4_value_score"] is not None

    article = store.fetchall(
        """
        SELECT ga4_match_status, ga4_organic_sessions, ga4_value_score, page_type
        FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (built["build_id"], "office shoes singapore"),
    )[0]
    assert article["ga4_match_status"] == "unmatched"
    assert article["ga4_organic_sessions"] is None
    assert article["ga4_value_score"] is None
    assert article["page_type"] == "article"


def test_serper_validation_fake_http_boundary(tmp_path):
    store, config = _store(tmp_path)
    _seed(store)
    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 1),
        gsc_end=date(2026, 9, 1),
        ga4_start=date(2026, 9, 2),
        ga4_end=date(2026, 9, 2),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=5),
    )

    def fake_search(keyword: str):
        return {
            "organic": [
                {"position": 1, "link": "https://example.com/a", "title": "Other"},
                {
                    "position": 4,
                    "link": "https://sunnystep.com/collections/flats",
                    "title": "Flats",
                },
            ],
            "aiOverview": {},
        }

    result = validate_serp_for_build(
        store,
        config,
        build_id=built["build_id"],
        decision="selected",
        search_fn=fake_search,
    )
    assert result["validated"] >= 1
    assert result["failed"] == 0
    assert result["report"]["coverage_ok"] is True

    row = store.fetchall(
        """
        SELECT serper_position, serper_ranking_url, proposed_target_ranks,
               serper_validation_score, serper_run_id, serper_ai_overview_status
        FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (built["build_id"], "comfortable flats singapore"),
    )[0]
    assert row["serper_position"] == 4
    assert "sunnystep.com/collections/flats" in (row["serper_ranking_url"] or "")
    assert row["proposed_target_ranks"] == 1
    assert row["serper_validation_score"] is not None
    assert row["serper_run_id"]
    assert row["serper_ai_overview_status"] == "absent"
