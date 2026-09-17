"""Phase 15 portfolio hardening tests for Phase 16 activation."""

from __future__ import annotations

from data_sources.tracking.opportunities.portfolio import select_top_ten
from data_sources.tracking.opportunities.targets import (
    canonicalize_target_url,
    validate_target_for_portfolio,
)


def _row(**overrides):
    base = {
        "category": "seo",
        "source_type": "ctr_underperformance",
        "action_type": "title_meta_rewrite",
        "problem": "CTR gap",
        "proposed_action": "Rewrite",
        "target_page": "https://sunnystep.com/collections/walking-shoes",
        "target_page_status": "observed_page_needs_optimization",
        "expected_incremental_clicks": 12,
        "confidence_value": 0.6,
        "effort_value": 2,
        "cluster_id": "c1",
    }
    base.update(overrides)
    return base


def test_portfolio_may_return_fewer_than_ten():
    rows = [
        _row(
            problem=f"p{i}",
            expected_incremental_clicks=10 + i,
            cluster_id=f"c{i}",
            target_page=f"https://sunnystep.com/pages/p{i}",
        )
        for i in range(3)
    ]
    out = select_top_ten(rows, limit=10)
    assert out["counts"]["selected"] == 3
    assert out["counts"]["selected"] < 10


def test_zero_impact_cannibalization_does_not_fill():
    traffic = [_row(problem="strong", expected_incremental_clicks=20, cluster_id="a")]
    weak = [
        _row(
            problem=f"cannibal-{i}",
            source_type="cannibalization",
            action_type="content_consolidation",
            expected_incremental_clicks=0,
            cluster_id=f"x{i}",
            target_page=f"https://sunnystep.com/pages/p{i}",
        )
        for i in range(9)
    ]
    out = select_top_ten(traffic + weak, limit=10)
    assert out["counts"]["selected"] == 1
    assert all(float(r["expected_incremental_clicks"]) >= 1 for r in out["selected"])


def test_http_and_homepage_rejected():
    ok, reason = validate_target_for_portfolio(
        _row(target_page="http://sunnystep.com/collections/walking-shoes")
    )
    assert not ok and reason == "http_target_blocked"
    ok, reason = validate_target_for_portfolio(_row(target_page="https://sunnystep.com/"))
    assert not ok and reason == "homepage_not_explicitly_approved"


def test_canonicalize_strips_variant_query():
    assert canonicalize_target_url(
        "https://sunnystep.com/products/shoe?variant=123"
    ) == "https://sunnystep.com/products/shoe"


def test_source_concentration_warning():
    rows = [
        _row(
            problem=f"p{i}",
            source_type="cannibalization",
            expected_incremental_clicks=10 + i,
            cluster_id=f"c{i}",
            target_page=f"https://sunnystep.com/pages/{i}",
        )
        for i in range(5)
    ]
    out = select_top_ten(rows, limit=10)
    assert out["concentration_warning"] is not None
    assert out["concentration_warning"]["share"] > 0.4
