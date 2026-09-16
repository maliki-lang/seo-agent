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


class CatalogueBuildType(str, Enum):
    KEYWORD = "keyword"
    AI_QUESTION = "ai_question"


class CatalogueBuildStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    ACTIVATED = "activated"
    SUPERSEDED = "superseded"


class CandidateDecision(str, Enum):
    PENDING = "pending"
    SELECTED = "selected"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class TransformationMethod(str, Enum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    MERGED_VARIANTS = "merged_variants"
    HUMAN_REWORDED = "human_reworded"


class BrandStatus(str, Enum):
    BRANDED = "branded"
    NON_BRANDED = "non_branded"
    AMBIGUOUS_BRAND = "ambiguous_brand"


class BrandMatchType(str, Enum):
    EXACT_CONFIGURED_TERM = "exact_configured_term"
    APPROVED_ALIAS = "approved_alias"
    APPROVED_TYPO = "approved_typo"
    FUZZY_SUSPECT = "fuzzy_suspect"
    NO_MATCH = "no_match"


class RoutingBucket(str, Enum):
    NONBRAND_DISCOVERY = "nonbrand_discovery"
    BRANDED_BENCHMARK = "branded_benchmark"
    COMPETITOR_BENCHMARK = "competitor_benchmark"
    LOCAL_STORE = "local_store"
    AMBIGUOUS_BRAND = "ambiguous_brand"
    IRRELEVANT = "irrelevant"
    MANUAL_REVIEW = "manual_review"


class CompetitorStatus(str, Enum):
    NONE = "none"
    EXPLICIT = "explicit"
    AMBIGUOUS = "ambiguous"


class SearchIntent(str, Enum):
    NAVIGATIONAL_BRAND = "navigational_brand"
    NAVIGATIONAL_COMPETITOR = "navigational_competitor"
    LOCAL_STORE = "local_store"
    TRANSACTIONAL_CATEGORY = "transactional_category"
    COMMERCIAL_INVESTIGATION = "commercial_investigation"
    PROBLEM_SOLUTION = "problem_solution"
    INFORMATIONAL = "informational"
    CAMPAIGN_EVENT = "campaign_event"
    AMBIGUOUS = "ambiguous"


class StrategicLane(str, Enum):
    NEED_STATE = "need_state"
    PRODUCT_CATEGORY = "product_category"
    USE_CASE_AUDIENCE = "use_case_audience"
    LOCAL_STORE = "local_store"
    COMMERCIAL_DISCOVERY = "commercial_discovery"
    COMPETITOR_DISCOVERY = "competitor_discovery"
    STRATEGIC_GAP = "strategic_gap"
    INFORMATIONAL_EDITORIAL = "informational_editorial"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_REVIEW = "eligible_with_review"
    INELIGIBLE_BRAND = "ineligible_brand"
    INELIGIBLE_DUPLICATE_VARIANT = "ineligible_duplicate_variant"
    INELIGIBLE_IRRELEVANT = "ineligible_irrelevant"
    INELIGIBLE_UNSUPPORTED_CLAIM = "ineligible_unsupported_claim"
    INELIGIBLE_NO_ACTIONABLE_TARGET = "ineligible_no_actionable_target"
    PENDING_CLASSIFICATION = "pending_classification"


class BusinessRelevanceStatus(str, Enum):
    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"
    LOCATION_ONLY = "location_only"
    PENDING_REVIEW = "pending_review"


class TargetPageStatus(str, Enum):
    OBSERVED_PAGE_SUITABLE = "observed_page_suitable"
    OBSERVED_PAGE_NEEDS_OPTIMIZATION = "observed_page_needs_optimization"
    MULTIPLE_PAGES_COMPETING = "multiple_pages_competing"
    APPROVED_NEW_PAGE = "approved_new_page"
    HOMEPAGE_UNRESOLVED = "homepage_unresolved"
    NO_SENSIBLE_TARGET = "no_sensible_target"
    MANUAL_REVIEW = "manual_review"


class ProposedAction(str, Enum):
    OPTIMIZE_EXISTING = "optimize_existing"
    CONSOLIDATE_COMPETING_PAGES = "consolidate_competing_pages"
    CREATE_NEW_PAGE = "create_new_page"
    PROTECT_EXISTING = "protect_existing"
    MONITOR_ONLY = "monitor_only"
    NO_ACTION = "no_action"


class MultiPageClass(str, Enum):
    CANNIBALIZATION_CANDIDATE = "cannibalization_candidate"
    INTENT_SPLIT = "intent_split"
    NORMAL_PAGE_VARIATION = "normal_page_variation"
    UNRESOLVED = "unresolved"


class FamilyRole(str, Enum):
    PRIMARY = "primary"
    VARIANT = "variant"


class CollectorStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
