"""Deterministic impact and priority calculations (Phase 15)."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# Approved CTR curve shared with v1 reports (not guarantees).
EXPECTED_CTR = {
    1: 0.316,
    2: 0.157,
    3: 0.105,
    4: 0.075,
    5: 0.059,
    6: 0.048,
    7: 0.041,
    8: 0.035,
    9: 0.031,
    10: 0.027,
    11: 0.018,
    12: 0.015,
    13: 0.013,
    14: 0.012,
    15: 0.011,
    16: 0.010,
    17: 0.009,
    18: 0.008,
    19: 0.008,
    20: 0.007,
}

CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
EFFORT = {"S": 1.0, "M": 2.0, "L": 3.0}


def expected_ctr(position: int) -> float:
    if position <= 0:
        return 0.0
    if position in EXPECTED_CTR:
        return EXPECTED_CTR[position]
    if position > 20:
        return 0.003
    return EXPECTED_CTR.get(min(EXPECTED_CTR.keys(), key=lambda k: abs(k - position)), 0.01)


def estimated_click_gain(
    *,
    impressions: float,
    current_clicks: float,
    target_position: int,
) -> Dict[str, Any]:
    """Raw estimated click gain with assumptions preserved."""
    ctr = expected_ctr(int(target_position))
    expected_clicks = float(impressions) * ctr
    gain = max(0.0, expected_clicks - float(current_clicks))
    return {
        "expected_incremental_clicks": round(gain, 6),
        "expected_clicks_at_target": round(expected_clicks, 6),
        "expected_ctr_at_target": ctr,
        "target_position": int(target_position),
        "impressions": float(impressions),
        "current_clicks": float(current_clicks),
        "assumptions": [
            f"approved_ctr_curve_position_{int(target_position)}={ctr}",
            "gain = impressions * expected_ctr_at_target - current_clicks",
            "not_a_guarantee",
        ],
    }


def priority_from_inputs(
    *,
    estimated_incremental_clicks: float,
    confidence_value: float,
    estimated_cost: Optional[float] = None,
    effort_value: Optional[float] = None,
) -> Dict[str, Any]:
    """
    priority = clicks × confidence ÷ cost   when monetary cost available
    else     = clicks × confidence ÷ effort
    """
    clicks = max(0.0, float(estimated_incremental_clicks))
    conf = max(0.0, float(confidence_value))
    if estimated_cost is not None and float(estimated_cost) > 0:
        denom = float(estimated_cost)
        mode = "monetary_cost"
    else:
        denom = float(effort_value or 0.0)
        mode = "effort_fallback"
    if denom <= 0:
        return {
            "priority_score": 0.0,
            "denominator": denom,
            "mode": mode,
            "inputs": {
                "estimated_incremental_clicks": clicks,
                "confidence_value": conf,
                "estimated_cost": estimated_cost,
                "effort_value": effort_value,
            },
        }
    return {
        "priority_score": round(clicks * conf / denom, 6),
        "denominator": denom,
        "mode": mode,
        "inputs": {
            "estimated_incremental_clicks": clicks,
            "confidence_value": conf,
            "estimated_cost": estimated_cost,
            "effort_value": effort_value,
        },
    }


def recalculate_priority(row: Mapping[str, Any]) -> float:
    """Recompute priority from stored inputs (reproducibility gate)."""
    result = priority_from_inputs(
        estimated_incremental_clicks=float(row.get("expected_incremental_clicks") or 0),
        confidence_value=float(row.get("confidence_value") or 0),
        estimated_cost=row.get("estimated_cost"),
        effort_value=row.get("effort_value"),
    )
    return float(result["priority_score"])
