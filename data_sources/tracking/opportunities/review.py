"""Opportunity review export/import (Phase 15)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict

from ..enums import OpportunityReviewStatus
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def export_opportunity_review(
    store: TrackingStore,
    *,
    report_id: str,
    output: str,
) -> Dict[str, Any]:
    store.migrate()
    rows = [
        dict(r)
        for r in store.fetchall(
            """
            SELECT * FROM opportunities
            WHERE report_id = ?
            ORDER BY COALESCE(portfolio_rank, 9999), priority_score DESC
            """,
            (report_id,),
        )
    ]
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "opportunity_id",
        "problem",
        "evidence",
        "source_type",
        "cluster_id",
        "family_id",
        "benchmarks",
        "target_page",
        "action_type",
        "proposed_action",
        "expected_click_gain",
        "estimated_cost",
        "confidence",
        "priority",
        "assumptions",
        "metric",
        "measurement_window",
        "review_status",
        "reviewer_decision",
        "reviewer_reason",
        "reviewed_by",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            evidence = _load_json(row.get("supporting_evidence_json"), {})
            writer.writerow(
                {
                    "rank": row.get("portfolio_rank") or "",
                    "opportunity_id": row["opportunity_id"],
                    "problem": row.get("problem"),
                    "evidence": json.dumps(evidence, sort_keys=True),
                    "source_type": row.get("source_type") or row.get("category"),
                    "cluster_id": row.get("cluster_id") or "",
                    "family_id": row.get("family_id") or "",
                    "benchmarks": json.dumps(_load_json(row.get("benchmark_ids_json"), []), sort_keys=True),
                    "target_page": row.get("target_page") or "",
                    "action_type": row.get("action_type") or "",
                    "proposed_action": row.get("proposed_action"),
                    "expected_click_gain": row.get("expected_incremental_clicks"),
                    "estimated_cost": row.get("estimated_cost") or "",
                    "confidence": row.get("confidence_label"),
                    "priority": row.get("priority_score"),
                    "assumptions": json.dumps(_load_json(row.get("assumptions_json"), []), sort_keys=True),
                    "metric": row.get("metric_to_watch"),
                    "measurement_window": row.get("measurement_window_json") or "",
                    "review_status": row.get("review_status") or "",
                    "reviewer_decision": "",
                    "reviewer_reason": "",
                    "reviewed_by": row.get("reviewed_by") or "",
                }
            )
    return {"report_id": report_id, "output": str(path), "rows": len(rows)}


def import_opportunity_decisions(
    store: TrackingStore,
    *,
    report_id: str,
    input_path: str,
) -> Dict[str, Any]:
    store.migrate()
    path = Path(input_path).expanduser()
    if not path.exists():
        raise DataQualityError(f"Missing input file: {path}")
    updated = 0
    now = utc_now_iso()
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        seen = set()
        for row in reader:
            oid = (row.get("opportunity_id") or "").strip()
            if not oid:
                continue
            if oid in seen:
                raise DataQualityError(f"Duplicate opportunity_id in import: {oid}")
            seen.add(oid)
            existing = store.fetchall(
                "SELECT opportunity_id FROM opportunities WHERE opportunity_id = ? AND report_id = ?",
                (oid, report_id),
            )
            if not existing:
                raise DataQualityError(f"Unknown opportunity_id for report: {oid}")
            decision = (row.get("reviewer_decision") or "").strip().lower()
            if not decision:
                continue
            if decision not in {"approved", "rejected", "deferred"}:
                raise DataQualityError(f"Invalid reviewer_decision for {oid}: {decision}")
            reviewed_by = (row.get("reviewed_by") or "").strip()
            if not reviewed_by:
                raise DataQualityError(f"reviewed_by required for decision on {oid}")
            status_map = {
                "approved": OpportunityReviewStatus.APPROVED.value,
                "rejected": OpportunityReviewStatus.REJECTED.value,
                "deferred": OpportunityReviewStatus.DEFERRED.value,
            }
            store.update_opportunity(
                oid,
                {
                    "review_status": status_map[decision],
                    "reviewed_by": reviewed_by,
                    "reviewed_at": now,
                    "status": "approved" if decision == "approved" else decision,
                    "updated_at": now,
                    "blocked_reason": (row.get("reviewer_reason") or "").strip() or None,
                },
            )
            updated += 1
    return {"report_id": report_id, "updated": updated, "input": str(path)}
