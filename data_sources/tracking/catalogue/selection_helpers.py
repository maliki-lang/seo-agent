"""Shared helpers for portfolio selection and quality gates (Phase 13)."""

from __future__ import annotations

from typing import Any, Dict, Set, Tuple

from ..enums import TargetPageStatus
from ..transforms.normalize import is_homepage_page_key
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

# Stopwords stripped when building near-duplicate topic keys.
_TOPIC_STOPWORDS = {
    "a",
    "an",
    "the",
    "for",
    "of",
    "to",
    "and",
    "or",
    "with",
    "your",
    "my",
    "are",
    "is",
    "it",
    "in",
    "on",
    "best",
    "most",
    "good",
    "that",
    "what",
    "when",
    "how",
    "do",
    "does",
    "can",
    "from",
}

# Collapses near-paraphrase clusters Ting flagged (memory foam Qs, walking-all-day, barefoot).
_TOPIC_CANONICAL_PHRASES = (
    ("memory foam", "memory_foam"),
    ("foam shoes", "memory_foam"),
    ("foam shoe", "memory_foam"),
    ("walking all day", "walking_standing_all_day"),
    ("standing all day", "walking_standing_all_day"),
    ("walking and standing", "walking_standing_all_day"),
    ("standing long hours", "walking_standing_all_day"),
    ("barefoot walking", "barefoot"),
    ("walking barefoot", "barefoot"),
    ("barefoot shoes", "barefoot"),
    ("plantar fasciitis", "plantar_fasciitis"),
    ("arch support", "arch_support"),
    ("slip on", "slip_on"),
    ("slips on", "slip_on"),
    ("slip in", "slip_on"),
)


def topic_key(cand: Dict[str, Any]) -> str:
    """Stable topic fingerprint so portfolio keeps ~one primary per near-duplicate cluster."""
    text = (cand.get("normalized_keyword") or cand.get("canonical_keyword") or "").lower()
    text = " ".join(text.split())
    for phrase, canon in _TOPIC_CANONICAL_PHRASES:
        if phrase in text:
            return canon
    tokens = [t for t in text.split() if t and t not in _TOPIC_STOPWORDS]
    # Prefer product/need-ish stems; keep sorted unique for paraphrase stability.
    core = sorted(set(tokens))
    if not core:
        return text or (cand.get("candidate_id") or "unknown")
    return " ".join(core)


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


def portfolio_target_page(cand: Dict[str, Any]) -> str:
    return (
        cand.get("llm_recommended_target_page")
        or cand.get("reviewed_target_page")
        or cand.get("proposed_target_page")
        or cand.get("primary_observed_page")
        or ""
    )


def auto_select_block_reasons(cand: Dict[str, Any]) -> Tuple[str, ...]:
    """Extra Ting acceptance bars beyond eligibility/actionable status."""
    reasons = []
    page = portfolio_target_page(cand)
    if page and is_homepage_page_key(page):
        reasons.append("homepage_target_blocked")
    lane = cand.get("strategic_lane") or ""
    lowered = page.lower()
    if "store-locations" in lowered and lane not in {"local_store"}:
        reasons.append("store_locations_for_non_local_lane")
    if "/pages/jobs" in lowered or page.rstrip("/").endswith("/jobs"):
        reasons.append("careers_page_blocked")

    text = (cand.get("normalized_keyword") or cand.get("canonical_keyword") or "").lower()
    tokens = set(text.split())
    # Sunnystep discovery catalogue is women comfort footwear — keep men/kids out of auto-select.
    if tokens & {"kids", "kid", "children", "boys", "girls"} or "kids shoes" in text:
        reasons.append("off_core_kids_audience")
    if tokens & {"men", "mens", "man", "male"} or "for men" in text or "men's" in text:
        if not (tokens & {"women", "woman", "ladies", "female"}):
            reasons.append("off_core_mens_audience")
    for bad in ("sunnyside", "easy steps", "shoe careers", "shoe stop", "shoe brand"):
        if bad in text:
            reasons.append(f"noise_query:{bad.replace(' ', '_')}")
            break
    return tuple(reasons)
