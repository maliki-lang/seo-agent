from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .enums import (
    AIOverviewStatus,
    AlertStatus,
    BaselineStatus,
    ChannelClass,
    CheckStatus,
    CollectorStatus,
    Engine,
    OpportunityCategory,
    ReportStatus,
    RunStatus,
    RunType,
    Severity,
    Source,
)
from .transforms.normalize import SCHEMA_VERSION, natural_key, row_hash, utc_now_iso


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


@dataclass
class RunLog:
    run_id: str
    run_type: RunType
    as_of_date: date
    started_at: str
    status: RunStatus = RunStatus.PENDING
    finished_at: Optional[str] = None
    requested_collectors: List[str] = field(default_factory=list)
    completed_collectors: List[str] = field(default_factory=list)
    failed_collectors: Dict[str, str] = field(default_factory=dict)
    attempt_number: int = 1
    parent_run_id: Optional[str] = None
    code_version: str = ""
    config_fingerprint: str = ""
    row_counts: Dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal = Decimal("0")
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class GscDailyRow:
    run_id: str
    as_of_date: date
    date: date
    query: str
    page: str
    country: str
    clicks: int
    impressions: int
    ctr: float
    position: float
    is_brand: bool
    brand_rule_version: str
    source: Source = Source.GSC
    collected_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    raw_record_id: Optional[str] = None

    def grain(self) -> List[Any]:
        return [self.date.isoformat(), self.query, self.page, self.country]

    def payload(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "query": self.query,
            "page": self.page,
            "country": self.country,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "ctr": self.ctr,
            "position": self.position,
            "is_brand": self.is_brand,
            "brand_rule_version": self.brand_rule_version,
            "source": self.source.value,
            "schema_version": self.schema_version,
        }

    @property
    def natural_key(self) -> str:
        return natural_key(self.grain())

    @property
    def row_hash(self) -> str:
        return row_hash(self.payload())


@dataclass
class Ga4DailyRow:
    run_id: str
    as_of_date: date
    date: date
    session_source: str
    session_medium: str
    landing_page: str
    channel_class: ChannelClass
    sessions: int
    engaged_sessions: int
    purchases: int
    total_revenue: Decimal
    source: Source = Source.GA4
    collected_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    raw_record_id: Optional[str] = None
    revenue_currency: str = "SGD"

    def grain(self) -> List[Any]:
        return [self.date.isoformat(), self.session_source, self.session_medium, self.landing_page]

    def payload(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "session_source": self.session_source,
            "session_medium": self.session_medium,
            "landing_page": self.landing_page,
            "channel_class": self.channel_class.value,
            "sessions": self.sessions,
            "engaged_sessions": self.engaged_sessions,
            "purchases": self.purchases,
            "total_revenue": format(self.total_revenue, "f"),
            "revenue_currency": self.revenue_currency,
            "source": self.source.value,
            "schema_version": self.schema_version,
        }

    @property
    def natural_key(self) -> str:
        return natural_key(self.grain())

    @property
    def row_hash(self) -> str:
        return row_hash(self.payload())


@dataclass
class SerpDailyRow:
    run_id: str
    as_of_date: date
    date: date
    keyword_id: str
    keyword: str
    cluster: str
    target_page: str
    country: str
    device: str
    sunnystep_position: int
    result_count_inspected: int
    top_10_domains: List[str]
    ai_overview_status: AIOverviewStatus
    ai_overview_citations: List[str]
    source: Source = Source.SERPER
    collected_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    raw_record_id: Optional[str] = None

    def grain(self) -> List[Any]:
        return [self.date.isoformat(), self.keyword_id, self.country, self.device]

    def payload(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "keyword_id": self.keyword_id,
            "keyword": self.keyword,
            "cluster": self.cluster,
            "target_page": self.target_page,
            "country": self.country,
            "device": self.device,
            "sunnystep_position": self.sunnystep_position,
            "result_count_inspected": self.result_count_inspected,
            "top_10_domains": self.top_10_domains,
            "ai_overview_status": self.ai_overview_status.value,
            "ai_overview_citations": self.ai_overview_citations,
            "source": self.source.value,
            "schema_version": self.schema_version,
        }

    @property
    def natural_key(self) -> str:
        return natural_key(self.grain())

    @property
    def row_hash(self) -> str:
        return row_hash(self.payload())


@dataclass
class AiAnswerRow:
    run_id: str
    as_of_date: date
    engine: Engine
    question_id: str
    question: str
    repetition_number: int
    raw_answer: str
    mentioned_sunnystep: bool
    cited_urls: List[str]
    named_competitors: List[str]
    target_cluster: str
    target_page: str
    api_cost_usd: Decimal
    latency_ms: int
    model: str
    search_enabled: bool
    parser_version: str
    source: Source = Source.AI_VISIBILITY
    collected_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    raw_record_id: Optional[str] = None

    def grain(self) -> List[Any]:
        return [self.as_of_date.isoformat(), self.engine.value, self.question_id, self.repetition_number]

    def payload(self) -> Dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "engine": self.engine.value,
            "question_id": self.question_id,
            "question": self.question,
            "repetition_number": self.repetition_number,
            "raw_answer": self.raw_answer,
            "mentioned_sunnystep": self.mentioned_sunnystep,
            "cited_urls": self.cited_urls,
            "named_competitors": self.named_competitors,
            "target_cluster": self.target_cluster,
            "target_page": self.target_page,
            "api_cost_usd": format(self.api_cost_usd, "f"),
            "latency_ms": self.latency_ms,
            "model": self.model,
            "search_enabled": self.search_enabled,
            "parser_version": self.parser_version,
            "source": self.source.value,
            "schema_version": self.schema_version,
        }

    @property
    def natural_key(self) -> str:
        return natural_key(self.grain())

    @property
    def row_hash(self) -> str:
        return row_hash(self.payload())


@dataclass
class QualityCheckRow:
    check_id: str
    run_id: str
    check_name: str
    scope: str
    status: CheckStatus
    severity: Severity
    threshold: str
    observed_value: str
    details_json: Dict[str, Any]
    checked_at: str = field(default_factory=utc_now_iso)


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: "UpsertStats") -> "UpsertStats":
        return UpsertStats(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
        )

    def as_dict(self) -> Dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def dump_json(value: Any) -> str:
    return _json_dump(value)
