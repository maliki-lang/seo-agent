"""
Learned Weights

Closes the rank/organic-impact feedback loop. Reads checkpoints.jsonl (written
by track_published.py) and computes per-cluster and per-opportunity-type
"positive rate" multipliers that the OpportunityScorer applies to its final
score.

Multipliers are bounded to [1 - CAP, 1 + CAP] so a small sample or a few bad
data points cannot wildly distort prioritization. When sample size is below
MIN_SAMPLE for a given key, get_weight() returns 1.0 (neutral) — the loop is
wired but inert until enough data has accumulated.

This is the missing link between "we measured what worked" and "the system
prioritizes future picks based on what worked."
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .ledger import LEDGER_DIR, read_checkpoints


WEIGHTS_FILE = LEDGER_DIR / "learned_weights.json"

CAP = 0.15        # ± multiplier bound (e.g. 0.15 = ±15%)
MIN_SAMPLE = 3    # min checkpoints in a group before any nudge applies


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_per_slug(checkpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse multiple checkpoints per article into the latest milestone."""
    latest: Dict[str, Dict[str, Any]] = {}
    for c in checkpoints:
        slug = c.get("slug")
        if not slug:
            continue
        prev = latest.get(slug)
        if prev is None or c.get("milestone_day", 0) > prev.get("milestone_day", 0):
            latest[slug] = c
    return list(latest.values())


def _positive_rate(group: List[Dict[str, Any]]) -> tuple:
    """Winners = full credit, movers = half credit, flat/losers = zero."""
    n = len(group)
    if n == 0:
        return 0.0, 0
    score = 0.0
    for c in group:
        outcome = c.get("outcome")
        if outcome == "winner":
            score += 1.0
        elif outcome == "mover":
            score += 0.5
    return score / n, n


def _multiplier_from_rate(rate: float, sample_size: int) -> float:
    """Map positive_rate ∈ [0,1] → multiplier ∈ [1-CAP, 1+CAP]. Neutral below MIN_SAMPLE."""
    if sample_size < MIN_SAMPLE:
        return 1.0
    return round(1.0 + CAP * (2.0 * rate - 1.0), 4)


def _summarize_group(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    rate, n = _positive_rate(group)
    return {
        "n": n,
        "winners": sum(1 for c in group if c.get("outcome") == "winner"),
        "movers": sum(1 for c in group if c.get("outcome") == "mover"),
        "flat": sum(1 for c in group if c.get("outcome") == "flat"),
        "losers": sum(1 for c in group if c.get("outcome") == "loser"),
        "positive_rate": round(rate, 3),
        "multiplier": _multiplier_from_rate(rate, n),
    }


def refresh() -> Dict[str, Any]:
    """
    Re-compute weights from current checkpoints.jsonl and write to disk.
    Safe to call repeatedly; idempotent for a given checkpoints state.
    """
    checkpoints = _latest_per_slug(read_checkpoints())

    cluster_groups: Dict[str, List[Dict[str, Any]]] = {}
    type_groups: Dict[str, List[Dict[str, Any]]] = {}

    for c in checkpoints:
        cluster = (c.get("cluster") or "").strip().lower()
        otype = (c.get("opportunity_type") or "").strip().lower()
        if cluster:
            cluster_groups.setdefault(cluster, []).append(c)
        if otype:
            type_groups.setdefault(otype, []).append(c)

    data: Dict[str, Any] = {
        "computed_at": _now_iso(),
        "cap": CAP,
        "min_sample": MIN_SAMPLE,
        "total_checkpoints": len(checkpoints),
        "cluster_weights": {k: _summarize_group(v) for k, v in cluster_groups.items()},
        "type_weights": {k: _summarize_group(v) for k, v in type_groups.items()},
    }

    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def load() -> Dict[str, Any]:
    """Load learned weights from disk. Returns empty struct if file missing."""
    if not WEIGHTS_FILE.exists():
        return {
            "cap": CAP,
            "min_sample": MIN_SAMPLE,
            "cluster_weights": {},
            "type_weights": {},
        }
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "cap": CAP,
            "min_sample": MIN_SAMPLE,
            "cluster_weights": {},
            "type_weights": {},
        }


def get_weight(
    cluster: Optional[str] = None,
    opportunity_type: Optional[str] = None,
    weights: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Return the multiplier to apply to an opportunity score.

    Prefers cluster-level weight when sample size is sufficient; falls back to
    opportunity-type weight; otherwise returns 1.0 (neutral). The neutral
    fallback means the loop is wired end-to-end even with zero history — it
    simply has no effect until data accumulates.
    """
    data = weights if weights is not None else load()
    min_n = data.get("min_sample", MIN_SAMPLE)

    if cluster:
        c = data.get("cluster_weights", {}).get(cluster.strip().lower())
        if c and c.get("n", 0) >= min_n:
            return float(c.get("multiplier", 1.0))

    if opportunity_type:
        t = data.get("type_weights", {}).get(opportunity_type.strip().lower())
        if t and t.get("n", 0) >= min_n:
            return float(t.get("multiplier", 1.0))

    return 1.0
