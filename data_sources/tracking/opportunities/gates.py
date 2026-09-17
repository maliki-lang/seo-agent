"""Opportunity quality gates (Phase 15)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..enums import OpportunityReviewStatus
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .impact import recalculate_priority


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


def evaluate_opportunity_gates(store: TrackingStore, *, report_id: str) -> Dict[str, Any]:
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
    for r in rows:
        page = r.get("target_page") or ""
        status = r.get("target_page_status") or ""
        if r.get("action_type") == "new_page":
            continue
        if page and not _sunnystep_url(page) and status not in {"manual_review", "approved_new_page"}:
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

    # Duplicate page+action pairs among selected
    seen = {}
    dups = []
    for r in rows:
        key = ((r.get("target_page") or "").lower(), r.get("action_type") or "")
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
        and r.get("action_type") not in {"manual_investigation", "product_mapping"}
    ]
    _check(
        "raw_impact_preservation",
        ok=not missing_raw,
        level="critical",
        observed={"missing_raw_gain": len(missing_raw)},
        details={"ids": missing_raw[:20]},
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

    # Portfolio size soft check
    _check(
        "portfolio_constraints",
        ok=len(rows) <= 10,
        level="error",
        observed={"selected": len(rows)},
        details={},
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

    # Human approval is enforced at experiment creation (Phase 16), not at build time.
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
    # llm_diagnosis_validity is only critical when llm was requested; if all are awaiting_human_review without llm ids, pass.
    if all(
        (r.get("review_status") or "")
        in {
            OpportunityReviewStatus.AWAITING_HUMAN_REVIEW.value,
            OpportunityReviewStatus.APPROVED.value,
            OpportunityReviewStatus.REJECTED.value,
            OpportunityReviewStatus.PENDING.value,
        }
        for r in rows
    ) and not any(r.get("blocked_reason") for r in rows):
        # Recompute critical without treating empty llm as failure when diagnosis not required.
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
