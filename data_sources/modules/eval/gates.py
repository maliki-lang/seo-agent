"""
Gates — turn a Scorecard into an enforced decision for a pipeline step.

The decision is code, not advice: callers use `.should_block` / `.should_regenerate`
to actually halt or loop. Per-step minimum bars can be stricter than the global
band (e.g. publish requires ship-ready; a YMYL draft can never pass with a hard
fail).
"""

from dataclasses import dataclass
from typing import List

from .scorecard import Scorecard, BAND_SHIP, BAND_USABLE, BAND_AT_RISK


# Minimum total a step must clear, on top of "no hard fail".
STEP_MIN = {
    "topic_selection": BAND_AT_RISK,    # 50 — weak topics get owner review
    "research_brief": BAND_USABLE,      # 70
    "draft": BAND_USABLE,               # 70 — regenerate below this
    "optimize": BAND_USABLE,            # 70
    "publish": BAND_SHIP,               # 85 — only ship-ready content goes live
}


@dataclass
class GateResult:
    step: str
    decision: str          # ship | ship_with_fixes | owner_fix | block
    total: float
    passed: bool
    hard_fail: bool
    reasons: List[str]

    @property
    def should_block(self) -> bool:
        return self.decision == "block"

    @property
    def should_regenerate(self) -> bool:
        # Below the step minimum but not a hard block -> loop and improve.
        return self.decision in ("ship_with_fixes", "owner_fix") and not self.passed

    def summary(self) -> str:
        head = f"[{self.step}] {self.decision.upper()} — {self.total}/100"
        if self.reasons:
            head += "\n  - " + "\n  - ".join(self.reasons[:6])
        return head


def gate(scorecard: Scorecard, step: str = "") -> GateResult:
    """Evaluate a Scorecard against the bar for its step."""
    step = step or scorecard.step
    minimum = STEP_MIN.get(step, BAND_USABLE)

    reasons: List[str] = []
    if scorecard.hard_fail:
        reasons.extend(scorecard.hard_fail_reasons)
        decision = "block"
        passed = False
    else:
        total = scorecard.total
        passed = total >= minimum
        decision = scorecard.band
        if not passed:
            reasons.append(
                f"total {total} below step minimum {minimum} for '{step}'"
            )
            # Surface the lowest dimensions so the regenerate loop has direction.
            low = sorted(scorecard.scores.items(), key=lambda kv: kv[1])[:3]
            for dim, s in low:
                if s < minimum:
                    notes = "; ".join(scorecard.notes.get(dim, [])[:2])
                    reasons.append(f"{dim}={s}" + (f" ({notes})" if notes else ""))

    return GateResult(
        step=step,
        decision=decision,
        total=scorecard.total,
        passed=passed and not scorecard.hard_fail,
        hard_fail=scorecard.hard_fail,
        reasons=reasons,
    )
