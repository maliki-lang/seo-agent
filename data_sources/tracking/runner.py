from __future__ import annotations

import json
import subprocess
import uuid
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from .catalogs import require_catalogue_minimums, sync_catalogues
from .checks.reconciliation import (
    Ga4ReconciliationCheck,
    GscReconciliationCheck,
    stored_ga4_sessions,
    stored_gsc_totals,
)
from .checks.suite import QualityCheckSuite
from .collectors.ai_visibility import AiVisibilityCollector
from .collectors.base import CollectorResult
from .collectors.ga4 import Ga4Collector, ga4_latest_available_date
from .collectors.gsc import GscCollector, gsc_latest_available_date
from .collectors.serper import SerperCollector
from .config import TrackingConfig
from .costs import CostLedger
from .enums import CollectorStatus, RunStatus, RunType, Severity, Source
from .exceptions import AuthenticationError, TrackingError
from .logging import StructuredLogger
from .models import QualityCheckRow, RunLog, UpsertStats
from .reports.baseline import BaselineService
from .reports.opportunities import OpportunityBuilder
from .reports.weekly import WeeklyReportService
from .sinks.alerts import AlertService
from .storage import TrackingStore
from .transforms.metrics import date_chunks
from .transforms.normalize import sha256_hex, utc_now_iso

