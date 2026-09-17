"""Phase 16 closed-loop integration test."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    ChannelClass,
    OpportunityReviewStatus,
    RunStatus,
    RunType,
)
from data_sources.tracking.experiments.costs import add_experiment_cost
from data_sources.tracking.experiments.measurement import measure_experiment
from data_sources.tracking.experiments.service import (
    create_experiment_from_opportunity,
    record_publication,
)
from data_sources.tracking.models import Ga4DailyRow, GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _seed_window(store, *, page: str, query: str, start: date, days: int, clicks_per_day: float, run_id: str):
    gsc = []
    ga4 = []
    for i in range(days):
        d = start + timedelta(days=i)
        gsc.append(
            GscDailyRow(
                run_id=run_id,
                as_of_date=d,
                date=d,
                query=query,
                page=page,
                country="sgp",
                clicks=int(clicks_per_day),
                impressions=100,
                ctr=clicks_per_day / 100,
                position=8.0,
                is_brand=False,
                brand_rule_version="v1",
            )
        )
        # Sitewide filler (non-target) for counterfactual.
        gsc.append(
            GscDailyRow(
                run_id=run_id,
                as_of_date=d,
                date=d,
                query="other query",
                page="https://sunnystep.com/collections/sandals",
                country="sgp",
                clicks=20,
                impressions=200,
                ctr=0.1,
                position=5.0,
                is_brand=False,
                brand_rule_version="v1",
            )
        )
        ga4.append(
            Ga4DailyRow(
                run_id=run_id,
                as_of_date=d,
                date=d,
                session_source="google",
                session_medium="organic",
                landing_page=page,
                channel_class=ChannelClass.ORGANIC_SEARCH,
                sessions=int(clicks_per_day) + 2,
                engaged_sessions=int(clicks_per_day),
                purchases=0,
                total_revenue=Decimal("0"),
                revenue_currency="SGD",
            )
        )
    store.upsert_gsc(gsc)
    store.upsert_ga4(ga4)


def test_phase16_closed_loop(tmp_path):
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 'loop.db'}")
    store = TrackingStore(config)
    store.migrate()
    store.insert_run(
        RunLog(
            run_id="loop-run",
            run_type=RunType.MANUAL,
            as_of_date=date(2026, 11, 1),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    page = "https://sunnystep.com/collections/walking-shoes"
    query = "comfortable walking shoes"
    published = date(2026, 9, 20)
    baseline_start = published - timedelta(days=28)
    _seed_window(
        store,
        page=page,
        query=query,
        start=baseline_start,
        days=28,
        clicks_per_day=5,
        run_id="loop-run",
    )
    _seed_window(
        store,
        page=page,
        query=query,
        start=published,
        days=28,
        clicks_per_day=8,
        run_id="loop-run",
    )

    now = utc_now_iso()
    store.insert_opportunities_v2(
        [
            {
                "opportunity_id": "opp-loop",
                "report_id": "rep-loop",
                "category": "seo",
                "source_type": "ctr_underperformance",
                "problem": "Title underperforms CTR curve",
                "supporting_evidence_json": {"keyword": query},
                "source_row_references_json": [{"table": "gsc_daily"}],
                "target_query_or_question": query,
                "target_page": page,
                "proposed_action": "Rewrite title",
                "action_type": "title_meta_rewrite",
                "expected_incremental_clicks": 40,
                "estimated_cost": 120,
                "cost_currency": "SGD",
                "owner": "Maliki",
                "impact_score": 40,
                "impact_estimate": "40",
                "confidence_label": "medium",
                "confidence_value": 0.6,
                "effort_label": "M",
                "effort_value": 2,
                "priority_score": 12,
                "metric_to_watch": "gsc_clicks",
                "measurement_window_json": {},
                "assumptions_json": ["test"],
                "review_status": OpportunityReviewStatus.APPROVED.value,
                "reviewed_by": "Ting",
                "reviewed_at": now,
                "priority_inputs_json": {},
                "catalogue_version": "evidence-loop",
                "selection_report_json": {},
                "status": "approved",
                "created_at": now,
                "updated_at": now,
            }
        ]
    )

    created = create_experiment_from_opportunity(
        store, config, opportunity_id="opp-loop", approved_by="Ting", owner="Maliki"
    )
    eid = created["experiment"]["experiment_id"]
    add_experiment_cost(
        store, config, experiment_id=eid, cost_type="writing", quantity=2, unit_cost=40
    )
    add_experiment_cost(
        store, config, experiment_id=eid, cost_type="review", quantity=1, unit_cost=45
    )
    record_publication(
        store,
        config,
        experiment_id=eid,
        published_at=datetime(2026, 9, 20, 9, 0, 0),
        content_before_hash="before",
        content_after_hash="after",
        implementation_reference="shopify:article:1",
        changes=[
            {
                "change_type": "title_meta_rewrite",
                "target_asset": page,
                "before_value": "Old title",
                "after_value": "New title",
            }
        ],
    )
    # as_of includes GSC lag of 3 days after day 28 window end (2026-10-17)
    measured = measure_experiment(
        store,
        config,
        experiment_id=eid,
        checkpoint_days=28,
        as_of_date=date(2026, 10, 20),
    )
    m = measured["measurement"]
    assert m["checkpoint_days"] == 28
    assert m["baseline_clicks"] == 28 * 5
    assert m["observed_clicks"] == 28 * 8
    assert m["adjustment_method"] in {"sitewide_adjusted", "before_after", "matched_controls"}
    assert m["adjusted_incremental_clicks"] is not None
    # Idempotent remeasure
    again = measure_experiment(
        store,
        config,
        experiment_id=eid,
        checkpoint_days=28,
        as_of_date=date(2026, 10, 20),
    )
    assert again["idempotent"] is True
    exp = store.get_experiment(eid)
    assert exp["actual_cost"] == 125.0
    assert measured["prior"] is not None
    assert measured["classification"]["outcome"] in {
        "winner",
        "likely_winner",
        "inconclusive",
        "likely_loss",
        "tracking_failure",
        "provisional",
    }
