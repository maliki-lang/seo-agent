from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .config import TrackingConfig
from .enums import CollectorStatus, RunStatus
from .exceptions import RunLockError, SchemaMismatchError
from .migrations import migration_files
from .models import (
    AiAnswerRow,
    Ga4DailyRow,
    GscDailyRow,
    QualityCheckRow,
    RunLog,
    SerpDailyRow,
    UpsertStats,
)
from .transforms.normalize import utc_now, utc_now_iso


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


class TrackingStore:
    def __init__(self, config: TrackingConfig):
        self.config = config
        self.path = config.sqlite_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self.path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> List[str]:
        applied: List[str] = []
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            existing = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for path in migration_files():
                version = path.stem
                if version in existing:
                    continue
                sql = path.read_text(encoding="utf-8")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now_iso()),
                )
                applied.append(version)
        return applied

    def acquire_lock(self, as_of_date: str, run_type: str, run_id: str, ttl_seconds: int) -> None:
        lock_key = f"{run_type}:{as_of_date}"
        now = utc_now()
        expires = (now + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT run_id, expires_at FROM run_locks WHERE lock_key = ?",
                (lock_key,),
            ).fetchone()
            if row:
                expires_at = datetime.fromisoformat(row["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at > now and row["run_id"] != run_id:
                    raise RunLockError(
                        f"Run lock held for {lock_key} by {row['run_id']}"
                    )
            conn.execute(
                """
                INSERT INTO run_locks(lock_key, run_id, acquired_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lock_key) DO UPDATE SET
                    run_id = excluded.run_id,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                """,
                (lock_key, run_id, utc_now_iso(), expires),
            )

    def release_lock(self, as_of_date: str, run_type: str, run_id: str) -> None:
        lock_key = f"{run_type}:{as_of_date}"
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM run_locks WHERE lock_key = ? AND run_id = ?",
                (lock_key, run_id),
            )

    def insert_run(self, run: RunLog) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO run_log(
                    run_id, run_type, as_of_date, started_at, finished_at, status,
                    requested_collectors, completed_collectors, failed_collectors,
                    attempt_number, parent_run_id, code_version, config_fingerprint,
                    row_counts, cost_usd, error_code, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.run_type.value,
                    run.as_of_date.isoformat(),
                    run.started_at,
                    run.finished_at,
                    run.status.value,
                    json.dumps(run.requested_collectors),
                    json.dumps(run.completed_collectors),
                    json.dumps(run.failed_collectors),
                    run.attempt_number,
                    run.parent_run_id,
                    run.code_version,
                    run.config_fingerprint,
                    json.dumps(run.row_counts),
                    format(run.cost_usd, "f"),
                    run.error_code,
                    run.error_message,
                    run.created_at,
                    run.updated_at,
                ),
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: Optional[RunStatus] = None,
        finished_at: Optional[str] = None,
        completed_collectors: Optional[List[str]] = None,
        failed_collectors: Optional[Dict[str, str]] = None,
        row_counts: Optional[Dict[str, int]] = None,
        cost_usd: Optional[Decimal] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: List[Any] = [utc_now_iso()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status.value)
        if finished_at is not None:
            assignments.append("finished_at = ?")
            values.append(finished_at)
        if completed_collectors is not None:
            assignments.append("completed_collectors = ?")
            values.append(json.dumps(completed_collectors))
        if failed_collectors is not None:
            assignments.append("failed_collectors = ?")
            values.append(json.dumps(failed_collectors))
        if row_counts is not None:
            assignments.append("row_counts = ?")
            values.append(json.dumps(row_counts))
        if cost_usd is not None:
            assignments.append("cost_usd = ?")
            values.append(format(cost_usd, "f"))
        if error_code is not None:
            assignments.append("error_code = ?")
            values.append(error_code)
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message)
        values.append(run_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE run_log SET {', '.join(assignments)} WHERE run_id = ?",
                values,
            )

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM run_log WHERE run_id = ?", (run_id,)).fetchone()

    def set_collector_state(
        self,
        run_id: str,
        collector: str,
        status: CollectorStatus,
        *,
        attempt_number: int = 1,
        row_count: Optional[int] = None,
        cost_usd: Optional[Decimal] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO collector_state(
                    run_id, collector, status, attempt_number, row_count, cost_usd,
                    error_code, error_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, collector) DO UPDATE SET
                    status = excluded.status,
                    attempt_number = excluded.attempt_number,
                    row_count = excluded.row_count,
                    cost_usd = excluded.cost_usd,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    started_at = COALESCE(excluded.started_at, collector_state.started_at),
                    finished_at = excluded.finished_at
                """,
                (
                    run_id,
                    collector,
                    status.value,
                    attempt_number,
                    row_count,
                    None if cost_usd is None else format(cost_usd, "f"),
                    error_code,
                    error_message,
                    started_at,
                    finished_at,
                ),
            )

    def upsert_gsc(self, rows: Sequence[GscDailyRow]) -> UpsertStats:
        sql = """
            INSERT INTO gsc_daily(
                natural_key, run_id, as_of_date, date, query, page, country,
                clicks, impressions, ctr, position, is_brand, brand_rule_version,
                source, collected_at, row_hash, schema_version, raw_record_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(natural_key) DO UPDATE SET
                run_id = excluded.run_id,
                as_of_date = excluded.as_of_date,
                clicks = excluded.clicks,
                impressions = excluded.impressions,
                ctr = excluded.ctr,
                position = excluded.position,
                is_brand = excluded.is_brand,
                brand_rule_version = excluded.brand_rule_version,
                collected_at = excluded.collected_at,
                row_hash = excluded.row_hash,
                schema_version = excluded.schema_version,
                raw_record_id = excluded.raw_record_id,
                updated_at = excluded.updated_at
            WHERE gsc_daily.row_hash != excluded.row_hash
        """
        values = [
            (
                row.natural_key,
                row.run_id,
                row.as_of_date.isoformat(),
                row.date.isoformat(),
                row.query,
                row.page,
                row.country,
                row.clicks,
                row.impressions,
                row.ctr,
                row.position,
                int(row.is_brand),
                row.brand_rule_version,
                row.source.value,
                row.collected_at,
                row.row_hash,
                row.schema_version,
                row.raw_record_id,
                utc_now_iso(),
                utc_now_iso(),
            )
            for row in rows
        ]
        return self._upsert("gsc_daily", [r.natural_key for r in rows], [r.row_hash for r in rows], sql, values)

    def upsert_ga4(self, rows: Sequence[Ga4DailyRow]) -> UpsertStats:
        sql = """
            INSERT INTO ga4_daily(
                natural_key, run_id, as_of_date, date, session_source, session_medium,
                landing_page, channel_class, sessions, engaged_sessions, purchases,
                total_revenue, revenue_currency, source, collected_at, row_hash,
                schema_version, raw_record_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(natural_key) DO UPDATE SET
                run_id = excluded.run_id,
                as_of_date = excluded.as_of_date,
                channel_class = excluded.channel_class,
                sessions = excluded.sessions,
                engaged_sessions = excluded.engaged_sessions,
                purchases = excluded.purchases,
                total_revenue = excluded.total_revenue,
                revenue_currency = excluded.revenue_currency,
                collected_at = excluded.collected_at,
                row_hash = excluded.row_hash,
                schema_version = excluded.schema_version,
                raw_record_id = excluded.raw_record_id,
                updated_at = excluded.updated_at
            WHERE ga4_daily.row_hash != excluded.row_hash
        """
        values = [
            (
                row.natural_key,
                row.run_id,
                row.as_of_date.isoformat(),
                row.date.isoformat(),
                row.session_source,
                row.session_medium,
                row.landing_page,
                row.channel_class.value,
                row.sessions,
                row.engaged_sessions,
                row.purchases,
                format(row.total_revenue, "f"),
                row.revenue_currency,
                row.source.value,
                row.collected_at,
                row.row_hash,
                row.schema_version,
                row.raw_record_id,
                utc_now_iso(),
                utc_now_iso(),
            )
            for row in rows
        ]
        return self._upsert("ga4_daily", [r.natural_key for r in rows], [r.row_hash for r in rows], sql, values)

    def upsert_serp(self, rows: Sequence[SerpDailyRow]) -> UpsertStats:
        sql = """
            INSERT INTO serp_daily(
                natural_key, run_id, as_of_date, date, keyword_id, keyword, cluster,
                target_page, country, device, sunnystep_position, result_count_inspected,
                top_10_domains, ai_overview_status, ai_overview_citations, source,
                collected_at, row_hash, schema_version, raw_record_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(natural_key) DO UPDATE SET
                run_id = excluded.run_id,
                as_of_date = excluded.as_of_date,
                keyword = excluded.keyword,
                cluster = excluded.cluster,
                target_page = excluded.target_page,
                sunnystep_position = excluded.sunnystep_position,
                result_count_inspected = excluded.result_count_inspected,
                top_10_domains = excluded.top_10_domains,
                ai_overview_status = excluded.ai_overview_status,
                ai_overview_citations = excluded.ai_overview_citations,
                collected_at = excluded.collected_at,
                row_hash = excluded.row_hash,
                schema_version = excluded.schema_version,
                raw_record_id = excluded.raw_record_id,
                updated_at = excluded.updated_at
            WHERE serp_daily.row_hash != excluded.row_hash
        """
        values = [
            (
                row.natural_key,
                row.run_id,
                row.as_of_date.isoformat(),
                row.date.isoformat(),
                row.keyword_id,
                row.keyword,
                row.cluster,
                row.target_page,
                row.country,
                row.device,
                row.sunnystep_position,
                row.result_count_inspected,
                json.dumps(row.top_10_domains),
                row.ai_overview_status.value,
                json.dumps(row.ai_overview_citations),
                row.source.value,
                row.collected_at,
                row.row_hash,
                row.schema_version,
                row.raw_record_id,
                utc_now_iso(),
                utc_now_iso(),
            )
            for row in rows
        ]
        return self._upsert("serp_daily", [r.natural_key for r in rows], [r.row_hash for r in rows], sql, values)

    def upsert_ai_answers(self, rows: Sequence[AiAnswerRow]) -> UpsertStats:
        sql = """
            INSERT INTO ai_answer_runs(
                natural_key, run_id, as_of_date, engine, question_id, question,
                repetition_number, raw_answer, mentioned_sunnystep, cited_urls,
                named_competitors, target_cluster, target_page, api_cost_usd,
                latency_ms, model, search_enabled, parser_version, source,
                collected_at, row_hash, schema_version, raw_record_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(natural_key) DO UPDATE SET
                run_id = excluded.run_id,
                question = excluded.question,
                raw_answer = excluded.raw_answer,
                mentioned_sunnystep = excluded.mentioned_sunnystep,
                cited_urls = excluded.cited_urls,
                named_competitors = excluded.named_competitors,
                target_cluster = excluded.target_cluster,
                target_page = excluded.target_page,
                api_cost_usd = excluded.api_cost_usd,
                latency_ms = excluded.latency_ms,
                model = excluded.model,
                search_enabled = excluded.search_enabled,
                parser_version = excluded.parser_version,
                collected_at = excluded.collected_at,
                row_hash = excluded.row_hash,
                schema_version = excluded.schema_version,
                raw_record_id = excluded.raw_record_id,
                updated_at = excluded.updated_at
            WHERE ai_answer_runs.row_hash != excluded.row_hash
        """
        values = [
            (
                row.natural_key,
                row.run_id,
                row.as_of_date.isoformat(),
                row.engine.value,
                row.question_id,
                row.question,
                row.repetition_number,
                row.raw_answer,
                int(row.mentioned_sunnystep),
                json.dumps(row.cited_urls),
                json.dumps(row.named_competitors),
                row.target_cluster,
                row.target_page,
                format(row.api_cost_usd, "f"),
                row.latency_ms,
                row.model,
                int(row.search_enabled),
                row.parser_version,
                row.source.value,
                row.collected_at,
                row.row_hash,
                row.schema_version,
                row.raw_record_id,
                utc_now_iso(),
                utc_now_iso(),
            )
            for row in rows
        ]
        return self._upsert(
            "ai_answer_runs", [r.natural_key for r in rows], [r.row_hash for r in rows], sql, values
        )

    def insert_quality_checks(self, rows: Sequence[QualityCheckRow]) -> None:
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO quality_check_log(
                    check_id, run_id, check_name, scope, status, severity,
                    threshold, observed_value, details_json, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.check_id,
                        row.run_id,
                        row.check_name,
                        row.scope,
                        row.status.value,
                        row.severity.value,
                        row.threshold,
                        row.observed_value,
                        json.dumps(row.details_json),
                        row.checked_at,
                    )
                    for row in rows
                ],
            )

    def insert_raw_record(
        self,
        raw_record_id: str,
        run_id: str,
        source: str,
        endpoint_or_operation: str,
        request_fingerprint: str,
        payload_json: str,
        content_type: str,
        retention_class: str,
        checksum: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO raw_records(
                    raw_record_id, run_id, source, endpoint_or_operation,
                    request_fingerprint, payload_json, content_type, received_at,
                    retention_class, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_record_id,
                    run_id,
                    source,
                    endpoint_or_operation,
                    request_fingerprint,
                    payload_json,
                    content_type,
                    utc_now_iso(),
                    retention_class,
                    checksum,
                ),
            )

    def insert_baseline_rows(self, rows: Sequence[Dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO baseline(
                    baseline_id, baseline_name, period_start, period_end, locked_at,
                    locked_by, status, metric_name, segment_json, metric_value,
                    numerator, denominator, source_query_version, input_fingerprint,
                    notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["baseline_id"],
                        row["baseline_name"],
                        row["period_start"],
                        row["period_end"],
                        row.get("locked_at"),
                        row.get("locked_by"),
                        row["status"],
                        row["metric_name"],
                        row["segment_json"],
                        row.get("metric_value"),
                        row.get("numerator"),
                        row.get("denominator"),
                        row["source_query_version"],
                        row["input_fingerprint"],
                        row.get("notes") or "",
                        row["created_at"],
                    )
                    for row in rows
                ],
            )

    def lock_baseline(self, baseline_id: str, *, locked_at: str, locked_by: str) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT status FROM baseline WHERE baseline_id = ? LIMIT 1",
                (baseline_id,),
            ).fetchone()
            if row is None:
                raise SchemaMismatchError(f"Unknown baseline_id {baseline_id}")
            if row["status"] == "locked":
                return
            conn.execute(
                """
                UPDATE baseline
                SET status = 'locked', locked_at = ?, locked_by = ?
                WHERE baseline_id = ? AND status != 'locked'
                """,
                (locked_at, locked_by, baseline_id),
            )

    def insert_opportunities(self, rows: Sequence[Dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO opportunities(
                    opportunity_id, report_id, category, problem,
                    supporting_evidence_json, source_row_references_json,
                    target_query_or_question, target_page, proposed_action, owner,
                    impact_score, impact_estimate, confidence_label, confidence_value,
                    effort_label, effort_value, priority_score, metric_to_watch,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["opportunity_id"],
                        row["report_id"],
                        row["category"],
                        row["problem"],
                        row["supporting_evidence_json"]
                        if isinstance(row["supporting_evidence_json"], str)
                        else json.dumps(row["supporting_evidence_json"]),
                        row["source_row_references_json"]
                        if isinstance(row["source_row_references_json"], str)
                        else json.dumps(row["source_row_references_json"]),
                        row["target_query_or_question"],
                        row["target_page"],
                        row["proposed_action"],
                        row["owner"],
                        float(row["impact_score"]),
                        row["impact_estimate"],
                        row["confidence_label"],
                        float(row["confidence_value"]),
                        row["effort_label"],
                        float(row["effort_value"]),
                        float(row["priority_score"]),
                        row["metric_to_watch"],
                        row["status"],
                        row["created_at"],
                    )
                    for row in rows
                ],
            )

    def insert_opportunities_v2(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return

        def _json(value: Any) -> str:
            if isinstance(value, str):
                return value
            return json.dumps(value if value is not None else {}, sort_keys=True)

        def _json_list(value: Any) -> str:
            if isinstance(value, str):
                return value
            return json.dumps(value if value is not None else [], sort_keys=True)

        values = []
        for row in rows:
            values.append(
                (
                    row["opportunity_id"],
                    row["report_id"],
                    row.get("opportunity_version") or "v2",
                    row["category"],
                    row.get("source_type"),
                    row["problem"],
                    _json(row.get("supporting_evidence_json") or {}),
                    _json_list(row.get("source_row_references_json") or []),
                    row.get("target_query_or_question") or "",
                    row.get("target_page") or "",
                    row.get("target_asset"),
                    row.get("target_page_status"),
                    row["proposed_action"],
                    row.get("action_type"),
                    row.get("cluster_id"),
                    row.get("family_id"),
                    _json_list(row.get("benchmark_ids_json") or []),
                    row.get("expected_incremental_clicks"),
                    row.get("expected_geo_gain"),
                    row.get("estimated_cost"),
                    row.get("cost_currency"),
                    row["owner"],
                    float(row.get("impact_score") or 0),
                    row.get("impact_estimate") or "",
                    row["confidence_label"],
                    float(row["confidence_value"]),
                    row.get("effort_label") or "M",
                    float(row.get("effort_value") or 2.0),
                    float(row.get("priority_score") or 0),
                    row["metric_to_watch"],
                    _json(row.get("measurement_window_json") or {}),
                    _json_list(row.get("assumptions_json") or []),
                    row.get("llm_assessment_id"),
                    row.get("review_status") or "pending",
                    row.get("reviewed_by"),
                    row.get("reviewed_at"),
                    row.get("portfolio_rank"),
                    _json(row.get("priority_inputs_json") or {}),
                    row.get("merge_group_id"),
                    row.get("blocked_reason"),
                    row.get("catalogue_version"),
                    _json(row.get("selection_report_json") or {}),
                    row["status"],
                    row["created_at"],
                    row.get("updated_at") or row["created_at"],
                )
            )
        with self.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
            if "opportunity_version" not in cols:
                # Migration not applied yet — fall back to v1 insert shape.
                self.insert_opportunities(
                    [
                        {
                            **row,
                            "supporting_evidence_json": row.get("supporting_evidence_json") or {},
                            "source_row_references_json": row.get("source_row_references_json") or [],
                        }
                        for row in rows
                    ]
                )
                return
            conn.executemany(
                """
                INSERT INTO opportunities(
                    opportunity_id, report_id, opportunity_version, category, source_type, problem,
                    supporting_evidence_json, source_row_references_json,
                    target_query_or_question, target_page, target_asset, target_page_status,
                    proposed_action, action_type, cluster_id, family_id, benchmark_ids_json,
                    expected_incremental_clicks, expected_geo_gain, estimated_cost, cost_currency,
                    owner, impact_score, impact_estimate, confidence_label, confidence_value,
                    effort_label, effort_value, priority_score, metric_to_watch,
                    measurement_window_json, assumptions_json, llm_assessment_id,
                    review_status, reviewed_by, reviewed_at, portfolio_rank, priority_inputs_json,
                    merge_group_id, blocked_reason, catalogue_version, selection_report_json,
                    status, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                values,
            )

    def update_opportunity(self, opportunity_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "review_status",
            "reviewed_by",
            "reviewed_at",
            "llm_assessment_id",
            "blocked_reason",
            "selection_report_json",
            "assumptions_json",
            "priority_score",
            "priority_inputs_json",
            "updated_at",
            "proposed_action",
            "action_type",
            "estimated_cost",
            "cost_currency",
        }
        json_fields = {
            "selection_report_json",
            "assumptions_json",
            "priority_inputs_json",
        }
        assignments = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise SchemaMismatchError(f"Unsupported opportunity field: {key}")
            if key in json_fields and value is not None and not isinstance(value, str):
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(opportunity_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE opportunities SET {', '.join(assignments)} WHERE opportunity_id = ?",
                values,
            )

    def upsert_weekly_report(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO weekly_reports(
                    report_id, period_start, period_end, baseline_id, status,
                    summary_json, quality_status, opportunity_count, lark_record_id,
                    created_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_start, period_end) DO UPDATE SET
                    report_id = excluded.report_id,
                    baseline_id = excluded.baseline_id,
                    status = excluded.status,
                    summary_json = excluded.summary_json,
                    quality_status = excluded.quality_status,
                    opportunity_count = excluded.opportunity_count,
                    lark_record_id = excluded.lark_record_id,
                    published_at = excluded.published_at
                """,
                (
                    row["report_id"],
                    row["period_start"],
                    row["period_end"],
                    row.get("baseline_id"),
                    row["status"],
                    row["summary_json"],
                    row["quality_status"],
                    int(row["opportunity_count"]),
                    row.get("lark_record_id"),
                    row["created_at"],
                    row.get("published_at"),
                ),
            )

    def insert_alert(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO alerts(
                    alert_id, run_id, severity, alert_type, summary, details_redacted,
                    status, attempts, created_at, sent_at, external_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["alert_id"],
                    row["run_id"],
                    row["severity"],
                    row["alert_type"],
                    row["summary"],
                    row["details_redacted"],
                    row["status"],
                    int(row.get("attempts") or 0),
                    row["created_at"],
                    row.get("sent_at"),
                    row.get("external_reference"),
                ),
            )

    def update_alert(
        self,
        alert_id: str,
        *,
        status: Optional[str] = None,
        attempts: Optional[int] = None,
        sent_at: Optional[str] = None,
        external_reference: Optional[str] = None,
    ) -> None:
        assignments = []
        values: List[Any] = []
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if attempts is not None:
            assignments.append("attempts = ?")
            values.append(attempts)
        if sent_at is not None:
            assignments.append("sent_at = ?")
            values.append(sent_at)
        if external_reference is not None:
            assignments.append("external_reference = ?")
            values.append(external_reference)
        if not assignments:
            return
        values.append(alert_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE alerts SET {', '.join(assignments)} WHERE alert_id = ?",
                values,
            )

    def insert_catalogue_build(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO catalogue_builds(
                    build_id, build_type, status, source_window_start, source_window_end,
                    gsc_source_run_ids, ga4_source_run_ids, serper_source_run_ids,
                    source_fingerprint, methodology_version, created_by, created_at,
                    approved_by, approved_at, activated_at, notes, funnel_json,
                    ga4_window_start, ga4_window_end, parent_build_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["build_id"],
                    row["build_type"],
                    row["status"],
                    row["source_window_start"],
                    row["source_window_end"],
                    json.dumps(row.get("gsc_source_run_ids") or [], sort_keys=True),
                    json.dumps(row.get("ga4_source_run_ids") or [], sort_keys=True),
                    json.dumps(row.get("serper_source_run_ids") or [], sort_keys=True),
                    row["source_fingerprint"],
                    row["methodology_version"],
                    row["created_by"],
                    row["created_at"],
                    row.get("approved_by"),
                    row.get("approved_at"),
                    row.get("activated_at"),
                    row.get("notes"),
                    json.dumps(row.get("funnel_json") or {}, sort_keys=True),
                    row.get("ga4_window_start"),
                    row.get("ga4_window_end"),
                    row.get("parent_build_id"),
                ),
            )

    def insert_keyword_candidates(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = []
        for row in rows:
            values.append(
                (
                    row["candidate_id"],
                    row["build_id"],
                    row["canonical_keyword"],
                    row["normalized_keyword"],
                    row["brand_status"],
                    row["brand_rule_version"],
                    row["primary_observed_page"],
                    json.dumps(row.get("observed_pages_json") or [], sort_keys=True),
                    int(row.get("multi_page_competition") or 0),
                    int(row["source_query_count"]),
                    int(row["source_row_count"]),
                    int(row["source_date_count"]),
                    int(row["gsc_clicks"]),
                    int(row["gsc_impressions"]),
                    row.get("gsc_weighted_ctr"),
                    row.get("gsc_weighted_position"),
                    row.get("ga4_organic_sessions"),
                    row.get("ga4_engaged_sessions"),
                    row.get("ga4_purchases"),
                    row.get("ga4_revenue"),
                    row.get("ga4_conversion_rate"),
                    row.get("serper_position"),
                    row.get("serper_ranking_url"),
                    json.dumps(row["serper_top_10_domains"], sort_keys=True)
                    if row.get("serper_top_10_domains") is not None
                    else None,
                    row.get("serper_intent"),
                    row.get("business_relevance_score"),
                    row.get("gsc_opportunity_score"),
                    row.get("ga4_value_score"),
                    row.get("serper_validation_score"),
                    row.get("evidence_confidence_score"),
                    row.get("final_selection_score"),
                    row["decision"],
                    row.get("decision_reason"),
                    row.get("proposed_target_page"),
                    row.get("reviewed_target_page"),
                    row.get("reviewed_by"),
                    row.get("reviewed_at"),
                    row["created_at"],
                    row["updated_at"],
                )
            )
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO keyword_candidates(
                    candidate_id, build_id, canonical_keyword, normalized_keyword,
                    brand_status, brand_rule_version, primary_observed_page, observed_pages_json,
                    multi_page_competition, source_query_count, source_row_count, source_date_count,
                    gsc_clicks, gsc_impressions, gsc_weighted_ctr, gsc_weighted_position,
                    ga4_organic_sessions, ga4_engaged_sessions, ga4_purchases, ga4_revenue,
                    ga4_conversion_rate, serper_position, serper_ranking_url, serper_top_10_domains,
                    serper_intent, business_relevance_score, gsc_opportunity_score, ga4_value_score,
                    serper_validation_score, evidence_confidence_score, final_selection_score,
                    decision, decision_reason, proposed_target_page, reviewed_target_page,
                    reviewed_by, reviewed_at, created_at, updated_at
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                values,
            )

    def insert_keyword_candidate_sources(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [
            (
                row["candidate_source_id"],
                row["candidate_id"],
                row["gsc_natural_key"],
                row["gsc_run_id"],
                row["raw_query"],
                row["raw_page"],
                int(row["clicks"]),
                int(row["impressions"]),
                float(row["ctr"]),
                float(row["position"]),
                row["row_date"],
                row["transformation_method"],
                row["created_at"],
            )
            for row in rows
        ]
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO keyword_candidate_sources(
                    candidate_source_id, candidate_id, gsc_natural_key, gsc_run_id,
                    raw_query, raw_page, clicks, impressions, ctr, position,
                    row_date, transformation_method, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def update_keyword_candidate(self, candidate_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "ga4_organic_sessions",
            "ga4_engaged_sessions",
            "ga4_purchases",
            "ga4_revenue",
            "ga4_conversion_rate",
            "ga4_engagement_rate",
            "ga4_match_status",
            "ga4_match_page",
            "ga4_shared_page",
            "ga4_value_score",
            "page_type",
            "cluster_id",
            "serper_position",
            "serper_ranking_url",
            "serper_top_10_domains",
            "serper_intent",
            "serper_validation_score",
            "serper_run_id",
            "serper_collected_at",
            "proposed_target_ranks",
            "serper_ai_overview_status",
            "serper_result_types_json",
            "business_relevance_score",
            "gsc_opportunity_score",
            "evidence_confidence_score",
            "final_selection_score",
            "decision",
            "decision_reason",
            "proposed_target_page",
            "reviewed_target_page",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
            "methodology_version",
            "routing_bucket",
            "brand_status",
            "brand_match_type",
            "brand_confidence",
            "competitor_status",
            "competitor_name",
            "search_intent",
            "strategic_lane",
            "business_relevance_status",
            "business_relevance_reason",
            "claims_review_required",
            "family_id",
            "family_role",
            "family_method",
            "family_confidence",
            "target_page_status",
            "target_page_confidence",
            "intent_fit_score",
            "target_actionability_score",
            "serp_opportunity_score",
            "incremental_coverage_score",
            "duplicate_penalty",
            "selection_score_v2",
            "selection_rank_within_lane",
            "eligibility_status",
            "eligibility_reasons_json",
            "selection_reasons_json",
            "alternate_rank",
            "proposed_action",
            "ga4_confidence_multiplier",
            "multi_page_class",
            "gsc_protection_score",
            "gsc_opportunity_score_v2",
            "product_need_relevance_score",
            "serp_visibility_score",
            "serp_target_alignment_score",
            "serp_validation_confidence",
            "serp_feasibility_score",
            "available_evidence_weight",
            "missing_evidence_fields_json",
            "score_confidence",
            "serp_preselected",
            "serp_preselect_rank",
            "portfolio_slot",
            "review_group",
            "llm_assessment_id",
            "llm_review_stage",
            "llm_customer_need",
            "llm_intent",
            "llm_business_relevance",
            "llm_business_relevance_rationale",
            "llm_family_key",
            "llm_is_representative",
            "llm_semantic_duplicates_json",
            "llm_recommended_target_page",
            "llm_no_suitable_target",
            "llm_actionability",
            "llm_actionability_rationale",
            "semantic_authority",
            "llm_pool_assessment_id",
            "llm_semantic_confidence",
        }
        assignments = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise SchemaMismatchError(f"Unsupported keyword_candidate field: {key}")
            if key in {
                "serper_top_10_domains",
                "serper_result_types_json",
                "eligibility_reasons_json",
                "selection_reasons_json",
                "missing_evidence_fields_json",
                "llm_semantic_duplicates_json",
            } and value is not None:
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(candidate_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE keyword_candidates SET {', '.join(assignments)} WHERE candidate_id = ?",
                values,
            )

    def update_catalogue_build(self, build_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "gsc_source_run_ids",
            "ga4_source_run_ids",
            "serper_source_run_ids",
            "ga4_window_start",
            "ga4_window_end",
            "ga4_match_report_json",
            "serper_validation_report_json",
            "funnel_json",
            "notes",
            "approved_by",
            "approved_at",
            "activated_at",
            "parent_build_id",
            "selection_policy_version",
            "selection_policy_json",
            "routing_report_json",
            "family_report_json",
            "preselection_report_json",
            "portfolio_report_json",
            "quality_exceptions_json",
        }
        json_fields = {
            "gsc_source_run_ids",
            "ga4_source_run_ids",
            "serper_source_run_ids",
            "ga4_match_report_json",
            "serper_validation_report_json",
            "funnel_json",
            "selection_policy_json",
            "routing_report_json",
            "family_report_json",
            "preselection_report_json",
            "portfolio_report_json",
            "quality_exceptions_json",
        }
        assignments = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise SchemaMismatchError(f"Unsupported catalogue_build field: {key}")
            if key in json_fields and value is not None and not isinstance(value, str):
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(build_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE catalogue_builds SET {', '.join(assignments)} WHERE build_id = ?",
                values,
            )

    def count(self, table: str) -> int:
        if table not in {
            "gsc_daily",
            "ga4_daily",
            "serp_daily",
            "ai_answer_runs",
            "keyword_catalog",
            "ai_question_catalog",
            "quality_check_log",
            "run_log",
            "baseline",
            "opportunities",
            "weekly_reports",
            "alerts",
            "raw_records",
            "catalogue_builds",
            "keyword_candidates",
            "keyword_candidate_sources",
            "catalogue_clusters",
            "ai_question_candidates",
            "ai_question_sources",
            "catalogue_comparisons",
            "keyword_families",
            "llm_assessments",
        }:
            raise SchemaMismatchError(f"Unknown table {table}")
        with self.connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            return int(row["n"])

    def delete_keyword_families_for_build(self, build_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM keyword_families WHERE build_id = ?", (build_id,))

    def insert_keyword_families(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = []
        for row in rows:
            values.append(
                (
                    row["family_id"],
                    row["build_id"],
                    row["family_key"],
                    row["family_label"],
                    row.get("primary_candidate_id"),
                    row.get("routing_bucket"),
                    row.get("search_intent"),
                    row.get("strategic_lane"),
                    json.dumps(row.get("member_candidate_ids_json") or [], sort_keys=True),
                    int(row.get("member_count") or 0),
                    int(row.get("family_gsc_clicks") or 0),
                    int(row.get("family_gsc_impressions") or 0),
                    row.get("family_weighted_ctr"),
                    row.get("family_weighted_position"),
                    row.get("primary_target_page"),
                    row["family_method"],
                    row.get("family_confidence"),
                    row.get("approval_status") or "draft",
                    row.get("reviewed_by"),
                    row.get("reviewed_at"),
                    row["created_at"],
                    row["updated_at"],
                )
            )
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO keyword_families(
                    family_id, build_id, family_key, family_label, primary_candidate_id,
                    routing_bucket, search_intent, strategic_lane, member_candidate_ids_json,
                    member_count, family_gsc_clicks, family_gsc_impressions, family_weighted_ctr,
                    family_weighted_position, primary_target_page, family_method, family_confidence,
                    approval_status, reviewed_by, reviewed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def insert_catalogue_cluster(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO catalogue_clusters(
                    cluster_id, build_id, cluster_name, primary_intent, primary_target_page,
                    member_candidate_ids, rationale, method, reviewed_by, reviewed_at, created_at,
                    approval_status, supporting_pages_json, gsc_clicks, gsc_impressions,
                    ga4_sessions, ga4_purchases, ga4_revenue
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["cluster_id"],
                    row["build_id"],
                    row["cluster_name"],
                    row["primary_intent"],
                    row["primary_target_page"],
                    json.dumps(row.get("member_candidate_ids") or [], sort_keys=True),
                    row["rationale"],
                    row["method"],
                    row.get("reviewed_by"),
                    row.get("reviewed_at"),
                    row["created_at"],
                    row.get("approval_status") or "draft",
                    json.dumps(row.get("supporting_pages_json") or [], sort_keys=True),
                    int(row.get("gsc_clicks") or 0),
                    int(row.get("gsc_impressions") or 0),
                    row.get("ga4_sessions"),
                    row.get("ga4_purchases"),
                    row.get("ga4_revenue"),
                ),
            )

    def insert_ai_question_candidates(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [
            (
                row["question_candidate_id"],
                row["build_id"],
                row["question"],
                row.get("cluster_id"),
                row["intent"],
                row["proposed_target_page"],
                row["transformation_method"],
                json.dumps(row.get("source_keyword_ids") or [], sort_keys=True),
                json.dumps(row.get("source_candidate_ids") or [], sort_keys=True),
                row["decision"],
                row.get("decision_reason"),
                row.get("reviewed_by"),
                row.get("reviewed_at"),
                row["created_at"],
                row["updated_at"],
                row.get("source_type"),
                row.get("source_reference"),
                json.dumps(row.get("source_evidence_refs_json") or [], sort_keys=True),
                row.get("persona"),
                row.get("situation"),
                row.get("decision_to_make"),
                json.dumps(row.get("constraints_json") or [], sort_keys=True),
                json.dumps(row.get("expected_answer_elements_json") or [], sort_keys=True),
                row.get("naturalness_status") or "pending",
                row.get("distinctness_status") or "pending",
                row.get("safety_status") or "pending",
                row.get("pilot_status") or "not_run",
                json.dumps(row.get("pilot_results_json") or {}, sort_keys=True),
                row.get("pilot_override_by"),
                row.get("pilot_override_at"),
                row.get("pilot_override_reason"),
                row.get("llm_assessment_id"),
                row.get("review_stage") or "evidence_ready",
                row.get("family_bucket"),
                int(row.get("human_validated_hypothesis") or 0),
            )
            for row in rows
        ]
        with self.connection() as conn:
            # Detect whether Phase 14 columns exist (pre-migration test DBs).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_question_candidates)").fetchall()}
            if "source_type" in cols:
                conn.executemany(
                    """
                    INSERT INTO ai_question_candidates(
                        question_candidate_id, build_id, question, cluster_id, intent,
                        proposed_target_page, transformation_method, source_keyword_ids,
                        source_candidate_ids, decision, decision_reason, reviewed_by,
                        reviewed_at, created_at, updated_at,
                        source_type, source_reference, source_evidence_refs_json,
                        persona, situation, decision_to_make, constraints_json,
                        expected_answer_elements_json, naturalness_status, distinctness_status,
                        safety_status, pilot_status, pilot_results_json, pilot_override_by,
                        pilot_override_at, pilot_override_reason, llm_assessment_id,
                        review_stage, family_bucket, human_validated_hypothesis
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                conn.executemany(
                    """
                    INSERT INTO ai_question_candidates(
                        question_candidate_id, build_id, question, cluster_id, intent,
                        proposed_target_page, transformation_method, source_keyword_ids,
                        source_candidate_ids, decision, decision_reason, reviewed_by,
                        reviewed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [v[:15] for v in values],
                )

    def insert_ai_question_sources(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [
            (
                row["question_source_id"],
                row["question_candidate_id"],
                row.get("keyword_id"),
                row["candidate_id"],
                row["gsc_natural_key"],
                row["gsc_run_id"],
                row["raw_gsc_query"],
                row["relationship"],
                row["created_at"],
            )
            for row in rows
        ]
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO ai_question_sources(
                    question_source_id, question_candidate_id, keyword_id, candidate_id,
                    gsc_natural_key, gsc_run_id, raw_gsc_query, relationship, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def update_ai_question_candidate(self, question_candidate_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "question",
            "cluster_id",
            "intent",
            "proposed_target_page",
            "transformation_method",
            "decision",
            "decision_reason",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
            "source_keyword_ids",
            "source_candidate_ids",
            "source_type",
            "source_reference",
            "source_evidence_refs_json",
            "persona",
            "situation",
            "decision_to_make",
            "constraints_json",
            "expected_answer_elements_json",
            "naturalness_status",
            "distinctness_status",
            "safety_status",
            "pilot_status",
            "pilot_results_json",
            "pilot_override_by",
            "pilot_override_at",
            "pilot_override_reason",
            "llm_assessment_id",
            "review_stage",
            "family_bucket",
            "human_validated_hypothesis",
        }
        json_fields = {
            "source_keyword_ids",
            "source_candidate_ids",
            "source_evidence_refs_json",
            "constraints_json",
            "expected_answer_elements_json",
            "pilot_results_json",
        }
        assignments = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise SchemaMismatchError(f"Unsupported ai_question_candidate field: {key}")
            if key in json_fields and value is not None:
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(question_candidate_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE ai_question_candidates SET {', '.join(assignments)} WHERE question_candidate_id = ?",
                values,
            )

    def insert_llm_assessment(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_assessments(
                    assessment_id, assessment_type, subject_type, subject_id, build_id,
                    prompt_version, provider, model, input_fingerprint,
                    input_evidence_refs_json, redacted_input_json, output_json,
                    validation_status, validation_errors_json, cost_usd, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assessment_type, subject_type, subject_id, prompt_version, input_fingerprint)
                DO UPDATE SET
                    assessment_id = excluded.assessment_id,
                    build_id = excluded.build_id,
                    provider = excluded.provider,
                    model = excluded.model,
                    input_evidence_refs_json = excluded.input_evidence_refs_json,
                    redacted_input_json = excluded.redacted_input_json,
                    output_json = excluded.output_json,
                    validation_status = excluded.validation_status,
                    validation_errors_json = excluded.validation_errors_json,
                    cost_usd = excluded.cost_usd,
                    latency_ms = excluded.latency_ms,
                    created_at = excluded.created_at
                """,
                (
                    row["assessment_id"],
                    row["assessment_type"],
                    row["subject_type"],
                    row["subject_id"],
                    row.get("build_id"),
                    row["prompt_version"],
                    row["provider"],
                    row["model"],
                    row["input_fingerprint"],
                    json.dumps(row.get("input_evidence_refs_json") or [], sort_keys=True),
                    json.dumps(row.get("redacted_input_json") or {}, sort_keys=True),
                    json.dumps(row.get("output_json"), sort_keys=True)
                    if row.get("output_json") is not None
                    else None,
                    row["validation_status"],
                    json.dumps(row.get("validation_errors_json") or [], sort_keys=True),
                    float(row.get("cost_usd") or 0),
                    row.get("latency_ms"),
                    row["created_at"],
                ),
            )

    def insert_catalogue_comparisons(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [
            (
                row["comparison_id"],
                row["build_id"],
                row["provisional_type"],
                row["provisional_id"],
                row["provisional_text"],
                row.get("provisional_cluster"),
                row.get("provisional_target_page"),
                row["decision"],
                row["decision_reason"],
                row.get("matched_candidate_id"),
                row.get("matched_question_id"),
                json.dumps(row.get("source_references_json") or [], sort_keys=True),
                row["created_at"],
            )
            for row in rows
        ]
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO catalogue_comparisons(
                    comparison_id, build_id, provisional_type, provisional_id, provisional_text,
                    provisional_cluster, provisional_target_page, decision, decision_reason,
                    matched_candidate_id, matched_question_id, source_references_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    # --- Phase 16 experiments ---

    def insert_experiment(self, row: Dict[str, Any]) -> None:
        def _json(value: Any, default: Any) -> str:
            if isinstance(value, str):
                return value
            return json.dumps(value if value is not None else default, sort_keys=True)

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO seo_experiments(
                    experiment_id, opportunity_id, supersedes_experiment_id, status, hypothesis,
                    action_type, target_page, target_queries_json, control_pages_json,
                    primary_metric, guardrail_metrics_json, approved_by, approved_at, owner,
                    planned_publish_at, published_at, baseline_start, baseline_end,
                    estimated_incremental_clicks, estimated_cost, actual_cost, cost_currency,
                    counterfactual_method, content_before_hash, content_after_hash,
                    implementation_reference, catalogue_version, source_report_id,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    row["experiment_id"],
                    row["opportunity_id"],
                    row.get("supersedes_experiment_id"),
                    row["status"],
                    row["hypothesis"],
                    row["action_type"],
                    row["target_page"],
                    _json(row.get("target_queries_json"), []),
                    _json(row.get("control_pages_json"), []),
                    row["primary_metric"],
                    _json(row.get("guardrail_metrics_json"), []),
                    row["approved_by"],
                    row["approved_at"],
                    row["owner"],
                    row.get("planned_publish_at"),
                    row.get("published_at"),
                    row.get("baseline_start"),
                    row.get("baseline_end"),
                    row.get("estimated_incremental_clicks"),
                    row.get("estimated_cost"),
                    float(row.get("actual_cost") or 0),
                    row["cost_currency"],
                    row.get("counterfactual_method") or "sitewide_adjusted",
                    row.get("content_before_hash"),
                    row.get("content_after_hash"),
                    row.get("implementation_reference"),
                    row.get("catalogue_version"),
                    row["source_report_id"],
                    _json(row.get("metadata_json"), {}),
                    row["created_at"],
                    row["updated_at"],
                ),
            )

    def get_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        rows = self.fetchall(
            "SELECT * FROM seo_experiments WHERE experiment_id = ?",
            (experiment_id,),
        )
        return dict(rows[0]) if rows else None

    def update_experiment(self, experiment_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "published_at",
            "baseline_start",
            "baseline_end",
            "actual_cost",
            "cost_currency",
            "counterfactual_method",
            "content_before_hash",
            "content_after_hash",
            "implementation_reference",
            "metadata_json",
            "planned_publish_at",
            "updated_at",
        }
        json_fields = {"metadata_json"}
        assignments = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise SchemaMismatchError(f"Unsupported experiment field: {key}")
            if key in json_fields and value is not None and not isinstance(value, str):
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(experiment_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE seo_experiments SET {', '.join(assignments)} WHERE experiment_id = ?",
                values,
            )

    def insert_experiment_changes(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        values = [
            (
                row["change_id"],
                row["experiment_id"],
                row["change_type"],
                row["target_asset"],
                row.get("before_value"),
                row.get("after_value"),
                row.get("evidence_reference"),
                row.get("implemented_by"),
                row["implemented_at"],
                row["created_at"],
            )
            for row in rows
        ]
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO experiment_changes(
                    change_id, experiment_id, change_type, target_asset, before_value,
                    after_value, evidence_reference, implemented_by, implemented_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def list_experiment_changes(self, experiment_id: str) -> List[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM experiment_changes WHERE experiment_id = ? ORDER BY implemented_at",
            (experiment_id,),
        )

    def insert_experiment_cost(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO experiment_costs(
                    cost_id, experiment_id, cost_type, quantity, unit_cost, amount,
                    currency, incurred_at, evidence_reference, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["cost_id"],
                    row["experiment_id"],
                    row["cost_type"],
                    float(row["quantity"]),
                    float(row["unit_cost"]),
                    float(row["amount"]),
                    row["currency"],
                    row["incurred_at"],
                    row.get("evidence_reference"),
                    row.get("notes"),
                    row["created_at"],
                ),
            )

    def list_experiment_costs(self, experiment_id: str) -> List[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM experiment_costs WHERE experiment_id = ? ORDER BY incurred_at",
            (experiment_id,),
        )

    def recalculate_experiment_actual_cost(self, experiment_id: str) -> float:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM experiment_costs WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            total = float(row["total"] or 0)
            conn.execute(
                "UPDATE seo_experiments SET actual_cost = ?, updated_at = ? WHERE experiment_id = ?",
                (total, utc_now_iso(), experiment_id),
            )
            return total

    def upsert_experiment_measurement(self, row: Dict[str, Any]) -> None:
        def _json(value: Any, default: Any) -> str:
            if isinstance(value, str):
                return value
            return json.dumps(value if value is not None else default, sort_keys=True)

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO experiment_measurements(
                    measurement_id, experiment_id, checkpoint_days, window_start, window_end,
                    baseline_clicks, observed_clicks, expected_clicks_without_change,
                    adjusted_incremental_clicks, sitewide_trend_factor, control_trend_factor,
                    nonbranded_impressions, ctr, average_position, organic_sessions,
                    engaged_sessions, purchases, revenue, ai_referral_sessions,
                    confidence_label, outcome, adjustment_method, source_references_json,
                    assumptions_json, quality_status, calculated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(experiment_id, checkpoint_days) DO UPDATE SET
                    measurement_id = excluded.measurement_id,
                    window_start = excluded.window_start,
                    window_end = excluded.window_end,
                    baseline_clicks = excluded.baseline_clicks,
                    observed_clicks = excluded.observed_clicks,
                    expected_clicks_without_change = excluded.expected_clicks_without_change,
                    adjusted_incremental_clicks = excluded.adjusted_incremental_clicks,
                    sitewide_trend_factor = excluded.sitewide_trend_factor,
                    control_trend_factor = excluded.control_trend_factor,
                    nonbranded_impressions = excluded.nonbranded_impressions,
                    ctr = excluded.ctr,
                    average_position = excluded.average_position,
                    organic_sessions = excluded.organic_sessions,
                    engaged_sessions = excluded.engaged_sessions,
                    purchases = excluded.purchases,
                    revenue = excluded.revenue,
                    ai_referral_sessions = excluded.ai_referral_sessions,
                    confidence_label = excluded.confidence_label,
                    outcome = excluded.outcome,
                    adjustment_method = excluded.adjustment_method,
                    source_references_json = excluded.source_references_json,
                    assumptions_json = excluded.assumptions_json,
                    quality_status = excluded.quality_status,
                    calculated_at = excluded.calculated_at
                """,
                (
                    row["measurement_id"],
                    row["experiment_id"],
                    int(row["checkpoint_days"]),
                    row["window_start"],
                    row["window_end"],
                    row.get("baseline_clicks"),
                    row.get("observed_clicks"),
                    row.get("expected_clicks_without_change"),
                    row.get("adjusted_incremental_clicks"),
                    row.get("sitewide_trend_factor"),
                    row.get("control_trend_factor"),
                    row.get("nonbranded_impressions"),
                    row.get("ctr"),
                    row.get("average_position"),
                    row.get("organic_sessions"),
                    row.get("engaged_sessions"),
                    row.get("purchases"),
                    row.get("revenue"),
                    row.get("ai_referral_sessions"),
                    row["confidence_label"],
                    row["outcome"],
                    row["adjustment_method"],
                    _json(row.get("source_references_json"), []),
                    _json(row.get("assumptions_json"), []),
                    row["quality_status"],
                    row["calculated_at"],
                ),
            )

    def get_experiment_measurement(
        self, experiment_id: str, checkpoint_days: int
    ) -> Optional[Dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT * FROM experiment_measurements
            WHERE experiment_id = ? AND checkpoint_days = ?
            """,
            (experiment_id, int(checkpoint_days)),
        )
        return dict(rows[0]) if rows else None

    def list_experiment_measurements(self, experiment_id: str) -> List[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT * FROM experiment_measurements
            WHERE experiment_id = ?
            ORDER BY checkpoint_days
            """,
            (experiment_id,),
        )

    def upsert_action_type_prior(self, row: Dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO action_type_priors(
                    action_type, completed_experiments, wins, losses, inconclusive,
                    median_incremental_clicks, median_cost_per_incremental_click,
                    empirical_confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_type) DO UPDATE SET
                    completed_experiments = excluded.completed_experiments,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    inconclusive = excluded.inconclusive,
                    median_incremental_clicks = excluded.median_incremental_clicks,
                    median_cost_per_incremental_click = excluded.median_cost_per_incremental_click,
                    empirical_confidence = excluded.empirical_confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    row["action_type"],
                    int(row.get("completed_experiments") or 0),
                    int(row.get("wins") or 0),
                    int(row.get("losses") or 0),
                    int(row.get("inconclusive") or 0),
                    row.get("median_incremental_clicks"),
                    row.get("median_cost_per_incremental_click"),
                    row.get("empirical_confidence"),
                    row["updated_at"],
                ),
            )

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute(sql, params))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self.connection() as conn:
            conn.execute(sql, params)

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> None:
        with self.connection() as conn:
            conn.executemany(sql, list(params))

    def _upsert(
        self,
        table: str,
        keys: Sequence[str],
        hashes: Sequence[str],
        sql: str,
        values: Sequence[Sequence[Any]],
    ) -> UpsertStats:
        stats = UpsertStats()
        if not values:
            return stats
        with self.connection() as conn:
            existing = {}
            if keys:
                placeholders = ",".join("?" * len(keys))
                for row in conn.execute(
                    f"SELECT natural_key, row_hash FROM {table} WHERE natural_key IN ({placeholders})",
                    list(keys),
                ):
                    existing[row["natural_key"]] = row["row_hash"]
            conn.executemany(sql, list(values))
            for key, digest in zip(keys, hashes):
                if key not in existing:
                    stats.inserted += 1
                elif existing[key] == digest:
                    stats.unchanged += 1
                else:
                    stats.updated += 1
        return stats
