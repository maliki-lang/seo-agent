"""Outcome classification tests."""

from data_sources.tracking.config import EconomicsConfig
from data_sources.tracking.experiments.outcomes import classify_outcome


def test_day14_cannot_finalize_winner():
    out = classify_outcome(
        checkpoint_days=14,
        adjusted_incremental_clicks=20,
        baseline_clicks=100,
        observed_clicks=120,
        confidence_label="high",
        quality_status="pass",
    )
    assert out["outcome"] == "provisional"
    assert out["finalizable"] is False


def test_winner_at_28_days():
    out = classify_outcome(
        checkpoint_days=28,
        adjusted_incremental_clicks=20,
        baseline_clicks=100,
        observed_clicks=130,
        confidence_label="high",
        quality_status="pass",
        isolation_quality="high",
        actual_cost=50,
        economics=EconomicsConfig(winner_min_lift_pct=0.15),
    )
    assert out["outcome"] == "winner"
    assert out["actual_cost_per_incremental_click"] == 2.5


def test_sparse_baseline_inconclusive():
    out = classify_outcome(
        checkpoint_days=28,
        adjusted_incremental_clicks=5,
        baseline_clicks=0,
        observed_clicks=5,
        confidence_label="high",
        quality_status="pass",
    )
    assert out["outcome"] == "inconclusive"


def test_negative_preserved_as_likely_loss():
    out = classify_outcome(
        checkpoint_days=28,
        adjusted_incremental_clicks=-8,
        baseline_clicks=100,
        observed_clicks=80,
        confidence_label="medium",
        quality_status="pass",
    )
    assert out["outcome"] == "likely_loss"
    assert out["lift_pct"] < 0


def test_isolation_low_blocks_winner():
    out = classify_outcome(
        checkpoint_days=28,
        adjusted_incremental_clicks=30,
        baseline_clicks=100,
        observed_clicks=140,
        confidence_label="high",
        quality_status="pass",
        isolation_quality="low",
    )
    assert out["outcome"] == "inconclusive"


def test_tracking_failure_on_quality():
    out = classify_outcome(
        checkpoint_days=28,
        adjusted_incremental_clicks=10,
        baseline_clicks=100,
        observed_clicks=110,
        confidence_label="high",
        quality_status="fail",
    )
    assert out["outcome"] == "tracking_failure"
