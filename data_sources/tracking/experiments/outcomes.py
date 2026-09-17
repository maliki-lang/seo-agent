"""Outcome classification for experiment checkpoints (Phase 16)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import EconomicsConfig
from ..enums import ExperimentOutcome
from .costs import cost_per_incremental_click


def classify_outcome(
    *,
    checkpoint_days: int,
    adjusted_incremental_clicks: Optional[float],
    baseline_clicks: Optional[float],
    observed_clicks: Optional[float],
    confidence_label: str,
    quality_status: str,
    isolation_quality: str = "high",
    actual_cost: Optional[float] = None,
    economics: Optional[EconomicsConfig] = None,
) -> Dict[str, Any]:
    economics = economics or EconomicsConfig()
    reasons = []

    if quality_status != "pass":
        return {
            "outcome": ExperimentOutcome.TRACKING_FAILURE.value,
            "finalizable": checkpoint_days >= 28,
            "reasons": ["quality_gate_failed", f"quality_status={quality_status}"],
            "actual_cost_per_incremental_click": None,
        }

    if baseline_clicks is None or float(baseline_clicks) <= 0:
        return {
            "outcome": ExperimentOutcome.INCONCLUSIVE.value,
            "finalizable": checkpoint_days >= 28,
            "reasons": ["sparse_or_zero_baseline"],
            "actual_cost_per_incremental_click": None,
        }

    if adjusted_incremental_clicks is None or observed_clicks is None:
        return {
            "outcome": ExperimentOutcome.TRACKING_FAILURE.value,
            "finalizable": checkpoint_days >= 28,
            "reasons": ["missing_click_windows"],
            "actual_cost_per_incremental_click": None,
        }

    lift = float(adjusted_incremental_clicks) / float(baseline_clicks)
    cost_per = cost_per_incremental_click(
        cost=actual_cost, incremental_clicks=adjusted_incremental_clicks
    )
    max_cost = economics.max_cost_per_incremental_click
    cost_ok = max_cost is None or (cost_per is not None and cost_per <= float(max_cost))

    if isolation_quality == "low":
        reasons.append("isolation_quality_low")
        return {
            "outcome": ExperimentOutcome.INCONCLUSIVE.value,
            "finalizable": checkpoint_days >= 28,
            "reasons": reasons + ["bundled_changes_prevent_confirmed_winner"],
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    if confidence_label == "low":
        reasons.append("confidence_low")
        return {
            "outcome": ExperimentOutcome.INCONCLUSIVE.value
            if checkpoint_days >= 28
            else ExperimentOutcome.PROVISIONAL.value,
            "finalizable": False,
            "reasons": reasons,
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    # Day-14 is always provisional for winners.
    if checkpoint_days < 28:
        if float(adjusted_incremental_clicks) < 0:
            outcome = ExperimentOutcome.PROVISIONAL.value
            reasons.append("negative_early_signal")
        elif float(adjusted_incremental_clicks) > 0:
            outcome = ExperimentOutcome.PROVISIONAL.value
            reasons.append("day14_cannot_finalize_winner")
        else:
            outcome = ExperimentOutcome.PROVISIONAL.value
            reasons.append("flat_early_signal")
        return {
            "outcome": outcome,
            "finalizable": False,
            "reasons": reasons,
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    # 28 / 56 day classification
    if (
        lift >= float(economics.winner_min_lift_pct)
        and float(adjusted_incremental_clicks) > 0
        and confidence_label in {"high", "medium"}
        and cost_ok
        and quality_status == "pass"
    ):
        if confidence_label == "high" and cost_ok and isolation_quality == "high":
            return {
                "outcome": ExperimentOutcome.WINNER.value,
                "finalizable": True,
                "reasons": ["meets_winner_thresholds"],
                "actual_cost_per_incremental_click": cost_per,
                "lift_pct": round(lift, 6),
            }
        return {
            "outcome": ExperimentOutcome.LIKELY_WINNER.value,
            "finalizable": True,
            "reasons": ["positive_but_incomplete_economic_or_confidence"],
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    if float(adjusted_incremental_clicks) > 0 and confidence_label == "medium":
        return {
            "outcome": ExperimentOutcome.LIKELY_WINNER.value,
            "finalizable": True,
            "reasons": ["positive_medium_confidence"],
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    if float(adjusted_incremental_clicks) < 0:
        return {
            "outcome": ExperimentOutcome.LIKELY_LOSS.value,
            "finalizable": True,
            "reasons": ["negative_adjusted_incremental_clicks"],
            "actual_cost_per_incremental_click": cost_per,
            "lift_pct": round(lift, 6),
        }

    return {
        "outcome": ExperimentOutcome.INCONCLUSIVE.value,
        "finalizable": True,
        "reasons": ["mixed_or_insufficient_lift", f"lift_pct={round(lift, 6)}"],
        "actual_cost_per_incremental_click": cost_per,
        "lift_pct": round(lift, 6),
    }
