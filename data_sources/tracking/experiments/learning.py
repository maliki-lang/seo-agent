"""Update advisory action-type priors from completed experiment outcomes."""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from ..config import TrackingConfig
from ..enums import ExperimentOutcome, ExperimentStatus
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .costs import cost_per_incremental_click

WIN_OUTCOMES = {
    ExperimentOutcome.WINNER.value,
    ExperimentOutcome.LIKELY_WINNER.value,
}
LOSS_OUTCOMES = {ExperimentOutcome.LIKELY_LOSS.value}
INCONCLUSIVE_OUTCOMES = {
    ExperimentOutcome.INCONCLUSIVE.value,
    ExperimentOutcome.TRACKING_FAILURE.value,
}


def update_action_type_prior(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    action_type: str,
) -> Dict[str, Any]:
    """Recompute prior from closed/finalized experiments of this action type."""
    store.migrate()
    rows = store.fetchall(
        """
        SELECT e.experiment_id, e.action_type, e.status, e.actual_cost,
               m.adjusted_incremental_clicks, m.outcome, m.checkpoint_days
        FROM seo_experiments e
        JOIN experiment_measurements m ON m.experiment_id = e.experiment_id
        WHERE e.action_type = ?
          AND m.checkpoint_days >= 28
          AND e.status IN (?, ?, ?, ?, ?, ?)
        ORDER BY e.experiment_id, m.checkpoint_days DESC
        """,
        (
            action_type,
            ExperimentStatus.WINNER.value,
            ExperimentStatus.LIKELY_WINNER.value,
            ExperimentStatus.INCONCLUSIVE.value,
            ExperimentStatus.LIKELY_LOSS.value,
            ExperimentStatus.TRACKING_FAILURE.value,
            ExperimentStatus.CLOSED.value,
        ),
    )
    # Keep latest qualifying checkpoint per experiment.
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        eid = row["experiment_id"]
        if eid not in latest:
            latest[eid] = dict(row)

    completed = list(latest.values())
    wins = sum(1 for r in completed if r["outcome"] in WIN_OUTCOMES)
    losses = sum(1 for r in completed if r["outcome"] in LOSS_OUTCOMES)
    inconclusive = sum(1 for r in completed if r["outcome"] in INCONCLUSIVE_OUTCOMES)
    increments = [
        float(r["adjusted_incremental_clicks"])
        for r in completed
        if r.get("adjusted_incremental_clicks") is not None
    ]
    cost_rates: List[float] = []
    for r in completed:
        rate = cost_per_incremental_click(
            cost=r.get("actual_cost"),
            incremental_clicks=r.get("adjusted_incremental_clicks"),
        )
        if rate is not None:
            cost_rates.append(rate)

    n = len(completed)
    min_n = config.economics.minimum_experiments_for_learning
    empirical = None
    if n >= min_n and n > 0:
        empirical = round(wins / n, 6)

    now = utc_now_iso()
    payload = {
        "action_type": action_type,
        "completed_experiments": n,
        "wins": wins,
        "losses": losses,
        "inconclusive": inconclusive,
        "median_incremental_clicks": float(median(increments)) if increments else None,
        "median_cost_per_incremental_click": float(median(cost_rates)) if cost_rates else None,
        "empirical_confidence": empirical,
        "updated_at": now,
        "learning_active": n >= min_n,
    }
    store.upsert_action_type_prior(payload)
    return payload
