from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from ..catalogs import KeywordRecord, active_keywords
from ..collectors.base import Collector, CollectorResult
from ..config import TrackingConfig
from ..costs import CostLedger
from ..enums import AIOverviewStatus, Source
from ..exceptions import AuthenticationError, ConfigurationError
from ..models import SerpDailyRow
from ..retries import classify_http_error
from ..transforms.normalize import canonicalize_url, normalize_host, utc_now_iso

SUNNYSTEP_HOSTS = {"sunnystep.com", "gosunnystep.myshopify.com"}
DEFAULT_QUERY_COST = Decimal("0.001")
SearchFn = Callable[[str], Dict[str, Any]]


def redact_serper_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    copy = json.loads(json.dumps(payload))
    for key in ("apiKey", "api_key", "key"):
        if key in copy:
            copy[key] = "[redacted]"
    return copy


def detect_ai_overview(payload: Dict[str, Any]) -> tuple:
    if not isinstance(payload, dict):
        return AIOverviewStatus.UNVERIFIED, []
    if "aiOverview" not in payload:
        return AIOverviewStatus.UNVERIFIED, []
    block = payload.get("aiOverview")
    if not block:
        return AIOverviewStatus.ABSENT, []
    citations: List[str] = []
    if isinstance(block, dict):
        for item in block.get("links") or block.get("citations") or block.get("sources") or []:
            url = ""
            if isinstance(item, str):
                url = item
            elif isinstance(item, dict):
                url = item.get("link") or item.get("url") or ""
            canon = canonicalize_url(url)
            if canon:
                citations.append(canon)
        text = json.dumps(block)
        if not citations:
            # Keep status present even when citations are empty; do not invent them.
            pass
    return AIOverviewStatus.PRESENT, citations


def _is_sunnystep_host(host: str) -> bool:
    host = normalize_host(host)
    return any(host == item or host.endswith("." + item) for item in SUNNYSTEP_HOSTS)


def sunnystep_position(organic: Sequence[Dict[str, Any]]) -> int:
    for item in organic:
        url = item.get("link") or item.get("url") or ""
        host = normalize_host(url)
        if _is_sunnystep_host(host):
            try:
                return int(item.get("position") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


class SerperCollector(Collector):
    source = Source.SERPER.value

    def __init__(
        self,
        config: TrackingConfig,
        *,
        search_fn: Optional[SearchFn] = None,
        keywords: Optional[Sequence[KeywordRecord]] = None,
        cost_ledger: Optional[CostLedger] = None,
        keyword_limit: int = 0,
        query_cost_usd: Decimal = DEFAULT_QUERY_COST,
    ):
        self.config = config
        self._search_fn = search_fn
        self._keywords = list(keywords) if keywords is not None else None
        self.cost_ledger = cost_ledger
        self.keyword_limit = keyword_limit
        self.query_cost_usd = query_cost_usd

    def _search(self) -> SearchFn:
        if self._search_fn is not None:
            return self._search_fn
        if not self.config.serper_api_key:
            raise ConfigurationError("SERPER_API_KEY is required for the Serper collector")
        from data_sources.modules.serper import SerperClient

        client = SerperClient(api_key=self.config.serper_api_key, timeout_seconds=30)

        def _call(keyword: str) -> Dict[str, Any]:
            try:
                return client._search(
                    keyword,
                    gl=self.config.serper_gl,
                    hl=self.config.serper_hl,
                    num=self.config.serper_results_limit,
                    location=self.config.serper_location or None,
                )
            except requests.HTTPError as exc:
                status = int(getattr(exc.response, "status_code", 0) or 0)
                mapped = classify_http_error(status, "Serper request failed")
                if status in {401, 403}:
                    raise AuthenticationError("Serper authentication failed.") from exc
                raise mapped from exc

        return _call

    def collect(
        self,
        *,
        run_id: str,
        as_of_date: date,
        start_date: date,
        end_date: date,
        **kwargs: Any,
    ) -> CollectorResult:
        keywords = list(self._keywords if self._keywords is not None else active_keywords(self.config))
        keywords = [row for row in keywords if row.active]
        if self.keyword_limit:
            keywords = keywords[: self.keyword_limit]
        search = self._search()
        rows: List[SerpDailyRow] = []
        raw_payloads: List[Dict[str, Any]] = []
        cost = Decimal("0")
        collected_at = utc_now_iso()
        capability = "unverified"
        for record in keywords:
            if self.cost_ledger is not None:
                self.cost_ledger.add(self.query_cost_usd, source=self.source)
            cost += self.query_cost_usd
            payload = search(record.keyword)
            if "aiOverview" in payload:
                capability = "aiOverview-key-present"
            status, citations = detect_ai_overview(payload)
            organic = payload.get("organic") or []
            inspected = min(len(organic), int(self.config.serper_results_limit or 100))
            domains = []
            for item in organic[:10]:
                host = normalize_host(item.get("link") or item.get("url") or "")
                if host:
                    domains.append(host)
            raw_id = str(uuid.uuid4())
            raw_payloads.append(
                {
                    "raw_record_id": raw_id,
                    "endpoint_or_operation": "serper.search",
                    "payload": redact_serper_payload(payload),
                }
            )
            rows.append(
                SerpDailyRow(
                    run_id=run_id,
                    as_of_date=as_of_date,
                    date=end_date,
                    keyword_id=record.keyword_id,
                    keyword=record.keyword,
                    cluster=record.cluster,
                    target_page=record.target_page,
                    country=record.country,
                    device=record.device,
                    sunnystep_position=sunnystep_position(organic),
                    result_count_inspected=inspected,
                    top_10_domains=domains,
                    ai_overview_status=status,
                    ai_overview_citations=citations,
                    collected_at=collected_at,
                    raw_record_id=raw_id,
                )
            )
        result = CollectorResult(
            source=self.source,
            rows=rows,
            cost_usd=cost,
            capability=capability,
            raw_payloads=raw_payloads,
            warnings=[
                "AI Overview absent is used only when the aiOverview key is present and empty; "
                "otherwise status is unverified."
            ],
        )
        return result
