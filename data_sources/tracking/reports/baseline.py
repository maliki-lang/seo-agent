from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

from ..checks.suite import QualityCheckSuite
from ..config import TrackingConfig
from ..enums import BaselineStatus, RunStatus, RunType
from ..exceptions import DataQualityError, TrackingError
from ..models import RunLog
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .metrics_calc import compute_period_metrics, resolve_baseline_window


class BaselineService:
    def __init__(self, config: TrackingConfig, store: TrackingStore):
        self.config = config
        self.store = store

    def create(
        self,
        *,
        end_date: Optional[date] = None,
        lock: bool = False,
        locked_by: str = "tracking-cli",
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.store.migrate()
        period_start, period_end = resolve_baseline_window(self.store, self.config, end_date)
        metrics, fingerprint = compute_period_metrics(self.store, period_start, period_end)
        baseline_id = str(uuid.uuid4())
        baseline_name = f"sitewide-{period_start.isoformat()}-to-{period_end.isoformat()}"
        status = BaselineStatus.DRAFT
        locked_at = None
        if lock:
            gate = self._required_checks(run_id, as_of=period_end)
            if gate["gate_status"] == "failed":
                raise DataQualityError(
                    "Cannot lock baseline while critical quality checks fail: "
                    + ", ".join(gate.get("critical_failures") or [])
                )
            status = BaselineStatus.LOCKED
            locked_at = utc_now_iso()

        rows = []
        created_at = utc_now_iso()
        for item in metrics:
            rows.append(
                {
                    "baseline_id": baseline_id,
                    "baseline_name": baseline_name,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "locked_at": locked_at,
                    "locked_by": locked_by if lock else None,
                    "status": status.value,
                    "metric_name": item["metric_name"],
                    "segment_json": item["segment_json"],
                    "metric_value": item["metric_value"],
                    "numerator": item.get("numerator"),
                    "denominator": item.get("denominator"),
                    "source_query_version": item["source_query_version"],
                    "input_fingerprint": item["input_fingerprint"],
                    "notes": item.get("notes") or "",
                    "created_at": created_at,
                }
            )
        self.store.insert_baseline_rows(rows)
        return {
            "baseline_id": baseline_id,
            "baseline_name": baseline_name,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "status": status.value,
            "metric_count": len(rows),
            "input_fingerprint": fingerprint,
            "locked_at": locked_at,
        }

    def lock(
        self,
        baseline_id: str,
        *,
        locked_by: str = "tracking-cli",
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        gate = self._required_checks(run_id)
        if gate["gate_status"] == "failed":
            raise DataQualityError(
                "Cannot lock baseline while critical quality checks fail: "
                + ", ".join(gate.get("critical_failures") or [])
            )
        existing = self.store.fetchall(
            "SELECT status FROM baseline WHERE baseline_id = ? LIMIT 1",
            (baseline_id,),
        )
        if not existing:
            raise TrackingError(f"Unknown baseline_id {baseline_id}")
        if existing[0]["status"] == BaselineStatus.LOCKED.value:
            return {"baseline_id": baseline_id, "status": "locked", "note": "already locked"}
        locked_at = utc_now_iso()
        self.store.lock_baseline(baseline_id, locked_at=locked_at, locked_by=locked_by)
        return {"baseline_id": baseline_id, "status": "locked", "locked_at": locked_at}

    def _required_checks(
        self,
        run_id: Optional[str],
        *,
        as_of: Optional[date] = None,
    ) -> Dict[str, Any]:
        if run_id:
            return QualityCheckSuite(self.config, self.store).run_for_run(run_id)
        latest = self.store.fetchall(
            "SELECT run_id FROM run_log ORDER BY started_at DESC LIMIT 1"
        )
        if latest:
            return QualityCheckSuite(self.config, self.store).run_for_run(latest[0]["run_id"])
        stub = RunLog(
            run_id=str(uuid.uuid4()),
            run_type=RunType.BASELINE,
            as_of_date=as_of or date.today(),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
            finished_at=utc_now_iso(),
            requested_collectors=[],
            completed_collectors=[],
            cost_usd=Decimal("0"),
        )
        self.store.insert_run(stub)
        return QualityCheckSuite(self.config, self.store).run_for_run(stub.run_id)
