"""Experiment creation gates and state-machine helpers (Phase 16)."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Set

from ..enums import ExperimentStatus, OpportunityReviewStatus
from ..exceptions import DataQualityError
from ..opportunities.targets import (
    DIAGNOSTIC_ACTIONS,
    canonicalize_target_url,
    geo_has_observable_metric,
    homepage_explicitly_approved,
    is_homepage_target,
    is_http_target,
)

ACTIVE_EXPERIMENT_STATUSES: FrozenSet[str] = frozenset(
    {
        ExperimentStatus.DRAFT.value,
        ExperimentStatus.APPROVED.value,
        ExperimentStatus.IMPLEMENTING.value,
        ExperimentStatus.PUBLISHED.value,
        ExperimentStatus.MEASURING.value,
        ExperimentStatus.WINNER.value,
        ExperimentStatus.LIKELY_WINNER.value,
        ExperimentStatus.INCONCLUSIVE.value,
        ExperimentStatus.LIKELY_LOSS.value,
        ExperimentStatus.TRACKING_FAILURE.value,
    }
)

TERMINAL_EXPERIMENT_STATUSES: FrozenSet[str] = frozenset(
    {
        ExperimentStatus.CLOSED.value,
    }
)

ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    ExperimentStatus.DRAFT.value: {
        ExperimentStatus.APPROVED.value,
        ExperimentStatus.IMPLEMENTING.value,
        ExperimentStatus.CLOSED.value,
    },
    ExperimentStatus.APPROVED.value: {
        ExperimentStatus.IMPLEMENTING.value,
        ExperimentStatus.PUBLISHED.value,
        ExperimentStatus.CLOSED.value,
    },
    ExperimentStatus.IMPLEMENTING.value: {
        ExperimentStatus.PUBLISHED.value,
        ExperimentStatus.CLOSED.value,
    },
    ExperimentStatus.PUBLISHED.value: {
        ExperimentStatus.MEASURING.value,
        ExperimentStatus.TRACKING_FAILURE.value,
        ExperimentStatus.CLOSED.value,
    },
    ExperimentStatus.MEASURING.value: {
        ExperimentStatus.WINNER.value,
        ExperimentStatus.LIKELY_WINNER.value,
        ExperimentStatus.INCONCLUSIVE.value,
        ExperimentStatus.LIKELY_LOSS.value,
        ExperimentStatus.TRACKING_FAILURE.value,
        ExperimentStatus.MEASURING.value,
        ExperimentStatus.CLOSED.value,
    },
    ExperimentStatus.WINNER.value: {ExperimentStatus.CLOSED.value},
    ExperimentStatus.LIKELY_WINNER.value: {ExperimentStatus.CLOSED.value, ExperimentStatus.MEASURING.value},
    ExperimentStatus.INCONCLUSIVE.value: {ExperimentStatus.CLOSED.value, ExperimentStatus.MEASURING.value},
    ExperimentStatus.LIKELY_LOSS.value: {ExperimentStatus.CLOSED.value},
    ExperimentStatus.TRACKING_FAILURE.value: {ExperimentStatus.CLOSED.value, ExperimentStatus.MEASURING.value},
}


def assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current) or set()
    if target not in allowed and target != current:
        raise DataQualityError(f"Invalid experiment transition {current} → {target}")


def validate_opportunity_for_experiment(
    opportunity: Dict[str, Any],
    *,
    minimum_incremental_clicks: float = 1.0,
    homepage_approved_reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return gate results; raises DataQualityError on critical failure."""
    gates: List[Dict[str, Any]] = []

    def _gate(name: str, ok: bool, detail: Any = None) -> None:
        gates.append({"gate": name, "ok": bool(ok), "detail": detail})

    review = opportunity.get("review_status")
    reviewed_by = (opportunity.get("reviewed_by") or "").strip()
    reviewed_at = opportunity.get("reviewed_at")
    approved = review == OpportunityReviewStatus.APPROVED.value
    _gate("review_status_approved", approved, review)
    _gate("reviewed_by_present", bool(reviewed_by), reviewed_by or None)
    _gate("reviewed_at_present", bool(reviewed_at), reviewed_at)

    page = canonicalize_target_url(opportunity.get("target_page") or "") or (
        opportunity.get("target_page") or ""
    )
    _gate("https_canonical_target", not is_http_target(opportunity.get("target_page") or ""), page)

    homepage = is_homepage_target(page) if page else False
    homepage_ok = True
    if homepage:
        evidence = dict(opportunity.get("supporting_evidence_json") or {})
        if isinstance(opportunity.get("supporting_evidence_json"), str):
            import json

            try:
                evidence = json.loads(opportunity["supporting_evidence_json"])
            except (TypeError, json.JSONDecodeError):
                evidence = {}
        if homepage_approved_reason:
            evidence = {**evidence, "homepage_approved_reason": homepage_approved_reason}
            opportunity = {**opportunity, "supporting_evidence_json": evidence}
        homepage_ok = homepage_explicitly_approved(opportunity)
    _gate("homepage_explicit_approval", not homepage or homepage_ok, {"homepage": homepage})

    action = opportunity.get("action_type") or ""
    clicks = opportunity.get("expected_incremental_clicks")
    diagnostic = action in DIAGNOSTIC_ACTIONS
    geo_ok = (opportunity.get("category") == "geo") and geo_has_observable_metric(opportunity)
    click_ok = clicks is not None and float(clicks) >= float(minimum_incremental_clicks)
    _gate(
        "expected_impact",
        diagnostic or geo_ok or click_ok,
        {
            "expected_incremental_clicks": clicks,
            "diagnostic": diagnostic,
            "geo_observable": geo_ok,
            "minimum": minimum_incremental_clicks,
        },
    )

    critical = [g for g in gates if not g["ok"]]
    if critical:
        raise DataQualityError(
            "Experiment creation gates failed: "
            + ", ".join(g["gate"] for g in critical)
        )
    return gates
