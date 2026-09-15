"""
Scorecard calibration (Production monitoring, Layer 6).

The publish-time scorecard is a *prediction* ("this should rank"). The
post-publish checkpoint is the *outcome* (it ranked / earned clicks or didn't).
Calibration asks the question the prior agent never asked: **is the score itself
any good?**

It joins eval_log.jsonl (publish-time totals per slug) with checkpoints.jsonl
(post-publish outcomes per slug) and reports whether higher-scoring drafts
actually produced better outcomes. A weak/negative relationship means the
scorecard is miscalibrated and the weights or graders need adjusting.

Usage:
    python3 -m data_sources.modules.eval.calibration
"""

import sys
from statistics import mean
from typing import Any, Dict, List

from .scorecard import read_eval_log
from ..ledger import read_checkpoints

# Outcome -> numeric quality for correlation.
OUTCOME_VALUE = {"winner": 1.0, "mover": 0.5, "flat": 0.0, "loser": -1.0}


def _publish_score_by_slug() -> Dict[str, float]:
    """Latest publish/draft total per slug from the eval log."""
    latest: Dict[str, Dict[str, Any]] = {}
    for row in read_eval_log():
        slug = row.get("slug")
        if not slug:
            continue
        # Prefer the publish-step score; else the most recent draft score.
        prev = latest.get(slug)
        if prev is None or row.get("recorded_at", "") >= prev.get("recorded_at", ""):
            latest[slug] = row
    return {s: r.get("total", 0.0) for s, r in latest.items()}


def _outcome_by_slug() -> Dict[str, float]:
    latest: Dict[str, Dict[str, Any]] = {}
    for c in read_checkpoints():
        slug = c.get("slug")
        if not slug:
            continue
        prev = latest.get(slug)
        if prev is None or c.get("milestone_day", 0) >= prev.get("milestone_day", 0):
            latest[slug] = c
    return {
        s: OUTCOME_VALUE.get(c.get("outcome"), 0.0)
        for s, c in latest.items()
        if c.get("outcome") in OUTCOME_VALUE
    }


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def calibrate() -> Dict[str, Any]:
    scores = _publish_score_by_slug()
    outcomes = _outcome_by_slug()
    joined = [(scores[s], outcomes[s]) for s in scores if s in outcomes]

    if not joined:
        return {"n": 0, "status": "no_data",
                "message": "No slugs with both a score and an outcome yet. "
                           "Calibration becomes meaningful after enough "
                           "published articles reach a checkpoint."}

    xs = [x for x, _ in joined]
    ys = [y for _, y in joined]
    r = round(_pearson(xs, ys), 3)

    # Bucket: did high-scored (>=85) articles beat the rest?
    high = [y for x, y in joined if x >= 85]
    rest = [y for x, y in joined if x < 85]
    high_mean = round(mean(high), 3) if high else None
    rest_mean = round(mean(rest), 3) if rest else None

    if len(joined) < 5:
        status = "insufficient"
    elif r >= 0.3:
        status = "calibrated"
    elif r >= 0.0:
        status = "weak"
    else:
        status = "miscalibrated"

    return {
        "n": len(joined),
        "correlation": r,
        "high_score_outcome_mean": high_mean,
        "low_score_outcome_mean": rest_mean,
        "status": status,
        "message": {
            "calibrated": "Score predicts outcome — keep monitoring.",
            "weak": "Weak relationship — watch; consider grader/weight tweaks.",
            "miscalibrated": "Higher scores are NOT producing better outcomes. "
                             "Adjust dimension weights or graders.",
            "insufficient": "Too few paired data points (<5) for a verdict.",
        }[status],
    }


def main() -> int:
    rep = calibrate()
    print("=== Scorecard Calibration ===")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
