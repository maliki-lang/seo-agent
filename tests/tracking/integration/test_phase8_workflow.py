"""Phase 8: clusters, AI questions, review, approve, activate, comparison."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from data_sources.tracking.catalogue import PROVISIONAL_CATALOGUE_VERSION
from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue.clusters import derive_clusters
from data_sources.tracking.catalogue.compare import compare_with_provisional
from data_sources.tracking.catalogue.questions import build_ai_questions
from data_sources.tracking.catalogue.workflow import (
    activate_catalogue,
    approve_catalogue,
    export_review,
    import_decisions,
)
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import RunStatus, RunType
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase8.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=10,
    )
    store = TrackingStore(config)
    store.migrate()
    sync_catalogues(store, config)
    return store, config


def _seed_and_build(store, config):
    store.insert_run(
        RunLog(
            run_id="gsc-p8",
            run_type=RunType.BACKFILL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    rows = []
    fixtures = [
        ("comfortable flats singapore", "https://sunnystep.com/collections/flats", 8, 120),
        ("walking shoes singapore", "https://sunnystep.com/collections/walking-shoes", 6, 90),
        ("office shoes that dont hurt", "https://sunnystep.com/blogs/news/office-shoes-that-dont-hurt", 4, 70),
        ("shoes for standing all day singapore", "https://sunnystep.com/blogs/news/shoes-for-standing-all-day-singapore", 3, 55),
        ("comfortable loafers singapore", "https://sunnystep.com/collections/loafers", 5, 80),
    ]
    for query, page, clicks, impr in fixtures:
        rows.append(
            GscDailyRow(
                run_id="gsc-p8",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 1),
                query=query,
                page=page,
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=clicks / impr,
                position=9.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
        )
    store.upsert_gsc(rows)
    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 1),
        gsc_end=date(2026, 9, 1),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=10),
    )
    return built


def test_phase8_end_to_end_review_approve_activate(tmp_path):
    store, config = _store(tmp_path)
    provisional_before = store.count("keyword_catalog")
    assert provisional_before >= 50

    built = _seed_and_build(store, config)
    build_id = built["build_id"]

    clusters = derive_clusters(store, build_id=build_id)
    assert clusters["clusters_created"] >= 2
    clustered = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_candidates WHERE build_id = ? AND cluster_id IS NOT NULL",
        (build_id,),
    )[0]["n"]
    assert clustered >= 2

    # Informational vs commercial separation exists when both page types present.
    intents = {
        r["primary_intent"]
        for r in store.fetchall("SELECT primary_intent FROM catalogue_clusters WHERE build_id = ?", (build_id,))
    }
    assert intents

    questions = build_ai_questions(
        store, config, keyword_build_id=build_id, count=20, status="draft"
    )
    q_build = questions["build_id"]
    assert questions["selected"] == 20
    assert store.count("ai_question_sources") >= 20
    # Every selected question has GSC lineage and is non-branded.
    for q in store.fetchall(
        "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = 'selected'",
        (q_build,),
    ):
        assert q["cluster_id"]
        assert "sunnystep" not in q["question"].lower()
        sources = store.fetchall(
            "SELECT COUNT(*) AS n FROM ai_question_sources WHERE question_candidate_id = ?",
            (q["question_candidate_id"],),
        )[0]["n"]
        assert sources >= 1

    review_path = tmp_path / "review.csv"
    exported = export_review(
        store, build_id=build_id, output=str(review_path), question_build_id=q_build
    )
    assert exported["rows"] > 20
    assert review_path.exists()

    imported = import_decisions(
        store, build_id=build_id, input_path=str(review_path), question_build_id=q_build
    )
    assert imported["updated"]["keyword"] >= 1
    assert imported["updated"]["ai_question"] == 20

    comparison = compare_with_provisional(
        store, config, keyword_build_id=build_id, question_build_id=q_build
    )
    assert comparison["rows"] >= 55
    assert sum(comparison["counts"].values()) >= 55

    approved = approve_catalogue(
        store,
        config,
        build_id=build_id,
        approved_by="Ting",
        question_build_id=q_build,
        keyword_minimum=1,
        question_count=20,
    )
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "Ting"

    activated = activate_catalogue(
        store,
        config,
        build_id=build_id,
        confirm=True,
        question_build_id=q_build,
        catalogue_version="evidence-v1-test",
    )
    assert activated["status"] == "activated"
    assert activated["activated_questions"] == 20
    assert activated["provisional_rows_still_queryable"] >= 50

    # Provisional remains queryable.
    provisional = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_catalog WHERE catalogue_version = ?",
        (PROVISIONAL_CATALOGUE_VERSION,),
    )[0]["n"]
    assert provisional >= 50

    evidence = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_catalog WHERE catalogue_version = ? AND active = 1",
        ("evidence-v1-test",),
    )[0]["n"]
    assert evidence >= 1

    # Idempotent rerun does not duplicate logical rows.
    again = activate_catalogue(
        store,
        config,
        build_id=build_id,
        confirm=True,
        question_build_id=q_build,
        catalogue_version="evidence-v1-test",
    )
    assert again["activated_keywords"] == evidence
    evidence_after = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_catalog WHERE catalogue_version = ?",
        ("evidence-v1-test",),
    )[0]["n"]
    assert evidence_after == evidence


def test_import_rejects_unknown_and_duplicate_ids(tmp_path):
    store, config = _store(tmp_path)
    built = _seed_and_build(store, config)
    derive_clusters(store, build_id=built["build_id"])
    questions = build_ai_questions(store, config, keyword_build_id=built["build_id"], count=20)
    path = tmp_path / "bad.csv"
    path.write_text(
        "row_type,id,decision,decision_reason\n"
        "keyword,not-a-real-id,selected,x\n",
        encoding="utf-8",
    )
    try:
        import_decisions(
            store,
            build_id=built["build_id"],
            input_path=str(path),
            question_build_id=questions["build_id"],
        )
        raised = False
    except Exception:
        raised = True
    assert raised

    good = tmp_path / "dup.csv"
    cand = store.fetchall(
        "SELECT candidate_id FROM keyword_candidates WHERE build_id = ? LIMIT 1",
        (built["build_id"],),
    )[0]["candidate_id"]
    good.write_text(
        "row_type,id,decision,decision_reason\n"
        f"keyword,{cand},selected,ok\n"
        f"keyword,{cand},selected,dup\n",
        encoding="utf-8",
    )
    try:
        import_decisions(
            store,
            build_id=built["build_id"],
            input_path=str(good),
            question_build_id=questions["build_id"],
        )
        dup_raised = False
    except Exception:
        dup_raised = True
    assert dup_raised
