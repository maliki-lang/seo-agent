"""Target-page actionability and shared GA4 confidence discount (Phase 11)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import (
    EligibilityStatus,
    MultiPageClass,
    ProposedAction,
    SearchIntent,
    TargetPageStatus,
)
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import (
    canonical_page_key,
    infer_page_type,
    is_homepage_page_key,
    utc_now_iso,
)
from ..transforms.brand_label import normalize_for_brand
from .policy import CatalogueSelectionPolicy, policy_from_config

# Spec 11.4 defaults.
GA4_MULT_ONE_FAMILY = 1.00
GA4_MULT_2_TO_5 = 0.60
GA4_MULT_GT_5 = 0.25
GA4_MULT_HOMEPAGE = 0.00


def _contains_phrase(normalized: str, phrase: str) -> bool:
    hay = f" {normalize_for_brand(normalized)} "
    needle = f" {normalize_for_brand(phrase)} "
    return bool(phrase) and needle in hay


def _has_any_phrase(normalized: str, phrases) -> bool:
    return any(_contains_phrase(normalized, phrase) for phrase in phrases)


def _is_specific_intent(candidate: Dict[str, Any], policy: CatalogueSelectionPolicy) -> bool:
    intent = candidate.get("search_intent") or ""
    if intent in {
        SearchIntent.TRANSACTIONAL_CATEGORY.value,
        SearchIntent.PROBLEM_SOLUTION.value,
        SearchIntent.COMMERCIAL_INVESTIGATION.value,
        SearchIntent.CAMPAIGN_EVENT.value,
    }:
        return True
    keyword = candidate.get("normalized_keyword") or ""
    return bool(
        _has_any_phrase(keyword, policy.product_terms)
        or _has_any_phrase(keyword, policy.need_terms)
    )


def classify_multi_page(candidate: Dict[str, Any]) -> str:
    if not int(candidate.get("multi_page_competition") or 0):
        return MultiPageClass.NORMAL_PAGE_VARIATION.value
    page_type = candidate.get("page_type") or infer_page_type(
        candidate.get("primary_observed_page") or ""
    )
    intent = candidate.get("search_intent") or ""
    if page_type in {"product", "collection"} and intent in {
        SearchIntent.TRANSACTIONAL_CATEGORY.value,
        SearchIntent.COMMERCIAL_INVESTIGATION.value,
        SearchIntent.PROBLEM_SOLUTION.value,
    }:
        return MultiPageClass.CANNIBALIZATION_CANDIDATE.value
    if intent == SearchIntent.INFORMATIONAL.value:
        return MultiPageClass.INTENT_SPLIT.value
    return MultiPageClass.UNRESOLVED.value


def evaluate_target_page(
    candidate: Dict[str, Any],
    *,
    policy: CatalogueSelectionPolicy,
) -> Tuple[str, float, str, str]:
    """Return (status, confidence, proposed_action, multi_page_class)."""
    observed = (candidate.get("primary_observed_page") or "").strip()
    proposed = (candidate.get("reviewed_target_page") or candidate.get("proposed_target_page") or "").strip()
    page = proposed or observed
    multi_class = classify_multi_page(candidate)

    if not page:
        return (
            TargetPageStatus.NO_SENSIBLE_TARGET.value,
            0.0,
            ProposedAction.NO_ACTION.value,
            multi_class,
        )

    if is_homepage_page_key(page) or is_homepage_page_key(observed):
        if _is_specific_intent(candidate, policy):
            return (
                TargetPageStatus.HOMEPAGE_UNRESOLVED.value,
                0.2,
                ProposedAction.CREATE_NEW_PAGE.value,
                multi_class,
            )
        # Generic store/brand/local intent may keep homepage as monitor/protect.
        return (
            TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
            0.45,
            ProposedAction.MONITOR_ONLY.value,
            multi_class,
        )

    if multi_class == MultiPageClass.CANNIBALIZATION_CANDIDATE.value:
        return (
            TargetPageStatus.MULTIPLE_PAGES_COMPETING.value,
            0.55,
            ProposedAction.CONSOLIDATE_COMPETING_PAGES.value,
            multi_class,
        )

    page_type = candidate.get("page_type") or infer_page_type(page)
    clicks = int(candidate.get("gsc_clicks") or 0)
    position = candidate.get("gsc_weighted_position")
    pos = float(position) if position is not None else 100.0

    if page_type in {"product", "collection", "article"}:
        if clicks >= 1 and pos <= 10:
            return (
                TargetPageStatus.OBSERVED_PAGE_SUITABLE.value,
                0.9,
                ProposedAction.PROTECT_EXISTING.value,
                multi_class,
            )
        if page_type in {"product", "collection"}:
            return (
                TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                0.7,
                ProposedAction.OPTIMIZE_EXISTING.value,
                multi_class,
            )
        return (
            TargetPageStatus.OBSERVED_PAGE_SUITABLE.value,
            0.65,
            ProposedAction.OPTIMIZE_EXISTING.value,
            multi_class,
        )

    return (
        TargetPageStatus.MANUAL_REVIEW.value,
        0.4,
        ProposedAction.MONITOR_ONLY.value,
        multi_class,
    )


def target_actionability_score(status: str, confidence: float) -> float:
    weights = {
        TargetPageStatus.OBSERVED_PAGE_SUITABLE.value: 1.0,
        TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value: 0.7,
        TargetPageStatus.MULTIPLE_PAGES_COMPETING.value: 0.55,
        TargetPageStatus.APPROVED_NEW_PAGE.value: 0.6,
        TargetPageStatus.HOMEPAGE_UNRESOLVED.value: 0.15,
        TargetPageStatus.NO_SENSIBLE_TARGET.value: 0.0,
        TargetPageStatus.MANUAL_REVIEW.value: 0.35,
    }
    return round(weights.get(status, 0.0) * float(confidence), 4)


def ga4_confidence_multiplier_for_counts(
    *,
    family_count_on_page: int,
    is_homepage: bool,
    ga4_match_status: Optional[str],
) -> Optional[float]:
    if ga4_match_status in {None, "unmatched", "unavailable"}:
        return None
    if is_homepage or ga4_match_status == "suppressed_homepage":
        return GA4_MULT_HOMEPAGE
    if family_count_on_page <= 1:
        return GA4_MULT_ONE_FAMILY
    if family_count_on_page <= 5:
        return GA4_MULT_2_TO_5
    return GA4_MULT_GT_5


def evaluate_targets_for_build(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    policy: Optional[CatalogueSelectionPolicy] = None,
    apply_ga4_discount: bool = True,
) -> Dict[str, Any]:
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")
    selection_policy = policy or policy_from_config(config)
    now = utc_now_iso()
    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
    ]
    status_counts: Counter = Counter()
    action_counts: Counter = Counter()
    homepage_unresolved = 0

    for cand in candidates:
        status, confidence, action, multi_class = evaluate_target_page(
            cand, policy=selection_policy
        )
        score = target_actionability_score(status, confidence)
        fields: Dict[str, Any] = {
            "target_page_status": status,
            "target_page_confidence": confidence,
            "target_actionability_score": score,
            "proposed_action": action,
            "multi_page_class": multi_class,
            "updated_at": now,
        }
        # Homepage unresolved blocks automatic final selection unless reviewed.
        if status == TargetPageStatus.HOMEPAGE_UNRESOLVED.value:
            homepage_unresolved += 1
            eligibility = cand.get("eligibility_status")
            if eligibility == EligibilityStatus.ELIGIBLE.value:
                fields["eligibility_status"] = EligibilityStatus.ELIGIBLE_WITH_REVIEW.value
                reasons = []
                raw = cand.get("eligibility_reasons_json")
                if raw:
                    try:
                        reasons = json.loads(raw) if isinstance(raw, str) else list(raw)
                    except json.JSONDecodeError:
                        reasons = []
                reasons = list(reasons) + ["homepage_unresolved_requires_review"]
                fields["eligibility_reasons_json"] = reasons
        store.update_keyword_candidate(cand["candidate_id"], fields)
        status_counts[status] += 1
        action_counts[action] += 1

    ga4_report: Dict[str, Any] = {}
    if apply_ga4_discount:
        ga4_report = apply_ga4_shared_discount(store, build_id=build_id)

    report = {
        "build_id": build_id,
        "candidate_count": len(candidates),
        "status_counts": dict(status_counts),
        "action_counts": dict(action_counts),
        "homepage_unresolved": homepage_unresolved,
        "ga4_shared_discount": ga4_report,
        "evaluated_at": now,
    }
    return report


def apply_ga4_shared_discount(store: TrackingStore, *, build_id: str) -> Dict[str, Any]:
    """Assign ga4_confidence_multiplier from family→page fan-out. Never invent revenue."""
    now = utc_now_iso()
    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
    ]
    # Count distinct families per observed page using family_id when present,
    # otherwise each candidate is its own pseudo-family.
    page_families: Dict[str, set] = defaultdict(set)
    for cand in candidates:
        page = cand.get("primary_observed_page") or cand.get("proposed_target_page") or ""
        key = canonical_page_key(page)
        if not key:
            continue
        family_token = cand.get("family_id") or cand["candidate_id"]
        page_families[key].add(family_token)

    assigned = 0
    homepage_zero = 0
    unavailable = 0
    histogram: Counter = Counter()

    for cand in candidates:
        page = cand.get("primary_observed_page") or cand.get("proposed_target_page") or ""
        key = canonical_page_key(page)
        is_home = bool(key and is_homepage_page_key(key))
        family_count = len(page_families.get(key, set())) if key else 0
        multiplier = ga4_confidence_multiplier_for_counts(
            family_count_on_page=family_count,
            is_homepage=is_home,
            ga4_match_status=cand.get("ga4_match_status"),
        )
        store.update_keyword_candidate(
            cand["candidate_id"],
            {"ga4_confidence_multiplier": multiplier, "updated_at": now},
        )
        assigned += 1
        if multiplier is None:
            unavailable += 1
            histogram["unavailable"] += 1
        else:
            histogram[str(multiplier)] += 1
            if multiplier == 0.0:
                homepage_zero += 1

    return {
        "candidates_updated": assigned,
        "unavailable": unavailable,
        "homepage_zero": homepage_zero,
        "multiplier_histogram": dict(histogram),
        "pages_with_families": {
            page: len(fams) for page, fams in list(page_families.items())[:50]
        },
    }
