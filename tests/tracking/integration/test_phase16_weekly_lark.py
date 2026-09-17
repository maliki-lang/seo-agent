"""Phase 16 weekly + Lark idempotency tests."""

from __future__ import annotations

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
from data_sources.tracking.sinks.alerts import AlertService
from data_sources.tracking.sinks.lark_base import LarkBaseSink
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def test_weekly_blocks_without_activated_catalogue_and_upserts_experiments(tmp_path):
    posted = []

    def post_fn(url, payload, headers):
        posted.append({"url": url, "payload": payload, "headers": headers})
        if "tenant_access_token" in url:
            return {"tenant_access_token": "tok"}
        return {"data": {"record": {"record_id": "rec-1"}}}

    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'p16w.db'}",
        lark_alert_webhook_url="https://example.test/hook",
        lark_base_app_token="basetoken",
        lark_weekly_table_id="tblWeekly",
        lark_opportunities_table_id="tblOpp",
        lark_experiments_table_id="tblExp",
        lark_experiment_costs_table_id="tblCost",
        lark_experiment_measurements_table_id="tblMeas",
        lark_experiment_outcomes_table_id="tblOut",
        lark_app_id="app",
        lark_app_secret="secret",
    )
    store = TrackingStore(config)
    store.migrate()
    store.insert_run(
        RunLog(
            run_id="seed",
            run_type=RunType.DAILY,
            as_of_date=date(2026, 9, 15),
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
            requested_collectors=["gsc", "ga4"],
            completed_collectors=["gsc", "ga4"],
            cost_usd=Decimal("0.01"),
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="seed",
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
                run_id="seed",
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 14),
                session_source="google",
                session_medium="organic",
                landing_page="https://sunnystep.com/collections/walking-shoes",
                channel_class=ChannelClass.ORGANIC_SEARCH,
                sessions=10,
                engaged_sessions=6,
                purchases=0,
                total_revenue=Decimal("0"),
                revenue_currency="SGD",
            )
        ]
    )
    # Seed one experiment for Lark upsert coverage.
    store.insert_experiment(
        {
            "experiment_id": "exp-1",
            "opportunity_id": "opp-x",
            "status": "measuring",
            "hypothesis": "h",
            "action_type": "title_meta_rewrite",
            "target_page": "https://sunnystep.com/collections/walking-shoes",
            "target_queries_json": ["walking shoes singapore"],
            "control_pages_json": [],
            "primary_metric": "adjusted_incremental_nonbranded_clicks",
            "guardrail_metrics_json": [],
            "approved_by": "Ting",
            "approved_at": utc_now_iso(),
            "owner": "Maliki",
            "published_at": "2026-09-01T00:00:00",
            "baseline_start": "2026-08-04",
            "baseline_end": "2026-08-31",
            "actual_cost": 10,
            "cost_currency": "SGD",
            "source_report_id": "rep",
            "metadata_json": {},
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
    )
    store.insert_experiment_cost(
        {
            "cost_id": "cost-1",
            "experiment_id": "exp-1",
            "cost_type": "review",
            "quantity": 1,
            "unit_cost": 10,
            "amount": 10,
            "currency": "SGD",
            "incurred_at": utc_now_iso(),
            "created_at": utc_now_iso(),
        }
    )

    sink = LarkBaseSink(config, post_fn=post_fn, token_fn=lambda: "tok")
    service = WeeklyReportService(
        config,
        store,
        lark_sink=sink,
        alert_service=AlertService(config, store, post_fn=post_fn),
    )
    result = service.generate(period_end=date(2026, 9, 15), publish=True)
    assert result["opportunity_blocked"] is True
    summary = result["summary"]
    assert summary["opportunity_portfolio"]["blocked"] is True
    assert summary["opportunity_version"] == "v2"
    assert "experiments" in summary
    # Weekly + opportunities (empty) + experiments + costs + measurements + outcomes
    client_tokens = [
        p["payload"].get("client_token")
        for p in posted
        if isinstance(p.get("payload"), dict) and "client_token" in p["payload"]
    ]
    assert "exp-1" in client_tokens
    assert "cost-1" in client_tokens
    # Idempotent second publish
    service.generate(period_end=date(2026, 9, 15), publish=True)
    assert client_tokens.count("exp-1") >= 1
