"""Unit tests for Phase 7 page join keys and enrichment scoring helpers."""

from decimal import Decimal

from data_sources.tracking.catalogue.scoring import (
    ga4_value_score,
    refresh_decision_reason,
    serper_validation_score,
)
from data_sources.tracking.transforms.normalize import canonical_page_key, infer_page_type


def test_refresh_decision_reason_keeps_prefix_replaces_score_suffix():
    fresh = ("high_impr_pos_4_10_weak_ctr", "business_relevant", "page_type_collection")
    assert refresh_decision_reason(
        "meets_phase6_evidence_threshold_awaiting_review;ga4_unavailable,serper_not_validated",
        fresh,
    ) == (
        "meets_phase6_evidence_threshold_awaiting_review;"
        "high_impr_pos_4_10_weak_ctr,business_relevant,page_type_collection"
    )
    assert refresh_decision_reason(
        "selected_phase6_shortlist;meets_phase6_evidence_threshold_awaiting_review;"
        "ga4_unavailable,serper_not_validated",
        fresh,
    ).startswith("selected_phase6_shortlist;meets_phase6_evidence_threshold_awaiting_review;")
    assert "ga4_unavailable" not in refresh_decision_reason(
        "selected_phase6_shortlist;meets_phase6_evidence_threshold_awaiting_review;"
        "ga4_unavailable",
        fresh,
    )
    assert refresh_decision_reason(None, fresh) == ",".join(fresh)
    assert refresh_decision_reason("imported_from_review", fresh) == (
        "imported_from_review;" + ",".join(fresh)
    )


def test_canonical_page_key_joins_url_and_path():
    assert canonical_page_key("https://www.sunnystep.com/collections/flats/?utm=1") == (
        "sunnystep.com/collections/flats"
    )
    assert canonical_page_key("/collections/flats?ref=x") == "sunnystep.com/collections/flats"
    assert canonical_page_key("https://gosunnystep.myshopify.com/products/Aria") == (
        "sunnystep.com/products/Aria"
    )
    assert canonical_page_key("(not set)") == ""
    assert canonical_page_key("") == ""


def test_is_homepage_page_key():
    from data_sources.tracking.transforms.normalize import is_homepage_page_key

    assert is_homepage_page_key("https://sunnystep.com/") is True
    assert is_homepage_page_key("http://sunnystep.com/") is True
    assert is_homepage_page_key("https://www.sunnystep.com") is True
    assert is_homepage_page_key("/") is True
    assert is_homepage_page_key("sunnystep.com/") is True  # already-canonical join key
    assert is_homepage_page_key("https://sunnystep.com/collections/mules") is False
    assert is_homepage_page_key("") is False


def test_infer_page_type():
    assert infer_page_type("https://sunnystep.com/blogs/news/foo") == "article"
    assert infer_page_type("/collections/walking-shoes") == "collection"
    assert infer_page_type("/products/aria") == "product"
    assert infer_page_type("https://sunnystep.com/") == "other"


def test_ga4_value_score_none_when_unavailable():
    score, reasons = ga4_value_score(
        sessions=None,
        engaged_sessions=None,
        purchases=None,
        revenue=None,
        page_type="article",
    )
    assert score is None
    assert "ga4_unavailable" in reasons


def test_ga4_value_score_page_type_weights():
    commercial, _ = ga4_value_score(
        sessions=10,
        engaged_sessions=5,
        purchases=2,
        revenue=Decimal("50"),
        page_type="collection",
    )
    article, _ = ga4_value_score(
        sessions=10,
        engaged_sessions=5,
        purchases=2,
        revenue=Decimal("50"),
        page_type="article",
    )
    assert commercial is not None and article is not None
    assert commercial != article


def test_serper_validation_score():
    absent, reasons = serper_validation_score(position=0, proposed_target_ranks=False, validated=True)
    assert absent is not None and absent < 0.5
    assert "sunnystep_absent_in_inspected_range" in reasons
    top, top_reasons = serper_validation_score(position=2, proposed_target_ranks=True, validated=True)
    assert top is not None and top > 0.9
    assert "proposed_target_page_ranks" in top_reasons
    missing, _ = serper_validation_score(position=None, proposed_target_ranks=None, validated=False)
    assert missing is None
