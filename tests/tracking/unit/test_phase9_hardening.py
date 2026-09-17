"""Phase 9 catalogue quality checks, report parity, credentials, ChatGPT search."""

from datetime import date
from pathlib import Path

from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue.clusters import derive_clusters
from data_sources.tracking.catalogue.questions import build_ai_questions
from data_sources.tracking.catalogue.question_review import mark_questions_review_ready
from data_sources.tracking.catalogue.report import write_provenance_report
from data_sources.tracking.catalogue.workflow import approve_catalogue, export_review, import_decisions
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.checks.catalogue_checks import CatalogueQualitySuite
from data_sources.tracking.collectors.ai_visibility import AiVisibilityCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.credentials import materialize_google_credentials
from data_sources.tracking.enums import Engine, RunStatus, RunType
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _seed_build(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'p9.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=10,
    )
    store = TrackingStore(config)
    store.migrate()
    sync_catalogues(store, config)
    store.insert_run(
        RunLog(
            run_id="gsc-p9",
            run_type=RunType.BACKFILL,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="gsc-p9",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 1),
                query=query,
                page=page,
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=clicks / impr,
                position=8.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for query, page, clicks, impr in [
                ("comfortable flats singapore", "https://sunnystep.com/collections/flats", 5, 100),
                ("walking shoes singapore", "https://sunnystep.com/collections/walking-shoes", 6, 90),
                ("office shoes singapore", "https://sunnystep.com/blogs/news/office-shoes-that-dont-hurt", 3, 60),
                ("shoes for standing all day singapore", "https://sunnystep.com/blogs/news/shoes-for-standing-all-day-singapore", 4, 70),
                ("comfortable loafers singapore", "https://sunnystep.com/collections/loafers", 5, 80),
            ]
        ]
    )
    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 1),
        gsc_end=date(2026, 9, 1),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=10),
    )
    derive_clusters(store, build_id=built["build_id"])
    questions = build_ai_questions(store, config, keyword_build_id=built["build_id"], count=20)
    path = tmp_path / "review.csv"
    export_review(store, build_id=built["build_id"], output=str(path), question_build_id=questions["build_id"])
    import_decisions(
        store, build_id=built["build_id"], input_path=str(path), question_build_id=questions["build_id"]
    )
    mark_questions_review_ready(
        store, question_build_id=questions["build_id"], reviewed_by="Ting"
    )
    approve_catalogue(
        store,
        config,
        build_id=built["build_id"],
        approved_by="Ting",
        question_build_id=questions["build_id"],
        keyword_minimum=1,
        question_count=20,
    )
    return store, config, built["build_id"], questions["build_id"]


def test_catalogue_quality_and_report_parity(tmp_path):
    store, config, build_id, q_build = _seed_build(tmp_path)
    report = write_provenance_report(
        store,
        config,
        build_id=build_id,
        question_build_id=q_build,
        output=str(tmp_path / "catalogue-provenance-v1.md"),
    )
    assert Path(report["output_markdown"]).exists()
    assert Path(report["output_json"]).exists()
    assert "limitations" in report
    assert report["selected_questions"] == 20

    summary = CatalogueQualitySuite(config, store).run_for_build(
        build_id,
        question_build_id=q_build,
        report_counts=report["counts_for_parity"],
    )
    assert summary["build_id"] == build_id
    # Critical lineage/decision checks should pass for this seeded build.
    names = {c["check_name"]: c["status"] for c in summary["checks"]}
    assert names["gsc_source_lineage"] == "pass"
    assert names["keyword_decision_completeness"] == "pass"
    assert names["ai_question_count"] == "pass"
    assert names["provenance_report_parity"] == "pass"


def test_materialize_google_credentials(tmp_path, monkeypatch):
    payload = '{"type":"service_account","project_id":"x"}'
    monkeypatch.setenv("GSC_CREDENTIALS_JSON", payload)
    monkeypatch.delenv("GA4_CREDENTIALS_JSON", raising=False)
    with materialize_google_credentials() as overrides:
        assert "GSC_CREDENTIALS_PATH" in overrides
        path = Path(overrides["GSC_CREDENTIALS_PATH"])
        assert path.exists()
        assert path.read_text(encoding="utf-8") == payload
        assert oct(path.stat().st_mode)[-3:] == "600"
    assert not path.exists()


def test_chatgpt_search_unsupported_not_silent_success():
    config = TrackingConfig(openai_api_key="test-key")

    class _Resp:
        def __init__(self, status_code, text="", payload=None):
            self.status_code = status_code
            self.text = text
            self.ok = status_code < 400
            self._payload = payload or {}

        def json(self):
            return self._payload

    import data_sources.tracking.collectors.ai_visibility as mod

    def fake_post(url, headers=None, json=None, timeout=None):
        # Reject web_search tool.
        if json and json.get("tools"):
            return _Resp(400, text="tools unsupported")
        return _Resp(200, payload={"choices": [{"message": {"content": "should not be used"}}]})

    original = mod.requests.post
    mod.requests.post = fake_post
    try:
        collector = AiVisibilityCollector(config, engines=[Engine.CHATGPT], repetitions=1, questions=[])
        # Call private completer directly.
        out = collector._complete_openai("What are comfortable shoes?")
        assert out["search_enabled"] is False
        assert out["search_capability"] == "unsupported"
        assert out["text"] == ""
    finally:
        mod.requests.post = original


def test_production_doctor_fails_on_missing_integrations(tmp_path):
    config = TrackingConfig(
        env="production",
        storage_url=f"sqlite:///{tmp_path / 'doc.db'}",
        gsc_property="",
        gsc_credentials_path="",
        ga4_property_id="",
        ga4_credentials_path="",
    )
    runner = TrackingRunner(config)
    payload = runner.doctor(command="doctor")
    assert payload["ok"] is False
    assert payload["issues"]
