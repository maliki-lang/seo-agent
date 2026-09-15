from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

from .base import Check, CheckResult
from ..enums import CheckStatus, Severity
from ..storage import TrackingStore


def _status(ok: bool, warn_only: bool) -> CheckStatus:
    if ok:
        return CheckStatus.PASS
    return CheckStatus.WARN if warn_only else CheckStatus.FAIL


class GscReconciliationCheck(Check):
    name = "gsc_reconciliation"
    severity = Severity.WARNING

    def run(
        self,
        *,
        detailed_clicks: int,
        detailed_impressions: int,
        aggregate_clicks: Optional[int],
        aggregate_impressions: Optional[int],
        tolerance: float = 0.05,
        **kwargs,
    ) -> CheckResult:
        if aggregate_clicks is None or aggregate_impressions is None:
            return CheckResult(
                check_name=self.name,
                scope="gsc",
                status=CheckStatus.SKIPPED,
                severity=self.severity,
                threshold=str(tolerance),
                observed_value="unavailable",
                details={"reason": "aggregate query unavailable"},
            )
        click_delta = abs(detailed_clicks - aggregate_clicks)
        impression_delta = abs(detailed_impressions - aggregate_impressions)
        click_ok = click_delta <= max(10, aggregate_clicks * tolerance)
        impression_ok = impression_delta <= max(10, aggregate_impressions * tolerance)
        ok = click_ok and impression_ok
        return CheckResult(
            check_name=self.name,
            scope="gsc",
            status=_status(ok, warn_only=True),
            severity=self.severity,
            threshold=str(tolerance),
            observed_value=f"clicks_delta={click_delta};impressions_delta={impression_delta}",
            details={
                "detailed_clicks": detailed_clicks,
                "aggregate_clicks": aggregate_clicks,
                "detailed_impressions": detailed_impressions,
                "aggregate_impressions": aggregate_impressions,
            },
        )


class Ga4ReconciliationCheck(Check):
    name = "ga4_reconciliation"
    severity = Severity.ERROR

    def run(
        self,
        *,
        detailed_sessions: int,
        aggregate_sessions: Optional[int],
        tolerance: float = 0.02,
        **kwargs,
    ) -> CheckResult:
        if aggregate_sessions is None:
            return CheckResult(
                check_name=self.name,
                scope="ga4",
                status=CheckStatus.SKIPPED,
                severity=Severity.WARNING,
                threshold=str(tolerance),
                observed_value="unavailable",
                details={"reason": "aggregate query unavailable"},
            )
        delta = abs(detailed_sessions - aggregate_sessions)
        ok = delta <= max(5, aggregate_sessions * tolerance)
        return CheckResult(
            check_name=self.name,
            scope="ga4",
            status=_status(ok, warn_only=False),
            severity=self.severity,
            threshold=str(tolerance),
            observed_value=str(delta),
            details={
                "detailed_sessions": detailed_sessions,
                "aggregate_sessions": aggregate_sessions,
            },
        )


def stored_gsc_totals(store: TrackingStore, start: date, end: date) -> Tuple[int, int]:
    rows = store.fetchall(
        """
        SELECT COALESCE(SUM(clicks), 0) AS clicks, COALESCE(SUM(impressions), 0) AS impressions
        FROM gsc_daily WHERE date >= ? AND date <= ?
        """,
        (start.isoformat(), end.isoformat()),
    )
    row = rows[0]
    return int(row["clicks"]), int(row["impressions"])


def stored_ga4_sessions(store: TrackingStore, start: date, end: date) -> int:
    rows = store.fetchall(
        """
        SELECT COALESCE(SUM(sessions), 0) AS sessions
        FROM ga4_daily WHERE date >= ? AND date <= ?
        """,
        (start.isoformat(), end.isoformat()),
    )
    return int(rows[0]["sessions"])
