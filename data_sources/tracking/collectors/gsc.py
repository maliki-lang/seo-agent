from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..collectors.base import Collector, CollectorResult
from ..config import REPO_ROOT, TrackingConfig
from ..enums import Source
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    InvalidResponseError,
    PermissionDenied,
    TransientNetworkError,
)
from ..models import GscDailyRow
from ..retries import classify_http_error
from ..transforms.brand_label import BrandClassifier
from ..transforms.normalize import canonicalize_url, normalize_query, utc_now_iso

GSC_ROW_LIMIT = 25000
QueryFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def resolve_credentials_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def gsc_latest_available_date(as_of: date, delay_days: int) -> date:
    return as_of - timedelta(days=delay_days)


def _map_http_error(exc: Exception) -> Exception:
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    text = str(exc).lower()
    if status in {401, 403} or "permission" in text or "forbidden" in text:
        if status == 401:
            return AuthenticationError("Search Console authentication failed.")
        return PermissionDenied(
            "The tracking service account needs read access to the configured "
            "Search Console property. GSC rows were not inserted."
        )
    mapped = classify_http_error(status, "Search Console request failed")
    if status >= 500 or status in {408, 429}:
        return mapped
    return TransientNetworkError("Search Console transport error.")


class GscCollector(Collector):
    source = Source.GSC.value

    def __init__(
        self,
        config: TrackingConfig,
        *,
        query_fn: Optional[QueryFn] = None,
        brand_classifier: Optional[BrandClassifier] = None,
    ):
        self.config = config
        self._query_fn = query_fn
        self.brand = brand_classifier or BrandClassifier.from_config(config)

    def _client_query(self) -> QueryFn:
        if self._query_fn is not None:
            return self._query_fn
        if not self.config.gsc_property:
            raise ConfigurationError("GSC_PROPERTY is required for the GSC collector")
        creds = resolve_credentials_path(self.config.gsc_credentials_path)
        if not creds.exists():
            raise ConfigurationError("GSC credentials file is missing")
        from data_sources.modules.google_search_console import GoogleSearchConsole

        client = GoogleSearchConsole(
            site_url=self.config.gsc_property,
            credentials_path=str(creds),
            country="",
        )
        return client._query

    def collect(
        self,
        *,
        run_id: str,
        as_of_date: date,
        start_date: date,
        end_date: date,
        **kwargs: Any,
    ) -> CollectorResult:
        query = self._client_query()
        rows: List[GscDailyRow] = []
        start_row = 0
        pages = 0
        collected_at = utc_now_iso()
        country_filter = (self.config.gsc_country or "").strip().lower()
        while True:
            request: Dict[str, Any] = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["date", "query", "page", "country"],
                "rowLimit": GSC_ROW_LIMIT,
                "startRow": start_row,
            }
            if country_filter:
                request["dimensionFilterGroups"] = [{
                    "filters": [{
                        "dimension": "country",
                        "operator": "equals",
                        "expression": country_filter,
                    }]
                }]
            try:
                response = query(request)
            except Exception as exc:
                raise _map_http_error(exc) from exc
            page_rows = response.get("rows") if isinstance(response, dict) else None
            if page_rows is None:
                page_rows = []
            if not isinstance(page_rows, list):
                raise InvalidResponseError("Search Console rows were not a list")
            pages += 1
            for raw in page_rows:
                keys = raw.get("keys") or []
                if len(keys) < 4:
                    continue
                query_text = normalize_query(keys[1])
                page = canonicalize_url(keys[2]) or str(keys[2])
                country = (keys[3] or "").strip().lower()
                clicks = int(raw.get("clicks") or 0)
                impressions = int(raw.get("impressions") or 0)
                ctr = float(raw.get("ctr") or 0.0)
                if ctr > 1:
                    ctr = ctr / 100.0
                position = float(raw.get("position") or 0.0)
                rows.append(
                    GscDailyRow(
                        run_id=run_id,
                        as_of_date=as_of_date,
                        date=date.fromisoformat(str(keys[0])),
                        query=query_text,
                        page=page,
                        country=country,
                        clicks=clicks,
                        impressions=impressions,
                        ctr=ctr,
                        position=position,
                        is_brand=self.brand.is_brand(query_text),
                        brand_rule_version=self.brand.version,
                        collected_at=collected_at,
                    )
                )
            if len(page_rows) < GSC_ROW_LIMIT:
                break
            start_row += len(page_rows)
        delay = self.config.freshness.gsc_days
        return CollectorResult(
            source=self.source,
            rows=rows,
            warnings=[
                f"GSC data delay assumed at {delay} days; missing recent dates are unavailable, not zero.",
                f"pagination_pages={pages}",
            ],
        )

    def aggregate_totals(
        self,
        start_date: date,
        end_date: date,
    ) -> Tuple[int, int]:
        """Less-granular clicks/impressions for reconciliation. No placeholder zeros."""
        query = self._client_query()
        request: Dict[str, Any] = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date"],
            "rowLimit": GSC_ROW_LIMIT,
        }
        country_filter = (self.config.gsc_country or "").strip().lower()
        if country_filter:
            request["dimensionFilterGroups"] = [{
                "filters": [{
                    "dimension": "country",
                    "operator": "equals",
                    "expression": country_filter,
                }]
            }]
        try:
            response = query(request)
        except Exception as exc:
            raise _map_http_error(exc) from exc
        clicks = 0
        impressions = 0
        for raw in response.get("rows") or []:
            clicks += int(raw.get("clicks") or 0)
            impressions += int(raw.get("impressions") or 0)
        return clicks, impressions
