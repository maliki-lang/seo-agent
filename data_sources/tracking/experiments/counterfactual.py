"""Counterfactual traffic estimation hierarchy (Phase 16 MVP)."""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from ..enums import CounterfactualMethod


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def estimate_expected_clicks(
    *,
    target_baseline_clicks: float,
    target_observed_clicks: float,
    control_baseline_clicks: Sequence[float] = (),
    control_post_clicks: Sequence[float] = (),
    sitewide_baseline_clicks: Optional[float] = None,
    sitewide_post_clicks: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Method A: matched controls (median of post/baseline ratios).
    Method B: site-wide adjustment excluding target.
    Method C: before/after only (confidence capped low).
    """
    assumptions: List[str] = []
    control_factors: List[float] = []
    if (
        len(control_baseline_clicks) >= 2
        and len(control_post_clicks) == len(control_baseline_clicks)
    ):
        for base, post in zip(control_baseline_clicks, control_post_clicks):
            ratio = _safe_ratio(float(post), float(base))
            if ratio is not None:
                control_factors.append(ratio)
        if len(control_factors) >= 2:
            factor = float(median(control_factors))
            expected = float(target_baseline_clicks) * factor
            assumptions.extend(
                [
                    "method=matched_controls",
                    f"control_trend_factor_median={factor}",
                    f"control_pages_used={len(control_factors)}",
                ]
            )
            incremental = float(target_observed_clicks) - expected
            return {
                "method": CounterfactualMethod.MATCHED_CONTROLS.value,
                "expected_clicks_without_change": round(expected, 6),
                "adjusted_incremental_clicks": round(incremental, 6),
                "control_trend_factor": round(factor, 6),
                "sitewide_trend_factor": None,
                "confidence_cap": None,
                "assumptions": assumptions,
            }

    if (
        sitewide_baseline_clicks is not None
        and sitewide_post_clicks is not None
        and float(sitewide_baseline_clicks) > 0
    ):
        factor = float(sitewide_post_clicks) / float(sitewide_baseline_clicks)
        expected = float(target_baseline_clicks) * factor
        assumptions.extend(
            [
                "method=sitewide_adjusted",
                f"sitewide_trend_factor={factor}",
                "target_page_excluded_from_sitewide",
            ]
        )
        incremental = float(target_observed_clicks) - expected
        return {
            "method": CounterfactualMethod.SITEWIDE_ADJUSTED.value,
            "expected_clicks_without_change": round(expected, 6),
            "adjusted_incremental_clicks": round(incremental, 6),
            "control_trend_factor": None,
            "sitewide_trend_factor": round(factor, 6),
            "confidence_cap": None,
            "assumptions": assumptions,
        }

    expected = float(target_baseline_clicks)
    assumptions.extend(
        [
            "method=before_after",
            "no_valid_controls_or_sitewide_factor",
            "confidence_cannot_exceed_low",
        ]
    )
    incremental = float(target_observed_clicks) - expected
    return {
        "method": CounterfactualMethod.BEFORE_AFTER.value,
        "expected_clicks_without_change": round(expected, 6),
        "adjusted_incremental_clicks": round(incremental, 6),
        "control_trend_factor": None,
        "sitewide_trend_factor": None,
        "confidence_cap": "low",
        "assumptions": assumptions,
    }
