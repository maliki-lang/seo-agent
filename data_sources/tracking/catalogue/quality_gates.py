"""Portfolio quality gates for catalogue_selection_v2 (Phase 13)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from ..config import TrackingConfig
from ..enums import BrandStatus, CandidateDecision, EligibilityStatus, FamilyRole, TargetPageStatus
from ..storage import TrackingStore
from .policy import CatalogueSelectionPolicy, policy_from_config
from .selection_helpers import ACTIONABLE_TARGETS, is_broad_head_term

# Imported lazily-safe constants — helpers module avoids circular import with selection.py


def _check(
    name: str,
    *,
    ok: bool,
    level: str,
    threshold: str,
    observed: Any,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "check_name": name,
        "ok": bool(ok),
        "level": level,
        "threshold": threshold,
        "observed": observed,
        "details": details or {},
    }


def evaluate_portfolio_gates(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    policy: Optional[CatalogueSelectionPolicy] = None,
    portfolio_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run Phase 13 portfolio checks; returns gate_status pass|warn|failed."""
    selection_policy = policy or policy_from_config(config)
    selected = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )
    ]
    alternates = [
        dict(row)
        for row in store.fetchall(
            """
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND alternate_rank IS NOT NULL
            ORDER BY alternate_rank
            """,
            (build_id,),
        )
    ]

    checks: List[Dict[str, Any]] = []
    lane_counts = Counter(c.get("strategic_lane") or "" for c in selected)
    family_ids = [c.get("family_id") for c in selected if c.get("family_id")]
    broad_count = sum(1 for c in selected if is_broad_head_term(c, selection_policy))

    checks.append(
        _check(
            "selected_count",
            ok=len(selected) == selection_policy.selected_limit,
            level="error",
            threshold=f"exactly {selection_policy.selected_limit}",
            observed=len(selected),
            details={
                "shortages": (portfolio_report or {}).get("lane_shortages"),
                "eligible_shortage": (portfolio_report or {}).get("eligible_shortage"),
            },
        )
    )

    checks.append(
        _check(
            "all_family_primaries",
            ok=all((c.get("family_role") or "") == FamilyRole.PRIMARY.value for c in selected),
            level="error",
            threshold="every selected is family primary",
            observed=sum(1 for c in selected if c.get("family_role") == FamilyRole.PRIMARY.value),
        )
    )
    checks.append(
        _check(
            "unique_families",
            ok=len(family_ids) == len(set(family_ids)) and len(family_ids) == len(selected),
            level="error",
            threshold="one selected primary per family",
            observed={"selected": len(selected), "unique_families": len(set(family_ids))},
        )
    )
    checks.append(
        _check(
            "no_exact_branded",
            ok=all((c.get("brand_status") or "") != BrandStatus.BRANDED.value for c in selected),
            level="error",
            threshold="no branded in non-brand portfolio",
            observed=sum(1 for c in selected if c.get("brand_status") == BrandStatus.BRANDED.value),
        )
    )
    checks.append(
        _check(
            "no_ambiguous_brand",
            ok=all(
                (c.get("brand_status") or "") != BrandStatus.AMBIGUOUS_BRAND.value for c in selected
            ),
            level="error",
            threshold="no ambiguous brand auto-selected",
            observed=sum(
                1 for c in selected if c.get("brand_status") == BrandStatus.AMBIGUOUS_BRAND.value
            ),
        )
    )

    competitor_cap = selection_policy.lane_caps.get("competitor_discovery")
    local_cap = selection_policy.lane_caps.get("local_store")
    broad_cap = selection_policy.lane_caps.get("broad_head_term")
    competitor_n = lane_counts.get("competitor_discovery", 0)
    local_n = lane_counts.get("local_store", 0)
    checks.append(
        _check(
            "competitor_cap",
            ok=competitor_cap is None or competitor_n <= competitor_cap,
            level="error",
            threshold=f"<= {competitor_cap}",
            observed=competitor_n,
        )
    )
    checks.append(
        _check(
            "local_cap",
            ok=local_cap is None or local_n <= local_cap,
            level="error",
            threshold=f"<= {local_cap}",
            observed=local_n,
        )
    )
    checks.append(
        _check(
            "broad_head_cap",
            ok=broad_cap is None or broad_count <= broad_cap,
            level="error",
            threshold=f"<= {broad_cap}",
            observed=broad_count,
        )
    )
    checks.append(
        _check(
            "intent_and_lane_present",
            ok=all(bool(c.get("search_intent")) and bool(c.get("strategic_lane")) for c in selected),
            level="error",
            threshold="search_intent + strategic_lane on every selected",
            observed={
                "missing_intent": sum(1 for c in selected if not c.get("search_intent")),
                "missing_lane": sum(1 for c in selected if not c.get("strategic_lane")),
            },
        )
    )

    non_actionable = [
        c["candidate_id"]
        for c in selected
        if (c.get("target_page_status") or "") not in ACTIONABLE_TARGETS
    ]
    checks.append(
        _check(
            "actionable_targets",
            ok=len(non_actionable) == 0,
            level="error",
            threshold="every selected has actionable target_page_status",
            observed=len(non_actionable),
            details={"examples": non_actionable[:10]},
        )
    )

    if selection_policy.require_serper_for_selection:
        missing_serper = [
            c["candidate_id"]
            for c in selected
            if c.get("serper_position") is None
        ]
        checks.append(
            _check(
                "serper_validation",
                ok=len(missing_serper) == 0,
                level="error",
                threshold="Serper required for every selected",
                observed=len(missing_serper),
                details={"examples": missing_serper[:10]},
            )
        )
    else:
        missing_serper = [c["candidate_id"] for c in selected if c.get("serper_position") is None]
        checks.append(
            _check(
                "serper_validation",
                ok=True,
                level="warning",
                threshold="Serper optional (require_serper_for_selection=false)",
                observed=f"missing={len(missing_serper)}",
            )
        )

    lineage_missing = []
    for c in selected:
        n = store.fetchall(
            "SELECT COUNT(*) AS n FROM keyword_candidate_sources WHERE candidate_id = ?",
            (c["candidate_id"],),
        )[0]["n"]
        if int(n) < 1:
            lineage_missing.append(c["candidate_id"])
    checks.append(
        _check(
            "source_lineage",
            ok=len(lineage_missing) == 0,
            level="error",
            threshold="every selected has >=1 GSC source",
            observed=len(lineage_missing),
            details={"examples": lineage_missing[:10]},
        )
    )

    claims_bad = [
        c["candidate_id"]
        for c in selected
        if int(c.get("claims_review_required") or 0)
        and (c.get("eligibility_status") or "") == EligibilityStatus.ELIGIBLE.value
    ]
    checks.append(
        _check(
            "unsupported_claims",
            ok=len(claims_bad) == 0,
            level="error",
            threshold="claims-required rows must be eligible_with_review (or excluded)",
            observed=len(claims_bad),
        )
    )

    # Warnings
    for lane, quota in selection_policy.lane_quotas.items():
        got = lane_counts.get(lane, 0)
        if got < quota:
            checks.append(
                _check(
                    f"lane_shortage_{lane}",
                    ok=True,
                    level="warning",
                    threshold=f"quota {quota}",
                    observed=got,
                )
            )

    selected_without_alt = 0
    if not alternates:
        selected_without_alt = len(selected)
    checks.append(
        _check(
            "alternates_present",
            ok=len(alternates) > 0 or selection_policy.alternate_limit == 0,
            level="warning",
            threshold=f"up to {selection_policy.alternate_limit} alternates",
            observed=len(alternates),
            details={"selected_without_any_alternates_pool": selected_without_alt},
        )
    )

    homepage_n = sum(
        1
        for c in selected
        if (c.get("target_page_status") or "") == TargetPageStatus.HOMEPAGE_UNRESOLVED.value
    )
    if homepage_n:
        checks.append(
            _check(
                "homepage_observations",
                ok=True,
                level="warning",
                threshold="minimize homepage_unresolved in selected",
                observed=homepage_n,
            )
        )

    errors = [c for c in checks if c["level"] == "error" and not c["ok"]]
    warnings = [c for c in checks if c["level"] == "warning"]
    if errors:
        gate_status = "failed"
    elif any(c["level"] == "warning" and not c["ok"] for c in checks):
        gate_status = "warn"
    else:
        gate_status = "pass" if not warnings else "pass_with_warnings"

    return {
        "build_id": build_id,
        "gate_status": gate_status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "checks": checks,
        "selected_count": len(selected),
        "alternate_count": len(alternates),
        "lane_counts": dict(lane_counts),
    }