IMPLEMENTED_COLLECTORS = {
    Source.GSC.value,
    Source.GA4.value,
    Source.SERPER.value,
    Source.AI_VISIBILITY.value,
}
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
        gsc_collector: Optional[GscCollector] = None,
        ga4_collector: Optional[Ga4Collector] = None,
        serper_collector: Optional[SerperCollector] = None,
        ai_collector: Optional[AiVisibilityCollector] = None,
    ):
        self.config = config
        self.store = store or TrackingStore(config)
        self.logger = logger or StructuredLogger(level=config.log_level)
        self.collectors = collectors or {}
        self.costs = CostLedger(config.daily_cost_cap_usd)
        self.gsc_collector = gsc_collector
        self.ga4_collector = ga4_collector
        self.serper_collector = serper_collector
        self.ai_collector = ai_collector
        self._simulate_failure = ""
        self.alert_service = AlertService(config, self.store)

    def doctor(self) -> Dict[str, object]:
        applied = self.store.migrate()
        return {
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

    def start_run(
        self,
        run_type: RunType,
        as_of: date,
        collectors: Iterable[str],
        *,
        simulate_failure: str = "",
    ) -> RunLog:
        self.store.migrate()
        self._simulate_failure = (simulate_failure or "").strip().lower()
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

    def _gsc(self) -> GscCollector:
        return self.gsc_collector or GscCollector(self.config)

    def _ga4(self) -> Ga4Collector:
        return self.ga4_collector or Ga4Collector(self.config)

    def _serper(self, keyword_limit: int = 0) -> SerperCollector:
        if self.serper_collector is not None:
            return self.serper_collector
        return SerperCollector(self.config, cost_ledger=self.costs, keyword_limit=keyword_limit)

    def _ai(self, question_limit: int = 0) -> AiVisibilityCollector:
        if self.ai_collector is not None:
            return self.ai_collector
        return AiVisibilityCollector(self.config, cost_ledger=self.costs, question_limit=question_limit)

    def _collector(self, source: str, limit: int = 0):
        if source == Source.GSC.value:
            return self._gsc()
        if source == Source.GA4.value:
            return self._ga4()
        if source == Source.SERPER.value:
            return self._serper(keyword_limit=limit)
        if source == Source.AI_VISIBILITY.value:
            return self._ai(question_limit=limit)
        raise TrackingError(f"No collector for source {source}")

    def persist_collector_result(self, source: str, result: CollectorResult) -> UpsertStats:
        for raw in result.raw_payloads:
            payload_json = json.dumps(raw.get("payload"), default=str, sort_keys=True)
            self.store.insert_raw_record(
                raw_record_id=raw["raw_record_id"],
                run_id=result.rows[0].run_id if result.rows else "",
                source=source,
                endpoint_or_operation=raw.get("endpoint_or_operation") or source,
                request_fingerprint=sha256_hex(payload_json)[:32],
                payload_json=payload_json,
                content_type="application/json",
                retention_class="raw-api",
                checksum=sha256_hex(payload_json),
            )
        if source == Source.GSC.value:
            return self.store.upsert_gsc(list(result.rows))
        if source == Source.GA4.value:
            return self.store.upsert_ga4(list(result.rows))
        if source == Source.SERPER.value:
            return self.store.upsert_serp(list(result.rows))
        if source == Source.AI_VISIBILITY.value:
            return self.store.upsert_ai_answers(list(result.rows))
        raise TrackingError(f"No persistence path for source {source}")

    def _record_check(self, run_id: str, result) -> None:
        self.store.insert_quality_checks(
            [
                QualityCheckRow(
                    check_id=str(uuid.uuid4()),
                    run_id=run_id,
                    check_name=result.check_name,
                    scope=result.scope,
                    status=result.status,
                    severity=result.severity,
                    threshold=result.threshold,
                    observed_value=result.observed_value,
                    details_json=result.details,
                )
            ]
        )

    def _reconcile(self, run: RunLog, source: str, start: date, end: date, collector: Any) -> None:
        try:
            if source == Source.GSC.value:
                detailed = stored_gsc_totals(self.store, start, end)
                aggregate = collector.aggregate_totals(start, end)
                check = GscReconciliationCheck().run(
                    detailed_clicks=detailed[0],
                    detailed_impressions=detailed[1],
                    aggregate_clicks=aggregate[0],
                    aggregate_impressions=aggregate[1],
                )
            else:
                detailed_sessions = stored_ga4_sessions(self.store, start, end)
                aggregate_sessions = collector.aggregate_sessions(start, end)
                check = Ga4ReconciliationCheck().run(
                    detailed_sessions=detailed_sessions,
                    aggregate_sessions=aggregate_sessions,
                )
        except TrackingError as exc:
            check = Ga4ReconciliationCheck().run(
                detailed_sessions=0,
                aggregate_sessions=None,
            ) if source == Source.GA4.value else GscReconciliationCheck().run(
                detailed_clicks=0,
                detailed_impressions=0,
                aggregate_clicks=None,
                aggregate_impressions=None,
            )
            check.details["error_class"] = exc.error_code
        self._record_check(run.run_id, check)

    def run_one_source(
        self,
        run: RunLog,
        source: str,
        start: date,
        end: date,
        *,
        dry_run: bool = False,
        limit: int = 0,
    ) -> Dict[str, Any]:
        if source not in IMPLEMENTED_COLLECTORS:
            self.store.set_collector_state(
                run.run_id,
                source,
                CollectorStatus.SKIPPED,
                error_message="Collector not implemented in this phase",
                finished_at=utc_now_iso(),
            )
            return {"source": source, "status": "skipped", "reason": "not-implemented"}
        collector = self._collector(source, limit=limit)
        started = utc_now_iso()
        self.store.set_collector_state(
            run.run_id, source, CollectorStatus.RUNNING, started_at=started
        )
        try:
            if self._simulate_failure:
                target_source, _, failure_kind = self._simulate_failure.partition(":")
                if target_source == source:
                    if failure_kind in {"authentication", "auth"}:
                        raise AuthenticationError(
                            f"Simulated authentication failure for {source} (gate demo)"
                        )
                    raise TrackingError(f"Simulated failure for {source}: {failure_kind or 'error'}")
            result = collector.collect(
                run_id=run.run_id,
                as_of_date=run.as_of_date,
                start_date=start,
                end_date=end,
            )
            stats = UpsertStats(skipped=len(result.rows)) if dry_run else self.persist_collector_result(source, result)
            result.stats = stats
            if not dry_run and source in {Source.GSC.value, Source.GA4.value}:
                self._reconcile(run, source, start, end, collector)
            self.store.set_collector_state(
                run.run_id,
                source,
                CollectorStatus.SUCCEEDED,
                row_count=len(result.rows),
                finished_at=utc_now_iso(),
            )
            if source not in run.completed_collectors and source not in run.failed_collectors:
                run.completed_collectors.append(source)
            run.row_counts[source] = run.row_counts.get(source, 0) + len(result.rows)
            return {
                "source": source,
                "status": "succeeded",
                "rows": len(result.rows),
                "stats": stats.as_dict(),
                "warnings": result.warnings,
                "dry_run": dry_run,
            }
        except TrackingError as exc:
            self.store.set_collector_state(
                run.run_id,
                source,
                CollectorStatus.FAILED,
                error_code=exc.error_code,
                error_message=str(exc),
                finished_at=utc_now_iso(),
            )
            run.completed_collectors = [item for item in run.completed_collectors if item != source]
            run.failed_collectors[source] = exc.error_code
            self.logger.error(
                "collector_failed",
                run_id=run.run_id,
                source=source,
                error_class=exc.error_code,
                as_of_date=run.as_of_date.isoformat(),
            )
            severity = Severity.CRITICAL if exc.error_code in {
                "AuthenticationError",
                "PermissionDenied",
                "CostLimitExceeded",
            } else Severity.ERROR
            self.alert_service.emit(
                run_id=run.run_id,
                alert_type=f"collector_failure:{source}:{exc.error_code}",
                severity=severity,
                summary=f"{source} collector failed with {exc.error_code}",
                details={"source": source, "error_class": exc.error_code},
                recommended_action="Inspect collector_state and retry after fixing credentials/access",
            )
            return {
                "source": source,
                "status": "failed",
                "error_class": exc.error_code,
                "error": str(exc),
            }

    def run_sources(
        self,
        run: RunLog,
        sources: Iterable[str],
        start: date,
        end: date,
        *,
        dry_run: bool = False,
        chunk_days: int = 7,
    ) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for source in sources:
            source_summary = {"source": source, "chunks": []}
            if source not in IMPLEMENTED_COLLECTORS:
                summaries.append(self.run_one_source(run, source, start, end, dry_run=dry_run))
                continue
            for chunk_start, chunk_end in date_chunks(start, end, chunk_days):
                chunk_result = self.run_one_source(
                    run, source, chunk_start, chunk_end, dry_run=dry_run
                )
                source_summary["chunks"].append(
                    {
                        "start": chunk_start.isoformat(),
                        "end": chunk_end.isoformat(),
                        **chunk_result,
                    }
                )
                if chunk_result.get("status") == "failed":
                    break
            summaries.append(source_summary)
        return summaries

    def finalize(self, run: RunLog) -> RunStatus:
        failed = run.failed_collectors
        completed = list(dict.fromkeys(
            item for item in run.completed_collectors if item in IMPLEMENTED_COLLECTORS
        ))
        run.completed_collectors = completed
        requested_implemented = [item for item in run.requested_collectors if item in IMPLEMENTED_COLLECTORS]
        if failed and not completed:
            status = RunStatus.FAILED
        elif failed or len(completed) < len(requested_implemented):
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCEEDED
        self.finish_run(run, status, row_counts=run.row_counts, failed=failed)
        return status

    def collect_source(
        self,
        source: str,
        as_of: date,
        *,
        dry_run: bool = False,
        limit: int = 0,
    ) -> Dict[str, Any]:
        run = self.start_run(RunType.MANUAL, as_of, [source])
        start = as_of
        if source == Source.GSC.value:
            start = gsc_latest_available_date(as_of, self.config.freshness.gsc_days)
        elif source == Source.GA4.value:
            start = ga4_latest_available_date(as_of, self.config.freshness.ga4_days)
        summary = self.run_one_source(run, source, start, start, dry_run=dry_run, limit=limit)
        status = self.finalize(run)
        return {
            "run_id": run.run_id,
            "status": status.value,
            "as_of_date": as_of.isoformat(),
            "data_date": start.isoformat(),
            "result": summary,
        }

    def run_daily(
        self,
        as_of: date,
        *,
        dry_run: bool = False,
        sources: Optional[List[str]] = None,
        simulate_failure: str = "",
    ) -> Dict[str, Any]:
        requested = sources or list(COLLECTOR_ORDER)
        run = self.start_run(RunType.DAILY, as_of, requested, simulate_failure=simulate_failure)
        summaries = []
        for source in requested:
            if source == Source.GSC.value:
                day = gsc_latest_available_date(as_of, self.config.freshness.gsc_days)
            elif source == Source.GA4.value:
                day = ga4_latest_available_date(as_of, self.config.freshness.ga4_days)
            else:
                day = as_of
            summaries.append(self.run_one_source(run, source, day, day, dry_run=dry_run))
        status = self.finalize(run)
        return {
            "run_id": run.run_id,
            "status": status.value,
            "as_of_date": as_of.isoformat(),
            "collectors": summaries,
            "row_counts": run.row_counts,
            "failed_collectors": run.failed_collectors,
        }

    def run_backfill(
        self,
        sources: List[str],
        start: date,
        end: date,
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        run = self.start_run(RunType.BACKFILL, end, sources)
        summaries = self.run_sources(run, sources, start, end, dry_run=dry_run, chunk_days=7)
        status = self.finalize(run)
        return {
            "run_id": run.run_id,
            "status": status.value,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "collectors": summaries,
            "row_counts": run.row_counts,
            "failed_collectors": run.failed_collectors,
        }

    def run_quality_checks(self, run_id: str) -> Dict[str, Any]:
        summary = QualityCheckSuite(self.config, self.store).run_for_run(run_id)
        return {"run_id": run_id, **summary}

    def create_baseline(
        self,
        *,
        end_date: Optional[date] = None,
        lock: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return BaselineService(self.config, self.store).create(
            end_date=end_date,
            lock=lock,
            run_id=run_id,
        )

    def build_opportunities(
        self,
        *,
        report_id: Optional[str] = None,
        end_date: Optional[date] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        report = report_id or f"ops-{utc_now_iso()}"
        rows = OpportunityBuilder(self.config, self.store).build(
            report_id=report,
            end_date=end_date,
            limit=limit,
        )
        return {
            "report_id": report,
            "opportunity_count": len(rows),
            "opportunities": [
                {
                    "opportunity_id": row["opportunity_id"],
                    "category": row["category"],
                    "target_query_or_question": row["target_query_or_question"],
                    "target_page": row["target_page"],
                    "priority_score": row["priority_score"],
                    "impact_score": row["impact_score"],
                    "confidence_label": row["confidence_label"],
                    "effort_label": row["effort_label"],
                    "proposed_action": row["proposed_action"],
                }
                for row in rows
            ],
        }

    def generate_weekly(
        self,
        *,
        period_end: Optional[date] = None,
        publish: bool = False,
        lark_sink=None,
        alert_service=None,
    ) -> Dict[str, Any]:
        service = WeeklyReportService(
            self.config,
            self.store,
            lark_sink=lark_sink,
            alert_service=alert_service or self.alert_service,
        )
        return service.generate(period_end=period_end, publish=publish)
