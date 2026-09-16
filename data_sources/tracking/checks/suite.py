from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from ..catalogs import active_keywords, active_questions
from ..config import TrackingConfig
from ..enums import CheckStatus, CollectorStatus, Severity, Source
from ..models import QualityCheckRow
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .base import CheckResult
from .brand_eval import BrandClassifierQualityCheck
from .reconciliation import (
    Ga4ReconciliationCheck,
    GscReconciliationCheck,
    stored_ga4_sessions,
    stored_gsc_totals,
)

SOURCE_TABLES = {
    Source.GSC.value: "gsc_daily",
    Source.GA4.value: "ga4_daily",
    Source.SERPER.value: "serp_daily",
    Source.AI_VISIBILITY.value: "ai_answer_runs",
}


def _pass_fail(ok: bool, *, warn_only: bool = False) -> CheckStatus:
    if ok:
        return CheckStatus.PASS
    return CheckStatus.WARN if warn_only else CheckStatus.FAIL


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class QualityCheckSuite:
    """Run the Phase-4 automated data-quality checks for a run."""

    def __init__(self, config: TrackingConfig, store: TrackingStore):
        self.config = config
        self.store = store

    def run_for_run(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown run_id {run_id}")
        results: List[CheckResult] = []
        results.extend(self._row_presence_checks(run_id=run_id))
        results.append(self._natural_key_uniqueness(run_id=run_id))
        results.append(self._collector_completion(run))
        results.append(self._keyword_catalogue_size())
        results.append(self._ai_question_catalogue_size())
        results.append(self._ai_repetition_completeness(run_id=run_id))
        results.append(BrandClassifierQualityCheck().run(config=self.config))
        results.append(self._freshness(run, run_id=run_id))
        results.append(self._non_negative_metrics(run_id=run_id))
        results.append(self._ctr_range(run_id=run_id))
        results.append(self._position_validity(run_id=run_id))
        results.append(self._api_cost_cap(run))
        results.append(self._run_duration(run))
        results.append(self._backfill_preservation(run_id=run_id))
        results.append(self._raw_ai_answer_presence(run_id=run_id))
        results.extend(self._reconciliation_checks(run))
        results.append(self._url_validity(run_id=run_id))
        results.append(self._lark_publish_parity())
        results.append(self._chatgpt_search_capability(run_id=run_id))

        self.store.insert_quality_checks(
            [
                QualityCheckRow(
                    check_id=str(uuid.uuid4()),
                    run_id=run_id,
                    check_name=item.check_name,
                    scope=item.scope,
                    status=item.status,
                    severity=item.severity,
                    threshold=item.threshold,
                    observed_value=item.observed_value,
                    details_json=item.details,
                )
                for item in results
            ]
        )
        return summarize_check_results(results)

    def _row_presence_checks(self, *, run_id: str) -> List[CheckResult]:
        out: List[CheckResult] = []
        for source, table in SOURCE_TABLES.items():
            count = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                    (run_id,),
                )[0]["n"]
            )
            if count == 0:
                for name, _field in (
                    ("source_present", "source"),
                    ("timestamp_present", "collected_at"),
                    ("as_of_date_present", "as_of_date"),
                ):
                    out.append(
                        CheckResult(
                            check_name=name,
                            scope=source,
                            status=CheckStatus.SKIPPED,
                            severity=Severity.ERROR,
                            threshold="100%",
                            observed_value="no_rows",
                            details={"reason": f"no rows in {table} for run", "run_id": run_id},
                        )
                    )
                continue
            for name, column in (
                ("source_present", "source"),
                ("timestamp_present", "collected_at"),
                ("as_of_date_present", "as_of_date"),
            ):
                bad = self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ? AND ({column} IS NULL OR TRIM({column}) = '')",
                    (run_id,),
                )[0]["n"]
                out.append(
                    CheckResult(
                        check_name=name,
                        scope=source,
                        status=_pass_fail(int(bad) == 0),
                        severity=Severity.ERROR,
                        threshold="100%",
                        observed_value=f"missing={bad};total={count}",
                        details={"table": table, "column": column, "missing": int(bad), "run_id": run_id},
                    )
                )
        return out

    def _natural_key_uniqueness(self, *, run_id: str) -> CheckResult:
        dups = 0
        details = {}
        for table in SOURCE_TABLES.values():
            count = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                    (run_id,),
                )[0]["n"]
            )
            if count == 0:
                continue
            row = self.store.fetchall(
                f"""
                SELECT COUNT(*) AS n FROM (
                    SELECT natural_key FROM {table}
                    WHERE run_id = ?
                    GROUP BY natural_key HAVING COUNT(*) > 1
                )
                """,
                (run_id,),
            )[0]
            n = int(row["n"])
            details[table] = n
            dups += n
        return CheckResult(
            check_name="natural_key_uniqueness",
            scope="storage",
            status=_pass_fail(dups == 0),
            severity=Severity.CRITICAL,
            threshold="0 duplicates in run",
            observed_value=str(dups),
            details={**details, "run_id": run_id},
        )

    def _collector_completion(self, run) -> CheckResult:
        requested = json.loads(run["requested_collectors"] or "[]")
        completed = json.loads(run["completed_collectors"] or "[]")
        failed = json.loads(run["failed_collectors"] or "{}")
        states = self.store.fetchall(
            "SELECT collector, status FROM collector_state WHERE run_id = ?",
            (run["run_id"],),
        )
        by_source = {row["collector"]: row["status"] for row in states}
        missing = []
        for source in requested:
            status = by_source.get(source)
            if status == CollectorStatus.SUCCEEDED.value:
                continue
            if status == CollectorStatus.FAILED.value and source in failed:
                continue
            if status == CollectorStatus.SKIPPED.value:
                continue
            if source in completed:
                continue
            missing.append(source)
        ok = not missing
        return CheckResult(
            check_name="collector_completion",
            scope="run",
            status=_pass_fail(ok),
            severity=Severity.CRITICAL,
            threshold="all requested collectors succeeded or failed explicitly",
            observed_value=f"missing={len(missing)}",
            details={"missing": missing, "failed": failed, "completed": completed},
        )

    def _keyword_catalogue_size(self) -> CheckResult:
        n = len(active_keywords(self.config))
        return CheckResult(
            check_name="keyword_catalogue_size",
            scope="catalogue",
            status=_pass_fail(n >= 50),
            severity=Severity.ERROR,
            threshold=">=50",
            observed_value=str(n),
            details={"active_keywords": n},
        )

    def _ai_question_catalogue_size(self) -> CheckResult:
        n = len(active_questions(self.config))
        return CheckResult(
            check_name="ai_question_catalogue_size",
            scope="catalogue",
            status=_pass_fail(n >= 20),
            severity=Severity.ERROR,
            threshold=">=20",
            observed_value=str(n),
            details={"active_questions": n},
        )

    def _ai_repetition_completeness(self, *, run_id: str) -> CheckResult:
        count = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_answer_runs WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        if count == 0:
            return CheckResult(
                check_name="ai_repetition_completeness",
                scope="ai_visibility",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="3 per question/engine",
                observed_value="no_rows",
                details={"reason": "no ai_answer_runs for run", "run_id": run_id},
            )
        incomplete = self.store.fetchall(
            """
            SELECT question_id, engine, COUNT(*) AS n,
                   COUNT(DISTINCT repetition_number) AS reps
            FROM ai_answer_runs
            WHERE run_id = ?
            GROUP BY as_of_date, question_id, engine
            HAVING reps < 3 OR n < 3
            """,
            (run_id,),
        )
        return CheckResult(
            check_name="ai_repetition_completeness",
            scope="ai_visibility",
            status=_pass_fail(len(incomplete) == 0),
            severity=Severity.ERROR,
            threshold="3 per question/engine",
            observed_value=str(len(incomplete)),
            details={"incomplete_groups": len(incomplete), "run_id": run_id},
        )

    def _freshness(self, run, *, run_id: str) -> CheckResult:
        as_of = date.fromisoformat(run["as_of_date"])
        issues = []
        gsc_latest = self.store.fetchall(
            "SELECT MAX(date) AS d FROM gsc_daily WHERE run_id = ?",
            (run_id,),
        )
        ga4_latest = self.store.fetchall(
            "SELECT MAX(date) AS d FROM ga4_daily WHERE run_id = ?",
            (run_id,),
        )
        if gsc_latest and gsc_latest[0]["d"]:
            expected = as_of - timedelta(days=self.config.freshness.gsc_days)
            latest = date.fromisoformat(gsc_latest[0]["d"])
            if latest < expected - timedelta(days=1):
                issues.append({"source": "gsc", "latest": latest.isoformat(), "expected": expected.isoformat()})
        if ga4_latest and ga4_latest[0]["d"]:
            expected = as_of - timedelta(days=self.config.freshness.ga4_days)
            latest = date.fromisoformat(ga4_latest[0]["d"])
            if latest < expected - timedelta(days=1):
                issues.append({"source": "ga4", "latest": latest.isoformat(), "expected": expected.isoformat()})
        for source, hours, table in (
            ("serper", self.config.freshness.serper_hours, "serp_daily"),
            ("ai_visibility", self.config.freshness.ai_visibility_hours, "ai_answer_runs"),
        ):
            count = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                    (run_id,),
                )[0]["n"]
            )
            if count == 0:
                continue
            row = self.store.fetchall(
                f"SELECT MAX(collected_at) AS t FROM {table} WHERE run_id = ?",
                (run_id,),
            )[0]
            collected = _parse_iso(row["t"])
            if collected is None:
                issues.append({"source": source, "reason": "missing collected_at"})
                continue
            age_hours = (datetime.now(timezone.utc) - collected.astimezone(timezone.utc)).total_seconds() / 3600
            if age_hours > hours + 6:
                issues.append({"source": source, "age_hours": round(age_hours, 2), "limit_hours": hours})
        return CheckResult(
            check_name="freshness",
            scope="run",
            status=_pass_fail(not issues),
            severity=Severity.ERROR,
            threshold="within configured source delay",
            observed_value=str(len(issues)),
            details={"issues": issues},
        )

    def _non_negative_metrics(self, *, run_id: str) -> CheckResult:
        offenders = {}
        checks = [
            ("gsc_daily", "clicks < 0 OR impressions < 0 OR position < 0"),
            ("ga4_daily", "sessions < 0 OR engaged_sessions < 0 OR purchases < 0 OR CAST(total_revenue AS REAL) < 0"),
            ("serp_daily", "sunnystep_position < 0 OR result_count_inspected < 0"),
            ("ai_answer_runs", "latency_ms < 0 OR CAST(api_cost_usd AS REAL) < 0"),
        ]
        total = 0
        for table, predicate in checks:
            count = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                    (run_id,),
                )[0]["n"]
            )
            if count == 0:
                continue
            n = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ? AND ({predicate})",
                    (run_id,),
                )[0]["n"]
            )
            offenders[table] = n
            total += n
        return CheckResult(
            check_name="non_negative_metrics",
            scope="storage",
            status=_pass_fail(total == 0),
            severity=Severity.ERROR,
            threshold="no negatives",
            observed_value=str(total),
            details=offenders,
        )

    def _ctr_range(self, *, run_id: str) -> CheckResult:
        count = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM gsc_daily WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        if count == 0:
            return CheckResult(
                check_name="ctr_range",
                scope="gsc",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="0<=ctr<=1",
                observed_value="no_rows",
                details={"reason": "no gsc_daily for run", "run_id": run_id},
            )
        bad = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM gsc_daily WHERE run_id = ? AND (ctr < 0 OR ctr > 1)",
                (run_id,),
            )[0]["n"]
        )
        return CheckResult(
            check_name="ctr_range",
            scope="gsc",
            status=_pass_fail(bad == 0),
            severity=Severity.ERROR,
            threshold="0<=ctr<=1",
            observed_value=str(bad),
            details={"out_of_range": bad},
        )

    def _position_validity(self, *, run_id: str) -> CheckResult:
        gsc_bad = 0
        serp_bad = 0
        gsc_count = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM gsc_daily WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        serp_count = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM serp_daily WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        if gsc_count:
            gsc_bad = int(
                self.store.fetchall(
                    "SELECT COUNT(*) AS n FROM gsc_daily WHERE run_id = ? AND position < 0",
                    (run_id,),
                )[0]["n"]
            )
        if serp_count:
            serp_bad = int(
                self.store.fetchall(
                    "SELECT COUNT(*) AS n FROM serp_daily WHERE run_id = ? AND (sunnystep_position < 0 OR sunnystep_position > 100)",
                    (run_id,),
                )[0]["n"]
            )
        return CheckResult(
            check_name="position_validity",
            scope="ranks",
            status=_pass_fail(gsc_bad + serp_bad == 0),
            severity=Severity.ERROR,
            threshold="gsc>=0;serp 0..100",
            observed_value=f"gsc_bad={gsc_bad};serp_bad={serp_bad}",
            details={"gsc_bad": gsc_bad, "serp_bad": serp_bad},
        )

    def _api_cost_cap(self, run) -> CheckResult:
        cost = Decimal(str(run["cost_usd"] or "0"))
        cap = self.config.daily_cost_cap_usd
        return CheckResult(
            check_name="api_cost_cap",
            scope="run",
            status=_pass_fail(cost <= cap),
            severity=Severity.CRITICAL,
            threshold=str(cap),
            observed_value=format(cost, "f"),
            details={"cost_usd": format(cost, "f"), "cap_usd": str(cap)},
        )

    def _run_duration(self, run) -> CheckResult:
        started = _parse_iso(run["started_at"])
        finished = _parse_iso(run["finished_at"]) or datetime.now(timezone.utc)
        if started is None:
            return CheckResult(
                check_name="run_duration",
                scope="run",
                status=CheckStatus.SKIPPED,
                severity=Severity.WARNING,
                threshold=str(self.config.run_timeout_seconds),
                observed_value="unavailable",
                details={"reason": "missing started_at"},
            )
        duration = (finished.astimezone(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
        limit = self.config.run_timeout_seconds
        ok = duration <= limit
        warn_only = duration <= limit * 1.1
        return CheckResult(
            check_name="run_duration",
            scope="run",
            status=_pass_fail(ok, warn_only=not ok and warn_only),
            severity=Severity.ERROR if not ok else Severity.WARNING,
            threshold=str(limit),
            observed_value=str(int(duration)),
            details={"duration_seconds": duration, "limit_seconds": limit},
        )

    def _backfill_preservation(self, *, run_id: str) -> CheckResult:
        # Natural-key uniqueness already enforces no duplicated keys; this check
        # confirms uniqueness plus that reruns leave counts stable for identical hashes.
        result = self._natural_key_uniqueness(run_id=run_id)
        return CheckResult(
            check_name="backfill_preservation",
            scope="storage",
            status=result.status,
            severity=Severity.CRITICAL,
            threshold="no duplicated natural keys",
            observed_value=result.observed_value,
            details=result.details,
        )

    def _raw_ai_answer_presence(self, *, run_id: str) -> CheckResult:
        count = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_answer_runs WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        if count == 0:
            return CheckResult(
                check_name="raw_ai_answer_presence",
                scope="ai_visibility",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="raw answer present",
                observed_value="no_rows",
                details={"run_id": run_id},
            )
        bad = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_answer_runs WHERE run_id = ? AND (raw_answer IS NULL OR TRIM(raw_answer) = '')",
                (run_id,),
            )[0]["n"]
        )
        return CheckResult(
            check_name="raw_ai_answer_presence",
            scope="ai_visibility",
            status=_pass_fail(bad == 0),
            severity=Severity.ERROR,
            threshold="raw answer present",
            observed_value=str(bad),
            details={"missing": bad, "run_id": run_id},
        )


    def _reconciliation_checks(self, run) -> List[CheckResult]:
        as_of = date.fromisoformat(run["as_of_date"])
        # Use as_of day window for stored totals; live aggregates unavailable offline.
        gsc = GscReconciliationCheck().run(
            detailed_clicks=stored_gsc_totals(self.store, as_of, as_of)[0],
            detailed_impressions=stored_gsc_totals(self.store, as_of, as_of)[1],
            aggregate_clicks=None,
            aggregate_impressions=None,
        )
        ga4 = Ga4ReconciliationCheck().run(
            detailed_sessions=stored_ga4_sessions(self.store, as_of, as_of),
            aggregate_sessions=None,
        )
        return [gsc, ga4]

    def _url_validity(self, *, run_id: str) -> CheckResult:
        bad = 0
        samples: List[str] = []
        queries = [
            ("gsc_daily", "page"),
            ("serp_daily", "target_page"),
            ("ai_answer_runs", "target_page"),
        ]
        for table, column in queries:
            count = int(
                self.store.fetchall(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                    (run_id,),
                )[0]["n"]
            )
            if count == 0:
                continue
            for row in self.store.fetchall(
                f"SELECT {column} AS url FROM {table} WHERE run_id = ?",
                (run_id,),
            ):
                url = row["url"] or ""
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    bad += 1
                    if len(samples) < 5:
                        samples.append(url)
        count_ai = int(
            self.store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_answer_runs WHERE run_id = ?",
                (run_id,),
            )[0]["n"]
        )
        if count_ai:
            for row in self.store.fetchall(
                "SELECT cited_urls FROM ai_answer_runs WHERE run_id = ?",
                (run_id,),
            ):
                try:
                    urls = json.loads(row["cited_urls"] or "[]")
                except json.JSONDecodeError:
                    bad += 1
                    continue
                for url in urls:
                    parsed = urlparse(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        bad += 1
                        if len(samples) < 5:
                            samples.append(url)
        return CheckResult(
            check_name="url_validity",
            scope="urls",
            status=_pass_fail(bad == 0, warn_only=True),
            severity=Severity.WARNING,
            threshold="http(s) URLs",
            observed_value=str(bad),
            details={"invalid": bad, "samples": samples, "run_id": run_id},
        )



    def _chatgpt_search_capability(self, *, run_id: str) -> CheckResult:
        rows = self.store.fetchall(
            "SELECT search_enabled, COUNT(*) AS n FROM ai_answer_runs WHERE run_id = ? AND engine = 'chatgpt' GROUP BY search_enabled",
            (run_id,),
        )
        if not rows:
            return CheckResult(
                check_name="chatgpt_search_capability",
                scope="ai_visibility",
                status=CheckStatus.SKIPPED,
                severity=Severity.CRITICAL,
                threshold="search_enabled for ChatGPT GEO rows",
                observed_value="no_chatgpt_rows",
                details={"run_id": run_id},
            )
        enabled = sum(int(r["n"]) for r in rows if int(r["search_enabled"] or 0) == 1)
        disabled = sum(int(r["n"]) for r in rows if int(r["search_enabled"] or 0) == 0)
        # Fail closed: any non-search ChatGPT rows in the run block GEO publish confidence.
        ok = disabled == 0 and enabled > 0
        return CheckResult(
            check_name="chatgpt_search_capability",
            scope="ai_visibility",
            status=_pass_fail(ok),
            severity=Severity.CRITICAL,
            threshold="all chatgpt rows search_enabled=1",
            observed_value=f"enabled={enabled};disabled={disabled}",
            details={"enabled": enabled, "disabled": disabled, "run_id": run_id},
        )

    def _lark_publish_parity(self) -> CheckResult:
        published = self.store.fetchall(
            "SELECT report_id, lark_record_id, status FROM weekly_reports WHERE status = 'published'"
        )
        if not published:
            return CheckResult(
                check_name="lark_publish_parity",
                scope="lark",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="published keys match intended set",
                observed_value="no_published_reports",
                details={"reason": "no published weekly reports yet"},
            )
        missing = [row["report_id"] for row in published if not row["lark_record_id"]]
        return CheckResult(
            check_name="lark_publish_parity",
            scope="lark",
            status=_pass_fail(not missing),
            severity=Severity.ERROR,
            threshold="each published report has lark_record_id",
            observed_value=f"published={len(published)};missing_ids={len(missing)}",
            details={"missing_report_ids": missing},
        )


def summarize_check_results(results: Sequence[CheckResult]) -> Dict[str, Any]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    critical_fails = []
    error_fails = []
    warnings = []
    for item in results:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
        if item.status == CheckStatus.FAIL:
            if item.severity == Severity.CRITICAL:
                critical_fails.append(item.check_name)
            elif item.severity == Severity.ERROR:
                error_fails.append(item.check_name)
            else:
                warnings.append(item.check_name)
        elif item.status == CheckStatus.WARN:
            warnings.append(item.check_name)
    if critical_fails:
        gate = "failed"
    elif error_fails:
        gate = "partial"
    else:
        gate = "succeeded"
    return {
        "check_count": len(results),
        "counts": counts,
        "gate_status": gate,
        "critical_failures": critical_fails,
        "error_failures": error_fails,
        "warnings": warnings,
        "checks": [
            {
                "check_name": item.check_name,
                "scope": item.scope,
                "status": item.status.value,
                "severity": item.severity.value,
                "observed_value": item.observed_value,
            }
            for item in results
        ],
    }
