from __future__ import annotations

import json
import subprocess
import uuid
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional

from .catalogs import require_catalogue_minimums, sync_catalogues
from .config import TrackingConfig
from .costs import CostLedger
from .enums import CollectorStatus, RunStatus, RunType, Source
from .exceptions import TrackingError
from .logging import StructuredLogger
from .models import RunLog, UpsertStats
from .storage import TrackingStore
from .transforms.normalize import parse_date, utc_now_iso


COLLECTOR_ORDER = [Source.GSC.value, Source.GA4.value, Source.SERPER.value, Source.AI_VISIBILITY.value]


def code_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        sha = result.stdout.strip()
        return sha[:12] if sha else "unknown"
    except OSError:
        return "unknown"


class TrackingRunner:
    def __init__(
        self,
        config: TrackingConfig,
        store: Optional[TrackingStore] = None,
        logger: Optional[StructuredLogger] = None,
        collectors: Optional[Dict[str, Callable]] = None,
    ):
        self.config = config
        self.store = store or TrackingStore(config)
        self.logger = logger or StructuredLogger(level=config.log_level)
        self.collectors = collectors or {}
        self.costs = CostLedger(config.daily_cost_cap_usd)

    def doctor(self) -> Dict[str, object]:
        applied = self.store.migrate()
        report = {
            "config": self.config.public_dict(),
            "migrations_applied": applied,
            "storage_path": str(self.store.path),
            "integrations": {
                "gsc": bool(self.config.gsc_property and self.config.gsc_credentials_path),
                "ga4": bool(self.config.ga4_property_id and self.config.ga4_credentials_path),
                "serper": bool(self.config.serper_api_key),
                "openai": bool(self.config.openai_api_key),
                "perplexity": bool(self.config.perplexity_api_key),
                "lark": bool(self.config.lark_base_app_token),
            },
        }
        return report

    def start_run(
        self,
        run_type: RunType,
        as_of: date,
        collectors: Iterable[str],
        *,
        simulate_failure: str = "",
    ) -> RunLog:
        self.store.migrate()
        run_id = str(uuid.uuid4())
        requested = list(collectors)
        self.store.acquire_lock(as_of.isoformat(), run_type.value, run_id, self.config.run_timeout_seconds)
        run = RunLog(
            run_id=run_id,
            run_type=run_type,
            as_of_date=as_of,
            started_at=utc_now_iso(),
            status=RunStatus.RUNNING,
            requested_collectors=requested,
            code_version=code_version(),
            config_fingerprint=self.config.fingerprint(),
        )
        self.store.insert_run(run)
        self.logger.log(
            "run_started",
            run_id=run_id,
            stage="running",
            as_of_date=as_of.isoformat(),
            status=run.status.value,
        )
        try:
            sync_catalogues(self.store, self.config)
            if run_type != RunType.DEMO:
                require_catalogue_minimums(self.config)
        except TrackingError as exc:
            self.store.update_run(
                run.run_id,
                status=RunStatus.FAILED,
                finished_at=utc_now_iso(),
                error_code=exc.error_code,
                error_message=str(exc),
            )
            self.store.release_lock(as_of.isoformat(), run_type.value, run_id)
            raise
        return run

    def finish_run(
        self,
        run: RunLog,
        status: RunStatus,
        *,
        row_counts: Optional[Dict[str, int]] = None,
        failed: Optional[Dict[str, str]] = None,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        self.store.update_run(
            run.run_id,
            status=status,
            finished_at=utc_now_iso(),
            completed_collectors=run.completed_collectors,
            failed_collectors=failed if failed is not None else run.failed_collectors,
            row_counts=row_counts or run.row_counts,
            cost_usd=self.costs.spent,
            error_code=error_code or None,
            error_message=error_message or None,
        )
        self.store.release_lock(run.as_of_date.isoformat(), run.run_type.value, run.run_id)
        self.logger.log(
            "run_finished",
            run_id=run.run_id,
            stage="finished",
            as_of_date=run.as_of_date.isoformat(),
            status=status.value,
            cost_usd=str(self.costs.spent),
        )
