from datetime import date
from decimal import Decimal

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    BaselineStatus,
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
from data_sources.tracking.enums import AIOverviewStatus, Source
from data_sources.tracking.reports.baseline import BaselineService
from data_sources.tracking.reports.opportunities import OpportunityBuilder
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path):
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 'p4.db'}")
    store = TrackingStore(config)
    store.migrate()
    return store, config


def _seed(store: TrackingStore, run_id: str):
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
            cost_usd=Decimal("0.05"),
        )
    )
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 10),
                query="walking shoes singapore",
                page="https://sunnystep.com/collections/walking-shoes",
                country="sgp",
                clicks=5,
                impressions=100,
                ctr=0.05,
                position=12.0,
                is_brand=False,
                brand_rule_version="v1",
            ),
            GscDailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 10),
                query="sunnystep shoes",
                page="https://sunnystep.com/",
                country="sgp",
                clicks=8,
                impressions=40,
                ctr=0.2,
                position=2.0,
                is_brand=True,
                brand_rule_version="v1",
            ),
        ]
    )
    store.upsert_ga4(
        [
            Ga4DailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 10),
                session_source="google",
                session_medium="organic",
                landing_page="/",
                channel_class=ChannelClass.ORGANIC_SEARCH,
                sessions=20,
                engaged_sessions=15,
                purchases=1,
                total_revenue=Decimal("49.00"),
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
                sunnystep_position=12,
                result_count_inspected=10,
                top_10_domains=["anothersole.com"],
                ai_overview_status=AIOverviewStatus.UNVERIFIED,
                ai_overview_citations=[],
            ),
            SerpDailyRow(
                run_id=run_id,
                as_of_date=date(2026, 9, 15),
                date=date(2026, 9, 15),
                keyword_id="k099",
                keyword="absent keyword singapore",
                cluster="comfortable shoes (discovery)",
                target_page="https://sunnystep.com/blogs/news/comfortable-shoes-singapore",
                country="sgp",
                device="all",
                sunnystep_position=0,
                result_count_inspected=10,
                top_10_domains=["anothersole.com"],
                ai_overview_status=AIOverviewStatus.ABSENT,
                ai_overview_citations=[],
            ),
        ]
    )
    ai_rows = []
    for engine in (Engine.CHATGPT, Engine.PERPLEXITY):
        for rep in (1, 2, 3):
            ai_rows.append(
                AiAnswerRow(
                    run_id=run_id,
                    as_of_date=date(2026, 9, 15),
                    engine=engine,
                    question_id="q001",
                    question="What are the most comfortable shoes for walking around Singapore?",
                    repetition_number=rep,
                    raw_answer="No brand mentioned.",
                    mentioned_sunnystep=False,
                    cited_urls=["https://example.com"],
                    named_competitors=[],
                    target_cluster="comfortable shoes (discovery)",
                    target_page="https://sunnystep.com/blogs/news/most-comfortable-shoes-singapore",
                    api_cost_usd=Decimal("0.01"),
                    latency_ms=10,
                    model="fixture",
                    search_enabled=False,
                    parser_version="ai_parser_v1",
                    raw_record_id=f"raw-{engine.value}-{rep}",
                )
            )
    store.upsert_ai_answers(ai_rows)


def test_quality_suite_writes_at_least_15_checks(tmp_path):
    store, config = _store(tmp_path)
    run_id = "run-quality-1"
    _seed(store, run_id)
    runner = TrackingRunner(config, store=store)
    summary = runner.run_quality_checks(run_id)
    names = {item["check_name"] for item in summary["checks"]}
    assert summary["check_count"] >= 15
    assert len(names) >= 15
    assert store.count("quality_check_log") >= 15
    assert "brand_classifier_quality" in names
    assert summary["gate_status"] in {"succeeded", "partial", "failed"}


def test_baseline_lock_is_immutable(tmp_path):
    store, config = _store(tmp_path)
    run_id = "run-baseline-1"
    _seed(store, run_id)
    service = BaselineService(config, store)
    created = service.create(end_date=date(2026, 9, 15), lock=True, run_id=run_id)
    assert created["status"] == BaselineStatus.LOCKED.value
    assert created["metric_count"] >= 10
    assert store.count("baseline") == created["metric_count"]
    try:
        store.execute(
            "UPDATE baseline SET metric_value = '0' WHERE baseline_id = ?",
            (created["baseline_id"],),
        )
        assert False, "expected immutability abort"
    except Exception as exc:
        assert "immutable" in str(exc).lower() or "locked" in str(exc).lower()


def test_opportunities_are_evidence_backed(tmp_path):
    store, config = _store(tmp_path)
    _seed(store, "run-opp-1")
    rows = OpportunityBuilder(config, store).build(
        report_id="report-1",
        end_date=date(2026, 9, 15),
        limit=10,
    )
    assert 1 <= len(rows) <= 10
    assert store.count("opportunities") == len(rows)
    for row in rows:
        assert row["supporting_evidence_json"]
        assert row["source_row_references_json"]
        assert row["target_page"].startswith("https://")
        assert row["priority_score"] >= 0
