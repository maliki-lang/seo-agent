"""Unit tests for experiment costs."""

from __future__ import annotations

from datetime import datetime

import pytest

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import OpportunityReviewStatus
from data_sources.tracking.exceptions import DataQualityError
from data_sources.tracking.experiments.costs import add_experiment_cost, cost_per_incremental_click
from data_sources.tracking.experiments.service import create_experiment_from_opportunity
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _ready_experiment(tmp_path):
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 'costs.db'}")
    store = TrackingStore(config)
    store.migrate()
    now = utc_now_iso()
    store.insert_opportunities_v2(
        [
            {
                "opportunity_id": "opp-cost",
                "report_id": "rep",
                "category": "seo",
                "source_type": "ctr_underperformance",
                "problem": "p",
                "supporting_evidence_json": {},
                "source_row_references_json": [{"t": 1}],
                "target_query_or_question": "q",
                "target_page": "https://sunnystep.com/collections/walking-shoes",
                "proposed_action": "a",
                "action_type": "title_meta_rewrite",
                "expected_incremental_clicks": 10,
                "owner": "Maliki",
                "impact_score": 10,
                "impact_estimate": "10",
                "confidence_label": "medium",
                "confidence_value": 0.6,
                "effort_label": "M",
                "effort_value": 2,
                "priority_score": 3,
                "metric_to_watch": "clicks",
                "measurement_window_json": {},
                "assumptions_json": [],
                "review_status": OpportunityReviewStatus.APPROVED.value,
                "reviewed_by": "Ting",
                "reviewed_at": now,
                "priority_inputs_json": {},
                "selection_report_json": {},
                "status": "approved",
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    created = create_experiment_from_opportunity(
        store, config, opportunity_id="opp-cost", approved_by="Ting", owner="Maliki"
    )
    return store, config, created["experiment"]["experiment_id"]


def test_amount_recomputed_and_actual_cost_sums(tmp_path):
    store, config, eid = _ready_experiment(tmp_path)
    first = add_experiment_cost(
        store, config, experiment_id=eid, cost_type="review", quantity=1.5, unit_cost=45
    )
    assert first["amount"] == 67.5
    second = add_experiment_cost(
        store, config, experiment_id=eid, cost_type="writing", quantity=2, unit_cost=50
    )
    assert second["actual_cost"] == 167.5
    exp = store.get_experiment(eid)
    assert exp["actual_cost"] == 167.5


def test_mixed_currency_requires_conversion(tmp_path):
    store, config, eid = _ready_experiment(tmp_path)
    add_experiment_cost(
        store, config, experiment_id=eid, cost_type="review", quantity=1, unit_cost=10, currency="SGD"
    )
    with pytest.raises(DataQualityError, match="Mixed currencies"):
        add_experiment_cost(
            store,
            config,
            experiment_id=eid,
            cost_type="review",
            quantity=1,
            unit_cost=10,
            currency="USD",
        )
    ok = add_experiment_cost(
        store,
        config,
        experiment_id=eid,
        cost_type="review",
        quantity=1,
        unit_cost=10,
        currency="USD",
        conversion_rate=1.35,
        conversion_rate_date="2026-09-01",
    )
    assert ok["currency"] == "SGD"
    assert ok["amount"] == 13.5


def test_zero_incremental_never_divides():
    assert cost_per_incremental_click(cost=100, incremental_clicks=0) is None
    assert cost_per_incremental_click(cost=100, incremental_clicks=-5) is None
    assert cost_per_incremental_click(cost=100, incremental_clicks=10) == 10.0
