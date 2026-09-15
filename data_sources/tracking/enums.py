from __future__ import annotations

from enum import Enum


class Source(str, Enum):
    GSC = "gsc"
    GA4 = "ga4"
    SERPER = "serper"
    AI_VISIBILITY = "ai_visibility"
    SHOPIFY = "shopify"


class RunType(str, Enum):
    DAILY = "daily"
    BACKFILL = "backfill"
    WEEKLY = "weekly"
    BASELINE = "baseline"
    MANUAL = "manual"
    DEMO = "demo"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChannelClass(str, Enum):
    ORGANIC_SEARCH = "organic_search"
    AI_REFERRAL = "ai_referral"
    OTHER = "other"


class AIOverviewStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class Engine(str, Enum):
    CHATGPT = "chatgpt"
    PERPLEXITY = "perplexity"


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class BaselineStatus(str, Enum):
    DRAFT = "draft"
    LOCKED = "locked"
    SUPERSEDED = "superseded"


class ReportStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    FAILED = "failed"


class AlertStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"


class OpportunityCategory(str, Enum):
    SEO = "seo"
    GEO = "geo"


class CollectorStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
