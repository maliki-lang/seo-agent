from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..collectors.base import Collector, CollectorResult
from ..config import REPO_ROOT, TrackingConfig
from ..enums import Source
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    PermissionDenied,
    SchemaMismatchError,
    TransientNetworkError,
)
from ..models import Ga4DailyRow
from ..transforms.ai_referrals import ChannelClassifier, classifier_from_config
from ..transforms.normalize import normalize_blank, utc_now_iso

GA4_PAGE_SIZE = 100000
GA4_DETAIL_METRICS = ("sessions", "engagedSessions", "ecommercePurchases", "totalRevenue")
GA4_FALLBACK_PURCHASE_METRIC = "purchases"
RunReportFn = Callable[[Dict[str, Any]], Any]


def _parse_ga4_date(value: str) -> date:
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text)


def ga4_latest_available_date(as_of: date, delay_days: int) -> date:
    return as_of - timedelta(days=delay_days)


def _map_ga4_error(exc: Exception) -> Exception:
    name = exc.__class__.__name__
    text = str(exc).lower()
    if name in {"Unauthenticated", "UnauthenticatedError"} or "unauthenticated" in text:
        return AuthenticationError("GA4 authentication failed.")
    if name in {"PermissionDenied", "PermissionDeniedError"} or "permission" in text or "403" in text:
        return PermissionDenied("The tracking service account needs read access to the configured GA4 property.")
    if "invalid argument" in text or "unrecognized" in text:
        return SchemaMismatchError("GA4 response schema did not match the requested dimensions or metrics.")
    return TransientNetworkError("GA4 transport error.")


def _row_values(row: Any) -> Tuple[List[str], List[str]]:
    dims = [item.value for item in row.dimension_values]
    metrics = [item.value for item in row.metric_values]
    return dims, metrics


class Ga4Collector(Collector):
    source = Source.GA4.value

    def __init__(
        self,
        config: TrackingConfig,
        *,
        run_report_fn: Optional[RunReportFn] = None,
        channel_classifier: Optional[ChannelClassifier] = None,
        purchase_metric: str = "ecommercePurchases",
    ):
        self.config = config
        self._run_report_fn = run_report_fn
        self.classify = channel_classifier or classifier_from_config(config)
        self.purchase_metric = purchase_metric
        self.capability = "verified"

    def _run_report(self) -> RunReportFn:
        if self._run_report_fn is not None:
            return self._run_report_fn
        if not self.config.ga4_property_id:
            raise ConfigurationError("GA4_PROPERTY_ID is required for the GA4 collector")
        creds = Path(self.config.ga4_credentials_path)
        if not creds.is_absolute():
            creds = REPO_ROOT / creds
        if not creds.exists():
            raise ConfigurationError("GA4 credentials file is missing")
        from data_sources.modules.google_analytics import GoogleAnalytics
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

        client = GoogleAnalytics(
            property_id=self.config.ga4_property_id,
            credentials_path=str(creds),
        )

        def _call(spec: Dict[str, Any]) -> Any:
            request = RunReportRequest(
                property=f"properties/{self.config.ga4_property_id}",
                date_ranges=[DateRange(start_date=spec["start_date"], end_date=spec["end_date"])],
                dimensions=[Dimension(name=name) for name in spec["dimensions"]],
                metrics=[Metric(name=name) for name in spec["metrics"]],
                limit=spec.get("limit", GA4_PAGE_SIZE),
                offset=spec.get("offset", 0),
            )
            return client.client.run_report(request)

        return _call

    def _fetch_pages(self, spec: Dict[str, Any]) -> List[Any]:
        runner = self._run_report()
        offset = 0
        rows: List[Any] = []
        while True:
            page_spec = dict(spec)
            page_spec["offset"] = offset
            page_spec["limit"] = GA4_PAGE_SIZE
            try:
                response = runner(page_spec)
            except Exception as exc:
                raise _map_ga4_error(exc) from exc
            page_rows = list(getattr(response, "rows", None) or [])
            rows.extend(page_rows)
            if len(page_rows) < GA4_PAGE_SIZE:
                break
            offset += len(page_rows)
        return rows

    def collect(
        self,
        *,
        run_id: str,
        as_of_date: date,
        start_date: date,
        end_date: date,
        **kwargs: Any,
    ) -> CollectorResult:
        collected_at = utc_now_iso()
        metrics = list(GA4_DETAIL_METRICS)
        if self.purchase_metric != "ecommercePurchases":
            metrics[2] = self.purchase_metric
        spec = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "dimensions": [
                "date",
                "sessionSource",
                "sessionMedium",
                "landingPagePlusQueryString",
            ],
            "metrics": metrics,
        }
        try:
            raw_rows = self._fetch_pages(spec)
        except SchemaMismatchError:
            if metrics[2] == "ecommercePurchases":
                self.purchase_metric = GA4_FALLBACK_PURCHASE_METRIC
                self.capability = "fallback_purchases"
                spec["metrics"] = ["sessions", "engagedSessions", "purchases", "totalRevenue"]
                raw_rows = self._fetch_pages(spec)
            else:
                raise
        rows: List[Ga4DailyRow] = []
        for raw in raw_rows:
            dims, metric_values = _row_values(raw)
            if len(dims) < 4 or len(metric_values) < 4:
                continue
            session_source = normalize_blank(dims[1])
            session_medium = normalize_blank(dims[2])
            landing = normalize_blank(dims[3], default="(not set)")
            rows.append(
                Ga4DailyRow(
                    run_id=run_id,
                    as_of_date=as_of_date,
                    date=_parse_ga4_date(dims[0]),
                    session_source=session_source,
                    session_medium=session_medium,
                    landing_page=landing,
                    channel_class=self.classify(session_source, session_medium),
                    sessions=int(float(metric_values[0] or 0)),
                    engaged_sessions=int(float(metric_values[1] or 0)),
                    purchases=int(float(metric_values[2] or 0)),
                    total_revenue=Decimal(str(metric_values[3] or "0")),
                    collected_at=collected_at,
                    revenue_currency="SGD",
                )
            )
        delay = self.config.freshness.ga4_days
        return CollectorResult(
            source=self.source,
            rows=rows,
            capability=self.capability,
            warnings=[
                f"GA4 property timezone/currency follow the GA4 property; tracking stores revenue as SGD decimals.",
                f"GA4 data delay assumed at {delay} days; missing recent dates are unavailable, not zero.",
            ],
        )

    def aggregate_sessions(self, start_date: date, end_date: date) -> int:
        spec = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "dimensions": ["date"],
            "metrics": ["sessions"],
        }
        total = 0
        for raw in self._fetch_pages(spec):
            _dims, metrics = _row_values(raw)
            total += int(float(metrics[0] or 0))
        return total


def fake_ga4_row(dimensions: Sequence[str], metrics: Sequence[str]) -> Any:
    """Test helper matching the GA4 row attribute shape."""
    return SimpleNamespace(
        dimension_values=[SimpleNamespace(value=value) for value in dimensions],
        metric_values=[SimpleNamespace(value=value) for value in metrics],
    )
