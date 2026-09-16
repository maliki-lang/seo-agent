"""Full catalogue selection scoring including GA4 and Serper components."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence, Tuple

from dataclasses import dataclass


def refresh_decision_reason(existing: Optional[str], score_reasons: Sequence[str]) -> str:
    """Keep decision prefix(es); replace the score-reason suffix with fresh reasons.

    Builder format is ``decision_code;reason1,reason2,...``. Selected shortlist
    prepends ``selected_phase6_shortlist;``. Enrichment/Serper must refresh the
    suffix so stale tokens like ``ga4_unavailable`` / ``serper_not_validated``
    do not survive after metrics are attached.
    """
    suffix = ",".join(score_reasons)
    existing = (existing or "").strip()
    if not existing:
        return suffix
    if ";" in existing:
        prefix = existing.rsplit(";", 1)[0]
        return f"{prefix};{suffix}" if prefix else suffix
    return f"{existing};{suffix}"


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


# --- Phase 12: protection/opportunity split + selection score v2 -------------------

# Provisional expected CTR by position band (not an approved causal CTR curve).
_PROVISIONAL_CTR_BY_POS = (
    (1, 0.28),
    (2, 0.15),
    (3, 0.11),
    (5, 0.07),
    (10, 0.04),
    (20, 0.02),
    (50, 0.01),
)


def provisional_expected_ctr(position: Optional[float]) -> Optional[float]:
    if position is None or position <= 0:
        return None
    prev = _PROVISIONAL_CTR_BY_POS[0][1]
    for cutoff, ctr in _PROVISIONAL_CTR_BY_POS:
        if position <= cutoff:
            return ctr
        prev = ctr
    return prev


def gsc_protection_score(
    *,
    clicks: int,
    impressions: int,
    weighted_position: Optional[float],
    min_impressions: int,
) -> Tuple[float, Tuple[str, ...]]:
    """Existing visibility worth protecting — not improvement opportunity."""
    if impressions < min_impressions and clicks < 1:
        return 0.0, ("insufficient_evidence",)
    pos = weighted_position if weighted_position is not None else 100.0
    score = 0.0
    reasons = []
    if clicks >= 3:
        score += 0.45
        reasons.append("stable_click_volume")
    elif clicks >= 1:
        score += 0.25
        reasons.append("some_existing_clicks")
    if pos <= 3 and impressions >= min_impressions:
        score += 0.4
        reasons.append("top3_visibility")
    elif pos <= 10 and impressions >= min_impressions:
        score += 0.25
        reasons.append("page1_visibility")
    if impressions >= min_impressions * 3:
        score += 0.15
        reasons.append("material_impressions")
    return round(min(1.0, score), 4), tuple(reasons) or ("low_protection_signal",)


def gsc_opportunity_score_v2(
    *,
    clicks: int,
    impressions: int,
    weighted_position: Optional[float],
    weighted_ctr: Optional[float],
    min_impressions: int,
    target_actionability: float = 0.0,
    multi_page_class: str = "normal_page_variation",
) -> Tuple[float, Tuple[str, ...]]:
    """Improvement opportunity — does not reward clicks>=1 as a fixed bonus."""
    if impressions < min_impressions and clicks < 1:
        return 0.0, ("insufficient_evidence",)
    pos = weighted_position if weighted_position is not None else 100.0
    ctr = weighted_ctr if weighted_ctr is not None else 0.0
    score = 0.0
    reasons = []

    if impressions >= min_impressions and 4.0 <= pos <= 10.0:
        expected = provisional_expected_ctr(pos) or 0.05
        if ctr < expected:
            score += 0.35
            reasons.append("provisional_ctr_gap_pos_4_10")
        else:
            score += 0.2
            reasons.append("mid_page1_movement_band")
    elif impressions >= min_impressions and 11.0 <= pos <= 20.0:
        score += 0.35
        reasons.append("plausible_page2_movement")
    elif impressions >= min_impressions and pos > 20:
        score += 0.2
        reasons.append("deeper_rank_with_demand")
    elif impressions >= min_impressions and pos <= 3.0:
        score += 0.1
        reasons.append("already_top_visibility_low_opportunity")

    if impressions >= min_impressions:
        score += 0.15 * min(1.0, impressions / max(min_impressions * 5, 1))
        reasons.append("meaningful_demand")

    score += 0.15 * max(0.0, min(1.0, target_actionability))
    reasons.append("target_actionability_component")

    if multi_page_class == "cannibalization_candidate":
        score += 0.15
        reasons.append("cannibalization_optimization")

    return round(min(1.0, score), 4), tuple(reasons) or ("low_opportunity_signal",)


def serp_component_scores(
    *,
    position: Optional[int],
    proposed_target_ranks: Optional[bool],
    validated: bool,
    target_actionability: float = 0.0,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Tuple[str, ...]]:
    """Return visibility, opportunity, alignment, confidence, feasibility (+ reasons)."""
    if not validated or position is None:
        return None, None, None, None, None, ("serper_not_validated",)

    reasons = []
    if position <= 0:
        visibility = 0.05
        opportunity = 0.55 * max(0.2, target_actionability)
        reasons.append("absent_visibility_gap")
    elif position <= 3:
        visibility = 0.95
        opportunity = 0.15
        reasons.append("top3_high_visibility_low_opportunity")
    elif position <= 10:
        visibility = 0.75
        opportunity = 0.55
        reasons.append("page1_improvement_band")
    elif position <= 20:
        visibility = 0.45
        opportunity = 0.7
        reasons.append("page2_improvement_band")
    else:
        visibility = 0.25
        opportunity = 0.5
        reasons.append("beyond_20")

    if proposed_target_ranks:
        alignment = 0.9
        reasons.append("proposed_target_ranks")
    else:
        alignment = 0.35
        reasons.append("proposed_target_absent")

    confidence = 0.85
    feasibility = round(
        min(1.0, 0.45 * opportunity + 0.35 * alignment + 0.20 * max(0.0, 1.0 - visibility)),
        4,
    )
    return (
        round(visibility, 4),
        round(min(1.0, opportunity), 4),
        round(alignment, 4),
        confidence,
        feasibility,
        tuple(reasons),
    )


@dataclass(frozen=True)
class ScoreBreakdownV2:
    intent_fit_score: float
    product_need_relevance_score: float
    target_actionability_score: float
    gsc_protection_score: float
    gsc_opportunity_score_v2: float
    serp_visibility_score: Optional[float]
    serp_opportunity_score: Optional[float]
    serp_target_alignment_score: Optional[float]
    serp_validation_confidence: Optional[float]
    serp_feasibility_score: Optional[float]
    incremental_coverage_score: float
    evidence_confidence_score: float
    duplicate_penalty: float
    selection_score_v2: Optional[float]
    available_evidence_weight: float
    missing_evidence_fields: Tuple[str, ...]
    score_confidence: float
    hard_excluded: bool
    exclusion_reasons: Tuple[str, ...]
    reasons: Tuple[str, ...]


def intent_fit_from_eligibility(eligibility_status: Optional[str], search_intent: Optional[str]) -> float:
    status = eligibility_status or ""
    if status == "eligible":
        base = 1.0
    elif status == "eligible_with_review":
        base = 0.7
    elif status == "pending_classification":
        base = 0.4
    else:
        return 0.0
    if search_intent in {"ambiguous", None, ""}:
        base *= 0.85
    return round(base, 4)


def product_need_relevance_from_candidate(
    *,
    business_relevance_score: Optional[float],
    business_relevance_status: Optional[str],
) -> float:
    if (business_relevance_status or "") in {"irrelevant", "location_only"}:
        return 0.0
    return round(max(0.0, min(1.0, float(business_relevance_score or 0.0))), 4)


def hard_exclusion_reasons(candidate: dict) -> Tuple[str, ...]:
    reasons = []
    eligibility = candidate.get("eligibility_status") or ""
    brand = candidate.get("brand_status") or ""
    family_role = candidate.get("family_role") or ""
    target_status = candidate.get("target_page_status") or ""
    if family_role == "variant":
        reasons.append("family_non_primary_variant")
    if brand == "branded":
        reasons.append("exact_branded_nonbrand_catalogue")
    if brand == "ambiguous_brand":
        reasons.append("ambiguous_brand_pending_review")
    if eligibility in {
        "ineligible_irrelevant",
        "ineligible_brand",
        "ineligible_duplicate_variant",
        "ineligible_unsupported_claim",
        "ineligible_no_actionable_target",
    }:
        reasons.append(f"eligibility_{eligibility}")
    if target_status in {"no_sensible_target"}:
        reasons.append("no_actionable_target")
    if int(candidate.get("claims_review_required") or 0) and eligibility != "eligible_with_review":
        # Still allow eligible_with_review through scoring; hard-block only when not flagged for review path.
        pass
    if int(candidate.get("claims_review_required") or 0) and eligibility == "eligible":
        reasons.append("unsupported_claims_without_review_flag")
    return tuple(reasons)


def configured_penalties(candidate: dict, penalties: dict) -> Tuple[float, Tuple[str, ...]]:
    total = 0.0
    reasons = []
    if (candidate.get("brand_status") or "") == "ambiguous_brand":
        total += float(penalties.get("ambiguous_brand", 0.0))
        reasons.append("penalty_ambiguous_brand")
    if (candidate.get("competitor_status") or "none") != "none":
        # Without an approved strategy field yet, apply configured competitor penalty.
        total += float(penalties.get("competitor_without_strategy", 0.0))
        reasons.append("penalty_competitor_without_strategy")
    if (candidate.get("target_page_status") or "") in {
        "homepage_unresolved",
        "manual_review",
        "no_sensible_target",
    }:
        total += float(penalties.get("unresolved_target_page", 0.0))
        reasons.append("penalty_unresolved_target_page")
    if (candidate.get("family_role") or "") == "variant":
        total += float(penalties.get("duplicate_non_primary", 0.0))
        reasons.append("penalty_duplicate_non_primary")
    return round(total, 4), tuple(reasons)


def score_candidate_v2(
    candidate: dict,
    *,
    score_weights: dict,
    penalties: dict,
    min_impressions: int,
    require_serper: bool = False,
    incremental_coverage_score: float = 0.5,
) -> ScoreBreakdownV2:
    exclusions = list(hard_exclusion_reasons(candidate))
    intent_fit = intent_fit_from_eligibility(
        candidate.get("eligibility_status"), candidate.get("search_intent")
    )
    product_need = product_need_relevance_from_candidate(
        business_relevance_score=candidate.get("business_relevance_score"),
        business_relevance_status=candidate.get("business_relevance_status"),
    )
    target_act = float(candidate.get("target_actionability_score") or 0.0)
    protection, prot_reasons = gsc_protection_score(
        clicks=int(candidate.get("gsc_clicks") or 0),
        impressions=int(candidate.get("gsc_impressions") or 0),
        weighted_position=candidate.get("gsc_weighted_position"),
        min_impressions=min_impressions,
    )
    opp_v2, opp_reasons = gsc_opportunity_score_v2(
        clicks=int(candidate.get("gsc_clicks") or 0),
        impressions=int(candidate.get("gsc_impressions") or 0),
        weighted_position=candidate.get("gsc_weighted_position"),
        weighted_ctr=candidate.get("gsc_weighted_ctr"),
        min_impressions=min_impressions,
        target_actionability=target_act,
        multi_page_class=candidate.get("multi_page_class") or "normal_page_variation",
    )
    serper_validated = candidate.get("serper_position") is not None and bool(
        candidate.get("serper_run_id") or candidate.get("serper_validation_score") is not None
    )
    # Prefer explicit validated flag when present via serper_collected_at.
    if candidate.get("serper_collected_at"):
        serper_validated = candidate.get("serper_position") is not None

    vis, serp_opp, align, serp_conf, feasibility, serp_reasons = serp_component_scores(
        position=candidate.get("serper_position"),
        proposed_target_ranks=bool(candidate.get("proposed_target_ranks"))
        if candidate.get("proposed_target_ranks") is not None
        else None,
        validated=serper_validated,
        target_actionability=target_act,
    )
    evidence = float(candidate.get("evidence_confidence_score") or 0.0)
    if evidence <= 0:
        evidence = evidence_confidence_score(
            impressions=int(candidate.get("gsc_impressions") or 0),
            source_row_count=int(candidate.get("source_row_count") or 0),
            source_date_count=int(candidate.get("source_date_count") or 0),
            min_impressions=min_impressions,
        )

    missing = []
    available = 0.0
    weight_map = {
        "intent_fit": ("intent_fit_score", intent_fit, True),
        "product_need_relevance": ("product_need_relevance_score", product_need, True),
        "target_actionability": ("target_actionability_score", target_act, True),
        "gsc_opportunity": ("gsc_opportunity_score_v2", opp_v2, True),
        "serp_feasibility": ("serp_feasibility_score", feasibility, False),
        "incremental_coverage": ("incremental_coverage_score", incremental_coverage_score, True),
        "evidence_confidence": ("evidence_confidence_score", evidence, True),
    }
    components = {}
    for key, (field, value, always) in weight_map.items():
        w = float(score_weights.get(key, 0.0))
        if value is None:
            missing.append(field)
            continue
        if not always and value is None:
            missing.append(field)
            continue
        components[key] = (w, float(value))
        available += w

    if require_serper and feasibility is None:
        exclusions.append("missing_required_serper_validation")

    penalty_total, penalty_reasons = configured_penalties(candidate, penalties)
    hard_excluded = bool(exclusions)

    reasons = list(prot_reasons) + list(opp_reasons) + list(serp_reasons) + list(penalty_reasons)
    if hard_excluded:
        return ScoreBreakdownV2(
            intent_fit_score=intent_fit,
            product_need_relevance_score=product_need,
            target_actionability_score=target_act,
            gsc_protection_score=protection,
            gsc_opportunity_score_v2=opp_v2,
            serp_visibility_score=vis,
            serp_opportunity_score=serp_opp,
            serp_target_alignment_score=align,
            serp_validation_confidence=serp_conf,
            serp_feasibility_score=feasibility,
            incremental_coverage_score=incremental_coverage_score,
            evidence_confidence_score=evidence,
            duplicate_penalty=penalty_total,
            selection_score_v2=None,
            available_evidence_weight=round(available, 4),
            missing_evidence_fields=tuple(missing),
            score_confidence=0.0,
            hard_excluded=True,
            exclusion_reasons=tuple(exclusions),
            reasons=tuple(reasons + list(exclusions)),
        )

    raw = 0.0
    for key, (w, value) in components.items():
        raw += w * value
    # Do not silently renormalize missing Serper to look fully validated.
    # Keep original weighted sum over full denominator (=1.0) and expose confidence.
    full_denom = sum(float(score_weights.get(k, 0.0)) for k in score_weights) or 1.0
    score = raw - penalty_total
    score = max(0.0, min(1.0, score))
    confidence = round(available / full_denom, 4) if full_denom else 0.0
    if missing:
        reasons.append("missing_evidence:" + ",".join(missing))
        # Confidence penalty already reflected in score_confidence; lightly discount score.
        score = round(score * (0.85 + 0.15 * confidence), 4)

    return ScoreBreakdownV2(
        intent_fit_score=intent_fit,
        product_need_relevance_score=product_need,
        target_actionability_score=target_act,
        gsc_protection_score=protection,
        gsc_opportunity_score_v2=opp_v2,
        serp_visibility_score=vis,
        serp_opportunity_score=serp_opp,
        serp_target_alignment_score=align,
        serp_validation_confidence=serp_conf,
        serp_feasibility_score=feasibility,
        incremental_coverage_score=incremental_coverage_score,
        evidence_confidence_score=evidence,
        duplicate_penalty=penalty_total,
        selection_score_v2=round(score, 4),
        available_evidence_weight=round(available, 4),
        missing_evidence_fields=tuple(missing),
        score_confidence=confidence,
        hard_excluded=False,
        exclusion_reasons=(),
        reasons=tuple(reasons),
    )


def score_v2_update_fields(breakdown: ScoreBreakdownV2) -> dict:
    return {
        "intent_fit_score": breakdown.intent_fit_score,
        "product_need_relevance_score": breakdown.product_need_relevance_score,
        "target_actionability_score": breakdown.target_actionability_score,
        "gsc_protection_score": breakdown.gsc_protection_score,
        "gsc_opportunity_score_v2": breakdown.gsc_opportunity_score_v2,
        "serp_visibility_score": breakdown.serp_visibility_score,
        "serp_opportunity_score": breakdown.serp_opportunity_score,
        "serp_target_alignment_score": breakdown.serp_target_alignment_score,
        "serp_validation_confidence": breakdown.serp_validation_confidence,
        "serp_feasibility_score": breakdown.serp_feasibility_score,
        "incremental_coverage_score": breakdown.incremental_coverage_score,
        "evidence_confidence_score": breakdown.evidence_confidence_score,
        "duplicate_penalty": breakdown.duplicate_penalty,
        "selection_score_v2": breakdown.selection_score_v2,
        "available_evidence_weight": breakdown.available_evidence_weight,
        "missing_evidence_fields_json": list(breakdown.missing_evidence_fields),
        "score_confidence": breakdown.score_confidence,
        "selection_reasons_json": {
            "reasons": list(breakdown.reasons),
            "hard_excluded": breakdown.hard_excluded,
            "exclusion_reasons": list(breakdown.exclusion_reasons),
        },
    }
