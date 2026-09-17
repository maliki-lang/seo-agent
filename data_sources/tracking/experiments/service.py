"""Experiment service: create from approved opportunity and record publication."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from ..config import TrackingConfig
from ..enums import ExperimentStatus, OpportunityReviewStatus
from ..exceptions import DataQualityError
from ..opportunities.targets import canonicalize_target_url
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .gates import ACTIVE_EXPERIMENT_STATUSES, assert_transition, validate_opportunity_for_experiment


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _baseline_window_before(published_at: datetime) -> tuple[str, str]:
    end = (published_at.date() - timedelta(days=1))
    start = end - timedelta(days=27)
    return start.isoformat(), end.isoformat()


def create_experiment_from_opportunity(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    opportunity_id: str,
    approved_by: str,
    owner: str,
    hypothesis: Optional[str] = None,
    control_pages: Optional[List[str]] = None,
    planned_publish_at: Optional[date] = None,
    cost_currency: Optional[str] = None,
    homepage_approved_reason: Optional[str] = None,
    supersedes_experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    rows = store.fetchall(
        "SELECT * FROM opportunities WHERE opportunity_id = ?",
        (opportunity_id,),
    )
    if not rows:
        raise DataQualityError(f"Unknown opportunity_id: {opportunity_id}")
    opportunity = dict(rows[0])
    opportunity["supporting_evidence_json"] = _load_json(
        opportunity.get("supporting_evidence_json"), {}
    )

    # Idempotent path: already converted with an active experiment.
    active = store.fetchall(
        f"""
        SELECT experiment_id, status FROM seo_experiments
        WHERE opportunity_id = ?
          AND status IN ({",".join("?" * len(ACTIVE_EXPERIMENT_STATUSES))})
        """,
        (opportunity_id, *sorted(ACTIVE_EXPERIMENT_STATUSES)),
    )
    if active and not supersedes_experiment_id:
        existing = store.get_experiment(active[0]["experiment_id"])
        return {
            "experiment": existing,
            "gates": [],
            "idempotent": True,
            "note": "active experiment already exists for opportunity",
        }

    if opportunity.get("review_status") == OpportunityReviewStatus.CONVERTED_TO_EXPERIMENT.value:
        raise DataQualityError(
            f"Opportunity {opportunity_id} already converted and has no active experiment; "
            "pass supersedes_experiment_id after closing the prior experiment"
        )

    if opportunity.get("review_status") != OpportunityReviewStatus.APPROVED.value:
        raise DataQualityError(
            f"Opportunity {opportunity_id} is not approved (status={opportunity.get('review_status')})"
        )

    if not (opportunity.get("reviewed_by") or "").strip() or not opportunity.get("reviewed_at"):
        raise DataQualityError("Approved opportunity requires reviewed_by and reviewed_at")

    gates = validate_opportunity_for_experiment(
        opportunity,
        minimum_incremental_clicks=config.economics.minimum_incremental_clicks,
        homepage_approved_reason=homepage_approved_reason,
    )

    if active:
        if supersedes_experiment_id:
            prior = store.get_experiment(supersedes_experiment_id)
            if not prior or prior.get("status") != ExperimentStatus.CLOSED.value:
                raise DataQualityError(
                    "supersedes_experiment_id must reference a closed experiment"
                )
        else:
            existing = store.get_experiment(active[0]["experiment_id"])
            return {
                "experiment": existing,
                "gates": gates,
                "idempotent": True,
                "note": "active experiment already exists for opportunity",
            }

    target_page = canonicalize_target_url(opportunity.get("target_page") or "") or (
        opportunity.get("target_page") or ""
    )
    queries = []
    tq = opportunity.get("target_query_or_question") or ""
    if tq:
        queries.append(tq)
    evidence = opportunity.get("supporting_evidence_json") or {}
    if evidence.get("keyword") and evidence["keyword"] not in queries:
        queries.append(evidence["keyword"])

    now = utc_now_iso()
    experiment_id = str(uuid.uuid4())
    controls = [canonicalize_target_url(p) or p for p in (control_pages or []) if p]
    row = {
        "experiment_id": experiment_id,
        "opportunity_id": opportunity_id,
        "supersedes_experiment_id": supersedes_experiment_id,
        "status": ExperimentStatus.APPROVED.value,
        "hypothesis": hypothesis
        or opportunity.get("problem")
        or "Approved opportunity experiment",
        "action_type": opportunity.get("action_type") or "",
        "target_page": target_page,
        "target_queries_json": queries,
        "control_pages_json": controls,
        "primary_metric": "adjusted_incremental_nonbranded_clicks",
        "guardrail_metrics_json": [
            "organic_sessions",
            "engaged_sessions",
            "purchases",
            "revenue",
        ],
        "approved_by": approved_by.strip(),
        "approved_at": now,
        "owner": owner.strip(),
        "planned_publish_at": planned_publish_at.isoformat() if planned_publish_at else None,
        "published_at": None,
        "baseline_start": None,
        "baseline_end": None,
        "estimated_incremental_clicks": opportunity.get("expected_incremental_clicks"),
        "estimated_cost": opportunity.get("estimated_cost"),
        "actual_cost": 0.0,
        "cost_currency": (
            cost_currency
            or opportunity.get("cost_currency")
            or config.economics.default_currency
        ),
        "counterfactual_method": "sitewide_adjusted",
        "content_before_hash": None,
        "content_after_hash": None,
        "implementation_reference": None,
        "catalogue_version": opportunity.get("catalogue_version"),
        "source_report_id": opportunity.get("report_id") or "",
        "metadata_json": {
            "benchmark_ids": _load_json(opportunity.get("benchmark_ids_json"), []),
            "source_row_references": _load_json(
                opportunity.get("source_row_references_json"), []
            ),
            "source_type": opportunity.get("source_type"),
            "isolation_quality": "high",
        },
        "created_at": now,
        "updated_at": now,
    }
    store.insert_experiment(row)
    store.update_opportunity(
        opportunity_id,
        {
            "review_status": OpportunityReviewStatus.CONVERTED_TO_EXPERIMENT.value,
            "status": "converted_to_experiment",
            "updated_at": now,
        },
    )
    return {"experiment": store.get_experiment(experiment_id), "gates": gates, "idempotent": False}


def record_publication(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    experiment_id: str,
    published_at: datetime,
    content_before_hash: str,
    content_after_hash: str,
    implementation_reference: str,
    changes: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    store.migrate()
    experiment = store.get_experiment(experiment_id)
    if not experiment:
        raise DataQualityError(f"Unknown experiment_id: {experiment_id}")
    current = experiment["status"]
    if current not in {
        ExperimentStatus.APPROVED.value,
        ExperimentStatus.IMPLEMENTING.value,
    }:
        raise DataQualityError(
            f"Publication requires approved or implementing status (got {current})"
        )
    if not content_before_hash or not content_after_hash:
        raise DataQualityError("content_before_hash and content_after_hash are required")
    if content_before_hash == content_after_hash:
        raise DataQualityError("content_before_hash and content_after_hash must differ")
    if not changes:
        raise DataQualityError("At least one change row is required")
    if not implementation_reference:
        raise DataQualityError("implementation_reference is required")

    assert_transition(current, ExperimentStatus.PUBLISHED.value)
    baseline_start, baseline_end = _baseline_window_before(published_at)
    now = utc_now_iso()
    change_rows = []
    for item in changes:
        change_rows.append(
            {
                "change_id": str(uuid.uuid4()),
                "experiment_id": experiment_id,
                "change_type": item["change_type"],
                "target_asset": item["target_asset"],
                "before_value": item.get("before_value"),
                "after_value": item.get("after_value"),
                "evidence_reference": item.get("evidence_reference"),
                "implemented_by": item.get("implemented_by"),
                "implemented_at": item.get("implemented_at") or published_at.isoformat(),
                "created_at": now,
            }
        )
    store.insert_experiment_changes(change_rows)

    metadata = _load_json(experiment.get("metadata_json"), {})
    primary_types = {c["change_type"] for c in change_rows}
    if len(primary_types) > 1:
        metadata["isolation_quality"] = "low"
        metadata["isolation_note"] = "multiple independent change types bundled"
    metadata["checkpoint_plan"] = [14, 28, 56]

    store.update_experiment(
        experiment_id,
        {
            "status": ExperimentStatus.PUBLISHED.value,
            "published_at": published_at.isoformat(),
            "baseline_start": baseline_start,
            "baseline_end": baseline_end,
            "content_before_hash": content_before_hash,
            "content_after_hash": content_after_hash,
            "implementation_reference": implementation_reference,
            "metadata_json": metadata,
            "updated_at": now,
        },
    )
    return {
        "experiment": store.get_experiment(experiment_id),
        "changes": len(change_rows),
        "baseline_start": baseline_start,
        "baseline_end": baseline_end,
    }


def list_experiments(
    store: TrackingStore,
    *,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    if status:
        rows = store.fetchall(
            "SELECT * FROM seo_experiments WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
    else:
        rows = store.fetchall("SELECT * FROM seo_experiments ORDER BY created_at DESC")
    return {"count": len(rows), "experiments": [dict(r) for r in rows]}


def get_experiment(store: TrackingStore, *, experiment_id: str) -> Dict[str, Any]:
    store.migrate()
    experiment = store.get_experiment(experiment_id)
    if not experiment:
        raise DataQualityError(f"Unknown experiment_id: {experiment_id}")
    changes = [dict(r) for r in store.list_experiment_changes(experiment_id)]
    costs = [dict(r) for r in store.list_experiment_costs(experiment_id)]
    measurements = [dict(r) for r in store.list_experiment_measurements(experiment_id)]
    return {
        "experiment": experiment,
        "changes": changes,
        "costs": costs,
        "measurements": measurements,
    }
