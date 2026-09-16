from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

from .exceptions import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "tracking.example.yaml"


def _load_env() -> None:
    load_dotenv(REPO_ROOT / "data_sources" / "config" / ".env")
    load_dotenv(REPO_ROOT / ".env")


def _as_decimal(value: Any, default: str) -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0
    jitter: bool = True


@dataclass(frozen=True)
class FreshnessConfig:
    gsc_days: int = 3
    ga4_days: int = 2
    serper_hours: int = 30
    ai_visibility_hours: int = 30


@dataclass(frozen=True)
class TrackingConfig:
    env: str = "development"
    timezone: str = "Asia/Singapore"
    storage_url: str = "sqlite:///data/tracking.db"
    daily_cost_cap_usd: Decimal = Decimal("25")
    run_timeout_seconds: int = 3600
    retry: RetryConfig = field(default_factory=RetryConfig)
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    baseline_days: int = 28
    backfill_days: int = 90
    keyword_catalog_path: str = "config/tracked_keywords.csv"
    ai_question_catalog_path: str = "config/ai_questions.csv"
    brand_terms_path: str = "config/brand_terms.txt"
    ai_referrers_path: str = "config/ai_referrers.yaml"
    log_level: str = "INFO"
    gsc_property: str = ""
    gsc_credentials_path: str = ""
    gsc_country: str = ""
    ga4_property_id: str = ""
    ga4_credentials_path: str = ""
    serper_api_key: str = ""
    serper_gl: str = "sg"
    serper_hl: str = "en"
    serper_location: str = "Singapore"
    serper_results_limit: int = 100
    openai_api_key: str = ""
    openai_visibility_model: str = ""
    perplexity_api_key: str = ""
    perplexity_visibility_model: str = ""
    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_base_app_token: str = ""
    lark_alert_webhook_url: str = ""
    lark_weekly_table_id: str = ""
    lark_opportunities_table_id: str = ""
    lark_alerts_table_id: str = ""
    shopify_shop_domain: str = ""
    shopify_access_token: str = ""
    shopify_api_version: str = "2026-01"
    config_path: str = ""
    catalogue_min_impressions: int = 10
    catalogue_min_clicks_protect: int = 1
    catalogue_selected_limit: int = 55
    catalogue_alternate_limit: int = 15
    catalogue_serper_preselection_limit: int = 100
    catalogue_methodology_version: str = "catalogue_gsc_v1"
    catalogue_relevance_terms: List[str] = field(default_factory=list)
    catalogue_selection_policy: Any = None

    @property
    def sqlite_path(self) -> Path:
        url = self.storage_url
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            path = Path(raw)
            if not path.is_absolute():
                path = REPO_ROOT / path
            return path
        raise ConfigurationError(
            f"Unsupported storage_url scheme. Use sqlite:///... (got a non-sqlite URL)."
        )

    def fingerprint(self) -> str:
        payload = {
            "timezone": self.timezone,
            "daily_cost_cap_usd": str(self.daily_cost_cap_usd),
            "run_timeout_seconds": self.run_timeout_seconds,
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "base_delay_seconds": self.retry.base_delay_seconds,
                "max_delay_seconds": self.retry.max_delay_seconds,
                "jitter": self.retry.jitter,
            },
            "freshness": {
                "gsc_days": self.freshness.gsc_days,
                "ga4_days": self.freshness.ga4_days,
                "serper_hours": self.freshness.serper_hours,
                "ai_visibility_hours": self.freshness.ai_visibility_hours,
            },
            "baseline_days": self.baseline_days,
            "backfill_days": self.backfill_days,
            "gsc_property": self.gsc_property,
            "serper_gl": self.serper_gl,
            "serper_hl": self.serper_hl,
            "serper_results_limit": self.serper_results_limit,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def require(self, *names: str) -> None:
        missing = [name for name in names if not str(getattr(self, name, "")).strip()]
        if missing:
            raise ConfigurationError(
                "Missing required configuration: " + ", ".join(missing)
            )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "env": self.env,
            "timezone": self.timezone,
            "storage_url": self.storage_url,
            "daily_cost_cap_usd": str(self.daily_cost_cap_usd),
            "run_timeout_seconds": self.run_timeout_seconds,
            "baseline_days": self.baseline_days,
            "backfill_days": self.backfill_days,
            "gsc_property": self.gsc_property,
            "ga4_property_configured": bool(self.ga4_property_id),
            "serper_configured": bool(self.serper_api_key),
            "openai_configured": bool(self.openai_api_key),
            "perplexity_configured": bool(self.perplexity_api_key),
            "lark_configured": bool(self.lark_base_app_token),
            "config_fingerprint": self.fingerprint(),
        }


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config file must be a mapping: {path}")
    return data.get("tracking", data)


