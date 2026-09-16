"""Full catalogue selection scoring including GA4 and Serper components."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence, Tuple

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreBreakdown:
    gsc_opportunity_score: float
    business_relevance_score: float
    evidence_confidence_score: float
    ga4_value_score: Optional[float]
    serper_validation_score: Optional[float]
    final_selection_score: float
    reasons: Tuple[str, ...]


def business_relevance_score(normalized_keyword: str, terms: Sequence[str]) -> float:
    if not normalized_keyword:
        return 0.0
    tokens = set(normalized_keyword.split())
    hits = sum(1 for term in terms if term in tokens or term in normalized_keyword)
    if hits <= 0:
        return 0.0
    return min(1.0, 0.35 + 0.15 * hits)


def evidence_confidence_score(
    *,
    impressions: int,
    source_row_count: int,
    source_date_count: int,
    min_impressions: int,
) -> float:
    if impressions <= 0 or source_row_count <= 0:
        return 0.0
    coverage = min(1.0, source_date_count / 14.0)
    volume = min(1.0, impressions / max(min_impressions * 5, 1))
    rows = min(1.0, source_row_count / 5.0)
    return round(0.4 * coverage + 0.4 * volume + 0.2 * rows, 4)


def gsc_opportunity_score(
    *,
    clicks: int,
    impressions: int,
    weighted_position: Optional[float],
    weighted_ctr: Optional[float],
    multi_page: bool,
    min_impressions: int,
) -> Tuple[float, Tuple[str, ...]]:
    reasons = []
    if impressions < min_impressions and clicks < 1:
        return 0.0, ("insufficient_evidence",)

    score = 0.0
    pos = weighted_position if weighted_position is not None else 100.0
    ctr = weighted_ctr if weighted_ctr is not None else 0.0

    if impressions >= min_impressions and 4.0 <= pos <= 10.0 and ctr < 0.05:
        score += 0.45
        reasons.append("high_impr_pos_4_10_weak_ctr")
    elif impressions >= min_impressions and 11.0 <= pos <= 20.0:
        score += 0.35
        reasons.append("meaningful_impr_pos_11_20")
    elif impressions >= min_impressions and pos <= 3.0:
        score += 0.25
        reasons.append("existing_top_visibility")
    elif impressions >= min_impressions:
        score += 0.2
        reasons.append("impressions_outside_priority_bands")

    if clicks >= 1:
        score += 0.25
        reasons.append("existing_clicks_worth_protecting")

    if multi_page:
        score += 0.15
        reasons.append("multi_page_competition")

    if impressions >= min_impressions * 3:
        score += 0.1
        reasons.append("strong_impression_volume")

    return round(min(1.0, score), 4), tuple(reasons) or ("insufficient_evidence",)


def ga4_value_score(
    *,
    sessions: Optional[int],
    engaged_sessions: Optional[int],
    purchases: Optional[int],
    revenue: Optional[Decimal],
    page_type: str,
) -> Tuple[Optional[float], Tuple[str, ...]]:
    """Return None when GA4 metrics are unavailable (unmatched), never invent zeros."""
    if sessions is None:
        return None, ("ga4_unavailable",)
    sessions_i = int(sessions)
    engaged_i = int(engaged_sessions or 0)
    purchases_i = int(purchases or 0)
    revenue_f = float(revenue or 0)
    engagement = (engaged_i / sessions_i) if sessions_i > 0 else 0.0
    conversion = (purchases_i / sessions_i) if sessions_i > 0 else 0.0
    reasons = [f"page_type_{page_type or 'other'}"]

    session_component = min(1.0, sessions_i / 50.0)
    engagement_component = min(1.0, engagement)
    purchase_component = min(1.0, purchases_i / 3.0)
    revenue_component = min(1.0, revenue_f / 100.0)

    if page_type in {"product", "collection"}:
        score = (
            0.20 * session_component
            + 0.15 * engagement_component
            + 0.35 * purchase_component
            + 0.30 * revenue_component
        )
        reasons.append("commercial_page_weights")
    elif page_type == "article":
        score = (
            0.35 * session_component
            + 0.45 * engagement_component
            + 0.10 * purchase_component
            + 0.10 * revenue_component
        )
        reasons.append("educational_page_weights")
    else:
        score = (
            0.30 * session_component
            + 0.30 * engagement_component
            + 0.20 * purchase_component
            + 0.20 * revenue_component
        )
        reasons.append("other_page_weights")

    if sessions_i == 0 and purchases_i == 0 and revenue_f == 0:
        reasons.append("ga4_matched_zero_activity")
    return round(min(1.0, score), 4), tuple(reasons)


def serper_validation_score(
    *,
    position: Optional[int],
    proposed_target_ranks: Optional[bool],
    validated: bool,
) -> Tuple[Optional[float], Tuple[str, ...]]:
    if not validated or position is None:
        return None, ("serper_not_validated",)
    reasons = []
    if position <= 0:
        score = 0.15
        reasons.append("sunnystep_absent_in_inspected_range")
    elif position <= 3:
        score = 0.95
        reasons.append("sunnystep_top_3")
    elif position <= 10:
        score = 0.75
        reasons.append("sunnystep_page_1")
    elif position <= 20:
        score = 0.45
        reasons.append("sunnystep_page_2")
    else:
        score = 0.25
        reasons.append("sunnystep_beyond_20")
    if proposed_target_ranks:
        score = min(1.0, score + 0.1)
        reasons.append("proposed_target_page_ranks")
    else:
        reasons.append("proposed_target_page_absent")
    return round(score, 4), tuple(reasons)


def score_candidate(
    *,
    clicks: int,
    impressions: int,
    weighted_position: Optional[float],
    weighted_ctr: Optional[float],
    multi_page: bool,
    normalized_keyword: str,
    source_row_count: int,
    source_date_count: int,
    relevance_terms: Sequence[str],
    min_impressions: int,
    ga4_sessions: Optional[int] = None,
    ga4_engaged_sessions: Optional[int] = None,
    ga4_purchases: Optional[int] = None,
    ga4_revenue: Optional[Decimal] = None,
    page_type: str = "other",
    serper_position: Optional[int] = None,
    proposed_target_ranks: Optional[bool] = None,
    serper_validated: bool = False,
) -> ScoreBreakdown:
    opp, opp_reasons = gsc_opportunity_score(
        clicks=clicks,
        impressions=impressions,
        weighted_position=weighted_position,
        weighted_ctr=weighted_ctr,
        multi_page=multi_page,
        min_impressions=min_impressions,
    )
    relevance = business_relevance_score(normalized_keyword, relevance_terms)
    confidence = evidence_confidence_score(
        impressions=impressions,
        source_row_count=source_row_count,
        source_date_count=source_date_count,
        min_impressions=min_impressions,
    )
    ga4_score, ga4_reasons = ga4_value_score(
        sessions=ga4_sessions,
        engaged_sessions=ga4_engaged_sessions,
        purchases=ga4_purchases,
        revenue=ga4_revenue,
        page_type=page_type,
    )
    serper_score, serper_reasons = serper_validation_score(
        position=serper_position,
        proposed_target_ranks=proposed_target_ranks,
        validated=serper_validated,
    )

    # Spec default weights. Missing GA4 is omitted and remaining weights renormalized.
    # Serper validation score is stored for review; it is not search-volume evidence and is
    # not part of the default final formula unless validated (then a small visibility weight).
    if ga4_score is None and serper_score is None:
        final = round((opp * 0.40 + relevance * 0.20 + confidence * 0.10) / 0.70, 4)
    elif ga4_score is None and serper_score is not None:
        final = round(
            (opp * 0.40 + relevance * 0.20 + confidence * 0.10 + serper_score * 0.10) / 0.80,
            4,
        )
    elif ga4_score is not None and serper_score is None:
        final = round(opp * 0.40 + ga4_score * 0.30 + relevance * 0.20 + confidence * 0.10, 4)
    else:
        # Keep GA4 commercial weight dominant; fold Serper as visibility confirmation.
        final = round(
            (opp * 0.40 + ga4_score * 0.30 + relevance * 0.20 + confidence * 0.10 + serper_score * 0.10)
            / 1.10,
            4,
        )

    reasons = list(opp_reasons)
    if relevance <= 0:
        reasons.append("low_business_relevance")
    else:
        reasons.append("business_relevant")
    reasons.extend(ga4_reasons)
    reasons.extend(serper_reasons)
    return ScoreBreakdown(
        gsc_opportunity_score=opp,
        business_relevance_score=relevance,
        evidence_confidence_score=confidence,
        ga4_value_score=ga4_score,
        serper_validation_score=serper_score,
        final_selection_score=final,
        reasons=tuple(reasons),
    )
