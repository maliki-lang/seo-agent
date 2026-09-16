"""GSC opportunity scoring and Phase-6 selection decisions for catalogue candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


@dataclass(frozen=True)
class ScoreBreakdown:
    gsc_opportunity_score: float
    business_relevance_score: float
    evidence_confidence_score: float
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
    # Phase 6 weights: GSC 0.40 + business 0.20 + evidence 0.10; GA4/Serper reserved for Phase 7.
    # Renormalize available components: 0.40/0.70, 0.20/0.70, 0.10/0.70.
    final = round((opp * 0.40 + relevance * 0.20 + confidence * 0.10) / 0.70, 4)
    reasons = list(opp_reasons)
    if relevance <= 0:
        reasons.append("low_business_relevance")
    else:
        reasons.append("business_relevant")
    return ScoreBreakdown(
        gsc_opportunity_score=opp,
        business_relevance_score=relevance,
        evidence_confidence_score=confidence,
        final_selection_score=final,
        reasons=tuple(reasons),
    )
