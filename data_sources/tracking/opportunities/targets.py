"""Target-page URL canonicalization and eligibility helpers (Phase 15/16)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from ..transforms.normalize import canonical_page_key, is_homepage_page_key

# Action types that may enter the portfolio without positive click-impact estimates.
DIAGNOSTIC_ACTIONS = frozenset(
    {
        "technical_fix",
        "product_mapping",
        "manual_investigation",
    }
)

# Human-approved homepage exception: status must be manual_review with an explicit reason.
HOMEPAGE_APPROVED_STATUS = "manual_review"


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def canonicalize_target_url(url: str) -> str:
    """
    Canonical HTTPS URL for opportunity targets.
    Drops query strings and fragments (product-variant params) and normalizes host/path.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(("https", host, path, "", "", ""))


def is_http_target(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme.lower() == "http"


def is_homepage_target(url: str) -> bool:
    if not (url or "").strip():
        return False
    if is_homepage_page_key(url):
        return True
    key = canonical_page_key(canonicalize_target_url(url) or url)
    return is_homepage_page_key(key)


def homepage_explicitly_approved(row: Dict[str, Any]) -> bool:
    status = (row.get("target_page_status") or "").strip()
    evidence = _load_json(row.get("supporting_evidence_json"), {})
    reason = (evidence.get("homepage_approved_reason") or "").strip()
    return status == HOMEPAGE_APPROVED_STATUS and bool(reason)


def is_diagnostic_action(action_type: Optional[str]) -> bool:
    return (action_type or "") in DIAGNOSTIC_ACTIONS


def geo_has_observable_metric(row: Dict[str, Any]) -> bool:
    if row.get("expected_geo_gain") not in (None, ""):
        return float(row.get("expected_geo_gain") or 0) > 0
    evidence = _load_json(row.get("supporting_evidence_json"), {})
    return bool(
        evidence.get("ai_referral_sessions")
        or evidence.get("website_citation_rate")
        or evidence.get("observable_referral_metric")
    )


def validate_target_for_portfolio(
    row: Dict[str, Any],
    *,
    minimum_incremental_clicks: float = 1.0,
    minimum_priority_score: float = 0.01,
) -> Tuple[bool, Optional[str]]:
    """Return (ok, reject_reason) for absolute portfolio eligibility."""
    page = canonicalize_target_url(row.get("target_page") or "") or (row.get("target_page") or "")
    action = row.get("action_type") or ""
    category = row.get("category") or ""

    if page and is_http_target(row.get("target_page") or ""):
        return False, "http_target_blocked"

    if page and is_homepage_target(page) and not homepage_explicitly_approved(row):
        return False, "homepage_not_explicitly_approved"

    clicks = row.get("expected_incremental_clicks")
    click_ok = clicks is not None and float(clicks) >= float(minimum_incremental_clicks)
    diagnostic_ok = is_diagnostic_action(action)
    geo_ok = category == "geo" and geo_has_observable_metric(row)

    if not (click_ok or diagnostic_ok or geo_ok):
        return False, "insufficient_impact_or_diagnostic"

    priority = float(row.get("priority_score") or 0)
    if priority < float(minimum_priority_score):
        return False, "priority_below_minimum"

    return True, None
