from datetime import date
from decimal import Decimal

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    AIOverviewStatus,
    ChannelClass,
    Engine,
    RunStatus,
    RunType,
)
from data_sources.tracking.models import (
    AiAnswerRow,
    Ga4DailyRow,
    GscDailyRow,
    RunLog,
    SerpDailyRow,
)
from data_sources.tracking.reports.weekly import WeeklyReportService
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.sinks.alerts import AlertService
from data_sources.tracking.sinks.lark_base import LarkBaseSink
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso
from data_sources.tracking.enums import Severity


def _store(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'p5.db'}",
        lark_alert_webhook_url="https://example.test/hook",
        lark_base_app_token="basetoken",
        lark_weekly_table_id="tblWeekly",
        lark_opportunities_table_id="tblOpp",
        lark_app_id="app",
        lark_app_secret="secret",
    )
    store = TrackingStore(config)
    store.migrate()
    return store, config


def _seed(store, run_id="seed-run"):
    store.insert_run(
        RunLog(
            run_id=run_id,
            run_type=RunType.DAILY,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
            requested_collectors=["gsc", "ga4", "serper", "ai_visibility"],
            completed_collectors=["gsc", "ga4", "serper", "ai_visibility"],
            cost_usd=Decimal("0.02"),
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 14),
                query="walking shoes singapore",
                page="https://sunnystep.com/collections/walking-shoes",
                country="sgp",
                clicks=4,
                impressions=80,
                ctr=0.05,
                position=11.0,
                is_brand=False,
                brand_rule_version="v1",
            )
        ]
    )
    store.upsert_ga4(
        [
            Ga4DailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 14),
                session_source="google",
                session_medium="organic",
                landing_page="/",
                channel_class=ChannelClass.ORGANIC_SEARCH,
                sessions=12,
                engaged_sessions=9,
                purchases=0,
                total_revenue=Decimal("0"),
            )
        ]
    )
    store.upsert_serp(
        [
            SerpDailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 15),
                keyword_id="k004",
                keyword="walking shoes singapore",
                cluster="comfortable shoes (discovery)",
                target_page="https://sunnystep.com/collections/walking-shoes",
                country="sgp",
                device="all",
                sunnystep_position=11,
                result_count_inspected=10,
                top_10_domains=["anothersole.com"],
                ai_overview_status=AIOverviewStatus.UNVERIFIED,
                ai_overview_citations=[],
            )
        ]
    )
    rows = []
    for engine in (Engine.CHATGPT, Engine.PERPLEXITY):
        for rep in (1, 2, 3):
            rows.append(
                AiAnswerRow(
                    run_id=run_id,
                    as_of_date=date(2026, 9, 15),
                    engine=engine,
                    question_id="q001",
                    question="What are the most comfortable shoes for walking around Singapore?",
                    repetition_number=rep,
                    raw_answer="No brand.",
                    mentioned_sunnystep=False,
                    cited_urls=["https://example.com"],
                    named_competitors=[],
                    target_cluster="comfortable shoes (discovery)",
                    target_page="https://sunnystep.com/blogs/news/most-comfortable-shoes-singapore",
                    api_cost_usd=Decimal("0.01"),
                    latency_ms=5,
                    model="fixture",
                    search_enabled=False,
                    parser_version="ai_parser_v1",
                    raw_record_id=f"raw-{engine.value}-{rep}",
                )
            )
    store.upsert_ai_answers(rows)


def test_alert_deduplicates_same_run_and_type(tmp_path):
    store, config = _store(tmp_path)
    posts = []

    def post(url, payload, headers):
        posts.append({"url": url, "payload": payload})
        return {"message_id": "m1"}

    alerts = AlertService(config, store, post_fn=post)
    first = alerts.emit(
        run_id="r1",
        alert_type="collector_failure:serper:AuthenticationError",
        severity=Severity.CRITICAL,
        summary="auth failed",
    )
    second = alerts.emit(
        run_id="r1",
        alert_type="collector_failure:serper:AuthenticationError",
        severity=Severity.CRITICAL,
        summary="auth failed again",
    )
    assert first["deduplicated"] is False
    assert first["status"] == "sent"
    assert second["deduplicated"] is True
    assert len(posts) == 1
    assert store.count("alerts") == 1


def test_lark_weekly_upsert_uses_external_key(tmp_path):
    store, config = _store(tmp_path)
    calls = []

    def post(url, payload, headers):
        calls.append({"url": url, "payload": payload, "headers": headers})
        if "tenant_access_token" in url:
            return {"tenant_access_token": "t-test"}
        return {"data": {"record": {"record_id": "rec123"}}}

    sink = LarkBaseSink(config, post_fn=post, token_fn=lambda: "t-test")
    result = sink.upsert_weekly_summary(
        {
            "report_id": "weekly-2026-09-09-to-2026-09-15",
            "period_start": "2026-09-09",
            "period_end": "2026-09-15",
            "quality_status": "succeeded",
            "opportunity_count": 2,
            "executive_summary": "Fixture summary",
            "baseline_id": "",
        }
    )
    assert result["lark_record_id"] == "rec123"
    assert calls[0]["payload"]["client_token"] == "weekly-2026-09-09-to-2026-09-15"
    assert "Authorization" in calls[0]["headers"]


def test_weekly_report_draft_and_publish(tmp_path):
    store, config = _store(tmp_path)
    _seed(store)

    def post(url, payload, headers):
        return {"data": {"record": {"record_id": "rec-weekly"}}, "message_id": "m"}

    sink = LarkBaseSink(config, post_fn=post, token_fn=lambda: "t")
    alerts = AlertService(config, store, post_fn=post)
    service = WeeklyReportService(config, store, lark_sink=sink, alert_service=alerts)
    draft = service.generate(period_end=date(2026, 9, 15), publish=False)
    assert draft["status"] == "draft"
    assert store.count("weekly_reports") == 1
    assert "executive_summary" in draft["summary"]
    assert len(draft["summary"]["top_actions"]) <= 10

    published = service.generate(period_end=date(2026, 9, 15), publish=True)
    assert published["status"] == "published"
    assert published["lark_record_id"] == "rec-weekly"
    assert store.count("weekly_reports") == 1


def test_simulate_failure_emits_alert(tmp_path):
    store, config = _store(tmp_path)
    posts = []

    def post(url, payload, headers):
        posts.append(payload)
        return {"message_id": "m2"}

    runner = TrackingRunner(config, store=store)
    runner.alert_service = AlertService(config, store, post_fn=post)
    result = runner.run_daily(
        date(2026, 9, 15),
        sources=["serper"],
        simulate_failure="serper:authentication",
    )
    assert result["status"] == RunStatus.FAILED.value
    assert result["failed_collectors"]["serper"] == "AuthenticationError"
    assert store.count("alerts") == 1
    assert posts