def load_config(path: Optional[str] = None) -> TrackingConfig:
    _load_env()
    config_path = Path(path or os.getenv("TRACKING_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    raw = _read_yaml(config_path)
    retry_raw = raw.get("retry") or {}
    freshness_raw = raw.get("freshness") or {}
    catalogue_raw = raw.get("catalogue") or {}
    relevance_terms = catalogue_raw.get("relevance_terms") or []
    if not isinstance(relevance_terms, list):
        raise ConfigurationError("catalogue.relevance_terms must be a list")
    from .catalogue.policy import policy_from_catalogue_raw

    selection_policy = policy_from_catalogue_raw(catalogue_raw)
    # Prefer structured policy flat terms; fall back to legacy list for v1 builders.
    flat_terms = list(selection_policy.flat_relevance_terms())
    if relevance_terms and not (catalogue_raw.get("relevance") or {}):
        flat_terms = [str(t).strip().lower() for t in relevance_terms if str(t).strip()]
    return TrackingConfig(
        env=os.getenv("TRACKING_ENV", raw.get("env", "development")),
        timezone=os.getenv("TRACKING_TIMEZONE", raw.get("timezone", "Asia/Singapore")),
        storage_url=os.getenv("TRACKING_DATABASE_URL", raw.get("storage_url", "sqlite:///data/tracking.db")),
        daily_cost_cap_usd=_as_decimal(
            os.getenv("TRACKING_DAILY_COST_CAP_USD", raw.get("daily_cost_cap_usd")), "25"
        ),
        run_timeout_seconds=_as_int(
            os.getenv("TRACKING_RUN_TIMEOUT_SECONDS", raw.get("run_timeout_seconds")), 3600
        ),
        retry=RetryConfig(
            max_attempts=_as_int(retry_raw.get("max_attempts"), 3),
            base_delay_seconds=float(retry_raw.get("base_delay_seconds", 2)),
            max_delay_seconds=float(retry_raw.get("max_delay_seconds", 60)),
            jitter=_as_bool(retry_raw.get("jitter"), True),
        ),
        freshness=FreshnessConfig(
            gsc_days=_as_int(freshness_raw.get("gsc_days"), 3),
            ga4_days=_as_int(freshness_raw.get("ga4_days"), 2),
            serper_hours=_as_int(freshness_raw.get("serper_hours"), 30),
            ai_visibility_hours=_as_int(freshness_raw.get("ai_visibility_hours"), 30),
        ),
        baseline_days=_as_int(raw.get("baseline_days"), 28),
        backfill_days=_as_int(raw.get("backfill_days"), 90),
        keyword_catalog_path=raw.get("keyword_catalog_path", "config/tracked_keywords.csv"),
        ai_question_catalog_path=raw.get("ai_question_catalog_path", "config/ai_questions.csv"),
        brand_terms_path=raw.get("brand_terms_path", "config/brand_terms.txt"),
        ai_referrers_path=raw.get("ai_referrers_path", "config/ai_referrers.yaml"),
        log_level=os.getenv("TRACKING_LOG_LEVEL", raw.get("log_level", "INFO")),
        gsc_property=os.getenv("GSC_PROPERTY", raw.get("gsc_property", "")),
        gsc_credentials_path=os.getenv("GSC_CREDENTIALS_PATH", ""),
        gsc_country=os.getenv("GSC_COUNTRY", raw.get("gsc_country", "")),
        ga4_property_id=os.getenv("GA4_PROPERTY_ID", ""),
        ga4_credentials_path=os.getenv("GA4_CREDENTIALS_PATH", ""),
        serper_api_key=os.getenv("SERPER_API_KEY", ""),
        serper_gl=os.getenv("SERPER_GL", raw.get("serper_gl", "sg")),
        serper_hl=os.getenv("SERPER_HL", raw.get("serper_hl", "en")),
        serper_location=os.getenv("SERPER_LOCATION", raw.get("serper_location", "Singapore")),
        serper_results_limit=_as_int(os.getenv("SERPER_RESULTS_LIMIT", raw.get("serper_results_limit")), 100),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_visibility_model=os.getenv("OPENAI_VISIBILITY_MODEL", raw.get("openai_visibility_model", "")),
        perplexity_api_key=os.getenv("PERPLEXITY_API_KEY", ""),
        perplexity_visibility_model=os.getenv(
            "PERPLEXITY_VISIBILITY_MODEL", raw.get("perplexity_visibility_model", "")
        ),
        lark_app_id=os.getenv("LARK_APP_ID", ""),
        lark_app_secret=os.getenv("LARK_APP_SECRET", ""),
        lark_base_app_token=os.getenv("LARK_BASE_APP_TOKEN", ""),
        lark_alert_webhook_url=os.getenv("LARK_ALERT_WEBHOOK_URL", ""),
        lark_weekly_table_id=os.getenv("LARK_WEEKLY_TABLE_ID", raw.get("lark_weekly_table_id", "")),
        lark_opportunities_table_id=os.getenv(
            "LARK_OPPORTUNITIES_TABLE_ID", raw.get("lark_opportunities_table_id", "")
        ),
        lark_alerts_table_id=os.getenv("LARK_ALERTS_TABLE_ID", raw.get("lark_alerts_table_id", "")),
        shopify_shop_domain=os.getenv("SHOPIFY_SHOP_DOMAIN", os.getenv("SHOPIFY_SHOP", "")),
        shopify_access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
        shopify_api_version=os.getenv("SHOPIFY_API_VERSION", "2026-01"),
        config_path=str(config_path),
        catalogue_min_impressions=selection_policy.min_impressions,
        catalogue_min_clicks_protect=selection_policy.min_clicks_protect,
        catalogue_selected_limit=selection_policy.selected_limit,
        catalogue_alternate_limit=selection_policy.alternate_limit,
        catalogue_serper_preselection_limit=selection_policy.serper_preselection_limit,
        catalogue_methodology_version=selection_policy.methodology_version,
        catalogue_relevance_terms=flat_terms,
        catalogue_selection_policy=selection_policy,
    )
