"""Integration: Phase 5 schema migrates cleanly into Phase 6 catalogue builds."""

from datetime import date
from typing import Tuple

from data_sources.tracking.catalogue.builder import (
    CatalogueThresholds,
    build_keyword_catalogue,
    get_candidate_lineage,
)
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.catalogue import PROVISIONAL_CATALOGUE_VERSION
from data_sources.tracking.cli import main
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import RunStatus, RunType
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path) -> Tuple[TrackingStore, TrackingConfig]:
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase6.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=5,
    )
    store = TrackingStore(config)
    applied = store.migrate()
    assert "001_initial" in applied or store.count("gsc_daily") == 0
    # Second migrate is idempotent.
    assert store.migrate() == []
    return store, config


def test_phase5_to_phase6_migrate_build_and_lineage(tmp_path):
    store, config = _store(tmp_path)
    sync_catalogues(store, config)
    keywords = store.fetchall(
        "SELECT catalogue_version, approval_status FROM keyword_catalog LIMIT 1"
    )
    assert keywords[0]["catalogue_version"] == PROVISIONAL_CATALOGUE_VERSION
    assert keywords[0]["approval_status"] == "provisional"
    assert store.count("keyword_catalog") >= 50
    assert store.count("ai_question_catalog") >= 20

    store.insert_run(
        RunLog(
            run_id="prod-gsc",
            run_type=RunType.BACKFILL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="prod-gsc",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 1),
                query="walking shoes singapore",
                page="https://sunnystep.com/collections/walking-shoes",
                country="sgp",
                clicks=3,
                impressions=60,
                ctr=0.05,
                position=9.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            ),
            GscDailyRow(
                run_id="prod-gsc",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 2),
                query="walking shoes singapore",
                page="https://sunnystep.com/collections/walking-shoes",
                country="sgp",
                clicks=7,
                impressions=140,
                ctr=0.05,
                position=7.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            ),
            GscDailyRow(
                run_id="prod-gsc",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 2),
                query="office shoes that dont hurt",
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

    payload = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 1),
        gsc_end=date(2026, 9, 2),
        ga4_start=date(2026, 8, 1),
        ga4_end=date(2026, 8, 31),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=5),
    )
    assert payload["status"] == "draft"
    assert payload["funnel"]["raw_gsc_rows"] == 3
    assert payload["funnel"]["normalized_candidates"] == 2
    assert payload["inserted"]["sources"] == 3
    assert "prod-gsc" in payload["gsc_source_run_ids"]
    assert payload["ga4_window_start"] == "2026-08-01"

    walking = store.fetchall(
        """
        SELECT candidate_id, gsc_clicks, gsc_impressions, gsc_weighted_position, decision
        FROM keyword_candidates
        WHERE build_id = ? AND normalized_keyword = ?
        """,
        (payload["build_id"], "walking shoes singapore"),
    )[0]
    assert walking["gsc_clicks"] == 10
    assert walking["gsc_impressions"] == 200
    expected_pos = (9.0 * 60 + 7.0 * 140) / 200
    assert abs(float(walking["gsc_weighted_position"]) - expected_pos) < 1e-9
    assert walking["decision"] == "selected"

    lineage = get_candidate_lineage(store, walking["candidate_id"])
    assert len(lineage["sources"]) == 2
    assert {s["gsc_natural_key"] for s in lineage["sources"]}
    assert lineage["build"]["methodology_version"] == payload["methodology_version"]
    assert sum(int(s["impressions"]) for s in lineage["sources"]) == 200

    # candidate-v0.1 remains queryable after build
    assert store.count("keyword_catalog") >= 50


def test_cli_catalogue_build_keywords_json(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cli.db"
    config = TrackingConfig(
        storage_url=f"sqlite:///{db}",
        brand_terms_path="config/brand_terms.txt",
    )
    store = TrackingStore(config)
    store.migrate()
    store.insert_run(
        RunLog(
            run_id="cli-gsc",
            run_type=RunType.MANUAL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="cli-gsc",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 5),
                query="comfortable flats singapore",
                page="https://sunnystep.com/collections/flats",
                country="sgp",
                clicks=1,
                impressions=25,
                ctr=0.04,
                position=11.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
        ]
    )

    # Point CLI at temp DB via env override used by load_config.
    monkeypatch.setenv("TRACKING_DATABASE_URL", f"sqlite:///{db}")
    code = main(
        [
            "catalogue",
            "build-keywords",
            "--gsc-start-date",
            "2026-09-05",
            "--gsc-end-date",
            "2026-09-05",
            "--status",
            "draft",
            "--json",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "build_id" in out
    assert "funnel" in out
