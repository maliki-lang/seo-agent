"""Shared helpers for portfolio selection and quality gates (Phase 13)."""

from __future__ import annotations

from typing import Any, Dict, Set

from ..enums import TargetPageStatus
from .policy import CatalogueSelectionPolicy

ACTIONABLE_TARGETS = {
    TargetPageStatus.OBSERVED_PAGE_SUITABLE.value,
    TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
    TargetPageStatus.MULTIPLE_PAGES_COMPETING.value,
    TargetPageStatus.APPROVED_NEW_PAGE.value,
}

AUDIENCE_TOKENS = (
    "women",
    "woman",
    "ladies",
    "office",
    "work",
    "standing",
    "nurses",
    "teachers",
    "kids",
    "elderly",
    "pregnant",
)


def coverage_tags(cand: Dict[str, Any], policy: CatalogueSelectionPolicy) -> Set[str]:
    """Stable coverage tags used for incremental portfolio diversity."""
    text = (cand.get("normalized_keyword") or cand.get("canonical_keyword") or "").lower()
    tags: Set[str] = set()
    for term in policy.product_terms:
        if term and term in text:
            tags.add(f"product:{term}")
    for term in policy.need_terms:
        if term and term in text:
            tags.add(f"need:{term}")
    padded = f" {text} "
    for term in AUDIENCE_TOKENS:
        if term in text.split() or f" {term} " in padded:
            tags.add(f"audience:{term}")
    intent = cand.get("search_intent") or ""
    if intent:
        tags.add(f"intent:{intent}")
    lane = cand.get("strategic_lane") or ""
    if lane:
        tags.add(f"lane:{lane}")
    return tags


def incremental_coverage_for_candidate(
    cand: Dict[str, Any],
    *,
    policy: CatalogueSelectionPolicy,
    covered: Set[str],
) -> float:
    tags = coverage_tags(cand, policy)
    if not tags:
        return 0.35
    novel = tags - covered
    ratio = len(novel) / max(len(tags), 1)
    return round(0.35 + 0.65 * ratio, 4)


def is_broad_head_term(cand: Dict[str, Any], policy: CatalogueSelectionPolicy) -> bool:
    text = (cand.get("normalized_keyword") or "").strip().lower()
    tokens = [t for t in text.split() if t]
    if not tokens or len(tokens) > 2:
        return False
    product = set(policy.product_terms)
    need = set(policy.need_terms)
    commercial = set(policy.commercial_terms)
    location = set(policy.location_terms)
    non_loc = [t for t in tokens if t not in location]
    if not non_loc:
        return False
    if any(t in need or t in commercial for t in non_loc):
        return False
    return all(t in product for t in non_loc)
