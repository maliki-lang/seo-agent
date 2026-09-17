"""Counterfactual method tests."""

from data_sources.tracking.experiments.counterfactual import estimate_expected_clicks


def test_matched_control_median():
    out = estimate_expected_clicks(
        target_baseline_clicks=100,
        target_observed_clicks=140,
        control_baseline_clicks=[50, 80],
        control_post_clicks=[55, 88],  # factors 1.1, 1.1
    )
    assert out["method"] == "matched_controls"
    assert out["control_trend_factor"] == 1.1
    assert out["expected_clicks_without_change"] == 110.0
    assert out["adjusted_incremental_clicks"] == 30.0


def test_sitewide_excludes_zero_baseline_path():
    out = estimate_expected_clicks(
        target_baseline_clicks=100,
        target_observed_clicks=120,
        sitewide_baseline_clicks=1000,
        sitewide_post_clicks=1100,
    )
    assert out["method"] == "sitewide_adjusted"
    assert out["sitewide_trend_factor"] == 1.1
    assert out["adjusted_incremental_clicks"] == 10.0


def test_before_after_when_no_controls():
    out = estimate_expected_clicks(
        target_baseline_clicks=100,
        target_observed_clicks=90,
    )
    assert out["method"] == "before_after"
    assert out["confidence_cap"] == "low"
    assert out["adjusted_incremental_clicks"] == -10.0


def test_zero_baseline_does_not_divide():
    out = estimate_expected_clicks(
        target_baseline_clicks=0,
        target_observed_clicks=5,
        sitewide_baseline_clicks=0,
        sitewide_post_clicks=10,
    )
    assert out["method"] == "before_after"
    assert out["expected_clicks_without_change"] == 0.0
