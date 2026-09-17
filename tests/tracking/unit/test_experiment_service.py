"""Unit tests for Phase 16 experiment service."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import OpportunityReviewStatus
from data_sources.tracking.exceptions import DataQualityError
from data_sources.tracking.experiments.service import (
    create_experiment_from_opportunity,
    record_publication,
)
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _store(tmp_path):
    config = TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'exp.db'}",
        brand_terms_path="config/brand_terms.txt",
    )
    store = TrackingStore(config)
    store.migrate()
    return store, config


def _insert_opportunity(store, *, approved=True, clicks=12.0, page="https://sunnystep.com/collections/walking-shoes"):
    oid = "opp-1"
    now = utc_now_iso()
    store.insert_opportunities_v2(
        [
            {
                "opportunity_id": oid,
                "report_id": "rep-1",
                "opportunity_version": "v2",
                "category": "seo",
                "source_type": "ctr_underperformance",
                "problem": "CTR underperformance",
                "supporting_evidence_json": {"keyword": "comfortable walking shoes"},
                "source_row_references_json": [{"table": "gsc_daily", "id": "1"}],
                "target_query_or_question": "comfortable walking shoes",
                "target_page": page,
                "target_page_status": "observed_page_needs_optimization",
                "proposed_action": "Rewrite title/meta",
                "action_type": "title_meta_rewrite",
                "cluster_id": "c1",
                "family_id": "f1",
                "benchmark_ids_json": ["b1"],
                "expected_incremental_clicks": clicks,
                "expected_geo_gain": None,
                "estimated_cost": 90.0,
                "cost_currency": "SGD",
                "owner": "Maliki",
                "impact_score": clicks,
                "impact_estimate": f"estimated_click_gain={clicks}",
                "confidence_label": "medium",
                "confidence_value": 0.6,
                "effort_label": "M",
                "effort_value": 2.0,
                "priority_score": 3.6,
                "metric_to_watch": "gsc_clicks",
                "measurement_window_json": {"measurement_days": [14, 28, 56]},
                "assumptions_json": ["test"],
                "review_status": (
                    OpportunityReviewStatus.APPROVED.value
                    if approved
                    else OpportunityReviewStatus.AWAITING_HUMAN_REVIEW.value
                ),
                "reviewed_by": "Ting" if approved else None,
                "reviewed_at": now if approved else None,
                "portfolio_rank": 1,
                "priority_inputs_json": {},
                "catalogue_version": "evidence-test",
                "selection_report_json": {},
                "status": "approved" if approved else "open",
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    return oid


def test_unapproved_opportunity_cannot_create_experiment(tmp_path):
    store, config = _store(tmp_path)
    oid = _insert_opportunity(store, approved=False)
    with pytest.raises(DataQualityError, match="not approved"):
        create_experiment_from_opportunity(
            store, config, opportunity_id=oid, approved_by="Ting", owner="Maliki"
        )


def test_approved_creates_one_active_experiment_idempotent(tmp_path):
    store, config = _store(tmp_path)
    oid = _insert_opportunity(store, approved=True)
    first = create_experiment_from_opportunity(
        store, config, opportunity_id=oid, approved_by="Ting", owner="Maliki"
    )
    second = create_experiment_from_opportunity(
        store, config, opportunity_id=oid, approved_by="Ting", owner="Maliki"
    )
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["experiment"]["experiment_id"] == second["experiment"]["experiment_id"]
    rows = store.fetchall("SELECT * FROM seo_experiments WHERE opportunity_id = ?", (oid,))
    assert len(rows) == 1


def test_publication_requires_changed_hashes_and_changes(tmp_path):
    store, config = _store(tmp_path)
    oid = _insert_opportunity(store, approved=True)
    created = create_experiment_from_opportunity(
        store, config, opportunity_id=oid, approved_by="Ting", owner="Maliki"
    )
    eid = created["experiment"]["experiment_id"]
    with pytest.raises(DataQualityError, match="must differ"):
        record_publication(
            store,
            config,
            experiment_id=eid,
            published_at=datetime(2026, 9, 20, 9, 0, 0),
            content_before_hash="abc",
            content_after_hash="abc",
            implementation_reference="commit:1",
            changes=[{"change_type": "title", "target_asset": "page"}],
        )
    with pytest.raises(DataQualityError, match="At least one change"):
        record_publication(
            store,
            config,
            experiment_id=eid,
            published_at=datetime(2026, 9, 20, 9, 0, 0),
            content_before_hash="abc",
            content_after_hash="def",
            implementation_reference="commit:1",
            changes=[],
        )
    out = record_publication(
        store,
        config,
        experiment_id=eid,
        published_at=datetime(2026, 9, 20, 9, 0, 0),
        content_before_hash="abc",
        content_after_hash="def",
        implementation_reference="commit:1",
        changes=[
            {
                "change_type": "title_meta_rewrite",
                "target_asset": "https://sunnystep.com/collections/walking-shoes",
                "before_value": "Old",
                "after_value": "New",
            }
        ],
    )
    assert out["experiment"]["status"] == "published"
    assert out["baseline_start"] == "2026-08-23"
    assert out["baseline_end"] == "2026-09-19"


def test_http_and_zero_impact_rejected(tmp_path):
    store, config = _store(tmp_path)
    oid = _insert_opportunity(
        store, approved=True, clicks=0, page="http://sunnystep.com/collections/walking-shoes"
    )
    with pytest.raises(DataQualityError, match="gates failed"):
        create_experiment_from_opportunity(
            store, config, opportunity_id=oid, approved_by="Ting", owner="Maliki"
        )
