"""Opportunity quality gates (Phase 15/16)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..enums import OpportunityReviewStatus
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .impact import recalculate_priority
from .targets import (
    DIAGNOSTIC_ACTIONS,
    canonicalize_target_url,
    homepage_explicitly_approved,
    is_homepage_target,
    is_http_target,
)


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sunnystep_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return parsed.scheme in {"http", "https"} and (
        host == "sunnystep.com" or host.endswith(".sunnystep.com") or host == "gosunnystep.myshopify.com"
    )


def evaluate_opportunity_gates(
    store: TrackingStore,
    *,
    report_id: str,
    minimum_incremental_clicks: float = 1.0,
    minimum_priority_score: float = 0.01,
) -> Dict[str, Any]:
    rows = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM opportunities WHERE report_id = ? AND opportunity_version = 'v2'",
            (report_id,),
        )
    ]
    checks: List[Dict[str, Any]] = []

    def _check(name: str, *, ok: bool, level: str, observed: Any, details: Optional[Dict] = None):
        checks.append(
            {
                "check_name": name,
                "ok": bool(ok),
                "level": level,
                "observed": observed,
                "details": details or {},
            }
        )

    missing_refs = [
        r["opportunity_id"]
        for r in rows
        if not _load_json(r.get("source_row_references_json"), [])
    ]
    _check(
        "evidence_lineage",
        ok=not missing_refs,
        level="critical",
        observed={"missing": len(missing_refs)},
        details={"ids": missing_refs[:20]},
    )

    bad_targets = []
    http_targets = []
    homepage_blocked = []
    for r in rows:
        page = r.get("target_page") or ""
        status = r.get("target_page_status") or ""
        if r.get("action_type") == "new_page":
            continue
        if page and is_http_target(page):
            http_targets.append(r["opportunity_id"])
        if page and is_homepage_target(page) and not homepage_explicitly_approved(r):
            homepage_blocked.append(r["opportunity_id"])
        canonical = canonicalize_target_url(page) if page else ""
        check_page = canonical or page
        if check_page and not _sunnystep_url(check_page) and status not in {
            "manual_review",
            "approved_new_page",
        }:
            bad_targets.append(r["opportunity_id"])
        if not page and status not in {"approved_new_page", "manual_review", "no_sensible_target"}:
            bad_targets.append(r["opportunity_id"])
    _check(
        "target_validity",
        ok=not bad_targets,
        level="critical",
        observed={"invalid": len(bad_targets)},
        details={"ids": bad_targets[:20]},
    )
    _check(
        "https_canonical_targets",
        ok=not http_targets,
        level="critical",
        observed={"http_targets": len(http_targets)},
        details={"ids": http_targets[:20]},
    )
    _check(
        "homepage_explicit_approval",
        ok=not homepage_blocked,
        level="critical",
        observed={"blocked_homepages": len(homepage_blocked)},
        details={"ids": homepage_blocked[:20]},
    )

    incomplete = [
        r["opportunity_id"]
        for r in rows
        if not (r.get("problem") and r.get("proposed_action") and r.get("metric_to_watch") and r.get("measurement_window_json"))
    ]
    _check(
        "actionability",
        ok=not incomplete,
        level="critical",
        observed={"incomplete": len(incomplete)},
        details={"ids": incomplete[:20]},
    )

    seen = {}
    dups = []
    for r in rows:
        page = canonicalize_target_url(r.get("target_page") or "") or (r.get("target_page") or "")
        key = (page.lower(), r.get("action_type") or "")
        if key in seen and key[0]:
            dups.append(r["opportunity_id"])
        seen[key] = r["opportunity_id"]
    _check(
        "duplicate_action",
        ok=not dups,
        level="error",
        observed={"duplicates": len(dups)},
        details={"ids": dups[:20]},
    )

    missing_raw = [
        r["opportunity_id"]
        for r in rows
        if r.get("category") == "seo"
        and r.get("expected_incremental_clicks") is None
        and r.get("action_type") not in DIAGNOSTIC_ACTIONS
        and r.get("action_type") != "geo_evidence_upgrade"
    ]
    _check(
        "raw_impact_preservation",
        ok=not missing_raw,
        level="critical",
        observed={"missing_raw_gain": len(missing_raw)},
        details={"ids": missing_raw[:20]},
    )

    weak_impact = []
    for r in rows:
        action = r.get("action_type") or ""
        if action in DIAGNOSTIC_ACTIONS:
            continue
        if r.get("category") == "geo":
            continue
        clicks = r.get("expected_incremental_clicks")
        if clicks is None or float(clicks) < float(minimum_incremental_clicks):
            weak_impact.append(r["opportunity_id"])
    _check(
        "minimum_incremental_clicks",
        ok=not weak_impact,
        level="critical",
        observed={"below_minimum": len(weak_impact), "threshold": minimum_incremental_clicks},
        details={"ids": weak_impact[:20]},
    )

    low_priority = [
        r["opportunity_id"]
        for r in rows
        if float(r.get("priority_score") or 0) < float(minimum_priority_score)
    ]
    _check(
        "minimum_priority_score",
        ok=not low_priority,
        level="critical",
        observed={"below_minimum": len(low_priority), "threshold": minimum_priority_score},
        details={"ids": low_priority[:20]},
    )

    bad_priority = []
    for r in rows:
        stored = float(r.get("priority_score") or 0)
        recomputed = recalculate_priority(r)
        if abs(stored - recomputed) > 1e-6:
            bad_priority.append(r["opportunity_id"])
    _check(
        "priority_reproducibility",
        ok=not bad_priority,
        level="critical",
        observed={"mismatch": len(bad_priority)},
        details={"ids": bad_priority[:20]},
    )

    # Soft upper bound only — portfolios may be smaller than ten.
    _check(
        "portfolio_constraints",
        ok=len(rows) <= 10,
        level="error",
        observed={"selected": len(rows)},
        details={"note": "up_to_ten_not_fill_ten"},
    )

    source_counts = Counter(r.get("source_type") or "" for r in rows)
    concentration = None
    if rows:
        top_source, top_count = source_counts.most_common(1)[0]
        share = top_count / len(rows)
        if share > 0.40:
            concentration = {"source_type": top_source, "share": round(share, 4), "count": top_count}
    _check(
        "source_concentration",
        ok=concentration is None,
        level="warning",
        observed=concentration or {"ok": True},
        details={},
    )

    cannibal_weak = []
    for r in rows:
        if r.get("source_type") != "cannibalization":
            continue
        evidence = _load_json(r.get("supporting_evidence_json"), {})
        url_count = int(evidence.get("distinct_url_count") or len(evidence.get("distinct_sunnystep_urls") or []))
        impressions = int(evidence.get("impressions") or 0)
        if url_count < 2 or impressions < 20:
            cannibal_weak.append(r["opportunity_id"])
    _check(
        "cannibalization_evidence",
        ok=not cannibal_weak,
        level="critical",
        observed={"weak": len(cannibal_weak)},
        details={"ids": cannibal_weak[:20]},
    )

    awaiting_llm = [
        r["opportunity_id"]
        for r in rows
        if r.get("review_status") == OpportunityReviewStatus.AWAITING_LLM_DIAGNOSIS.value
    ]
    invalid_llm = [
        r["opportunity_id"]
        for r in rows
        if r.get("blocked_reason") == "llm_diagnosis_invalid_or_unavailable"
    ]
    _check(
        "llm_diagnosis_validity",
        ok=not awaiting_llm and not invalid_llm,
        level="critical",
        observed={"awaiting": len(awaiting_llm), "invalid": len(invalid_llm)},
        details={"ids": (awaiting_llm + invalid_llm)[:20]},
    )

    unapproved = [
        r["opportunity_id"]
        for r in rows
        if r.get("review_status") == OpportunityReviewStatus.APPROVED.value and not r.get("reviewed_by")
    ]
    _check(
        "human_approval_fields",
        ok=not unapproved,
        level="critical",
        observed={"approved_missing_reviewer": len(unapproved)},
        details={"ids": unapproved[:20]},
    )

    critical_fail = any(not c["ok"] and c["level"] == "critical" for c in checks)
    if all(
        (r.get("review_status") or "")
        in {
            OpportunityReviewStatus.AWAITING_HUMAN_REVIEW.value,
            OpportunityReviewStatus.APPROVED.value,
            OpportunityReviewStatus.REJECTED.value,
            OpportunityReviewStatus.PENDING.value,
            OpportunityReviewStatus.DEFERRED.value,
            OpportunityReviewStatus.CONVERTED_TO_EXPERIMENT.value,
        }
        for r in rows
    ) and not any(r.get("blocked_reason") for r in rows):
        for c in checks:
            if c["check_name"] == "llm_diagnosis_validity" and not any(r.get("llm_assessment_id") for r in rows):
                c["ok"] = True
                c["observed"] = {"skipped": True, "reason": "llm_assist_not_enabled"}
        critical_fail = any(not c["ok"] and c["level"] == "critical" for c in checks)

    error_fail = any(not c["ok"] and c["level"] == "error" for c in checks)
    return {
        "report_id": report_id,
        "selected_count": len(rows),
        "gate_status": "failed" if critical_fail or error_fail else "pass",
        "critical_failures": sum(1 for c in checks if not c["ok"] and c["level"] == "critical"),
        "error_failures": sum(1 for c in checks if not c["ok"] and c["level"] == "error"),
        "checks": checks,
        "checked_at": utc_now_iso(),
    }
