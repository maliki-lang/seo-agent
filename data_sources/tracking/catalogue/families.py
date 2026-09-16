"""Conservative keyword-family derivation (Phase 11).

Keeps exact normalized_keyword as candidate identity. Family membership is a
separate, deterministic layer — never fuzzy-merge by edit distance alone.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import TrackingConfig
from ..enums import (
    EligibilityStatus,
    FamilyRole,
)
from ..exceptions import DataQualityError
from ..reports.metrics_calc import impression_weighted_position, weighted_ctr
from ..storage import TrackingStore
from ..transforms.normalize import natural_key, utc_now_iso
from . import SELECTION_METHODOLOGY_V2
from .policy import CatalogueSelectionPolicy, policy_from_config
from .target_pages import (
    apply_ga4_shared_discount,
    evaluate_targets_for_build,
)

FAMILY_METHOD = "conservative_rules_v1"

_PRODUCT_TOKENS = frozenset(
    {
        "shoe",
        "shoes",
        "sneaker",
        "sneakers",
        "sandal",
        "sandals",
        "loafer",
        "loafers",
        "mule",
        "mules",
        "slipper",
        "slippers",
        "flat",
        "flats",
    }
)
_PLACE_TOKENS = frozenset(
    {
        "nex",
        "westgate",
        "bugis",
        "jurong",
        "orchard",
        "ion",
        "vivocity",
        "tampines",
        "jem",
        "plaza",
        "singpost",
        "outlet",
        "outlets",
        "mall",
    }
)
_PLURAL_MAP = {
    "shoes": "shoe",
    "sneakers": "sneaker",
    "sandals": "sandal",
    "loafers": "loafer",
    "mules": "mule",
    "slippers": "slipper",
    "flats": "flat",
    "shops": "shop",
    "stores": "store",
    "outlets": "outlet",
}
# Do not strip modifiers that change demand family (best, for, women, kids, …).


def _tokens(text: str) -> List[str]:
    return [t for t in (text or "").lower().split() if t]


def _singularize_token(token: str, *, enabled: bool) -> str:
    if not enabled:
        return token
    return _PLURAL_MAP.get(token, token)


def _apply_shop_store(token: str, *, enabled: bool) -> str:
    if enabled and token == "store":
        return "shop"
    return token


def family_signature(normalized_keyword: str, policy: CatalogueSelectionPolicy) -> str:
    """Deterministic family key from an exact normalized candidate."""
    tokens = _tokens(normalized_keyword)
    if not tokens:
        return ""

    normalized_tokens = [
        _apply_shop_store(
            _singularize_token(tok, enabled=policy.family_singular_plural),
            enabled=policy.family_shop_store_equivalence,
        )
        for tok in tokens
    ]

    # Collapse "near me" into a stable unit token for signature purposes.
    collapsed: List[str] = []
    i = 0
    while i < len(normalized_tokens):
        if (
            i + 1 < len(normalized_tokens)
            and normalized_tokens[i] == "near"
            and normalized_tokens[i + 1] == "me"
        ):
            collapsed.append("near_me")
            i += 2
            continue
        collapsed.append(normalized_tokens[i])
        i += 1

    place = [t for t in collapsed if t in _PLACE_TOKENS]
    productish = [t for t in collapsed if t in _PRODUCT_TOKENS or t in {"shop", "near_me"}]
    other = [
        t
        for t in collapsed
        if t not in place and t not in _PRODUCT_TOKENS and t not in {"shop", "near_me"}
    ]

    # Safe local word-order: place + product(+optional shop) with no extra modifiers.
    # Optional shop/store is dropped so "nex shoes" and "nex shoes shop" share a family.
    if (
        policy.family_safe_word_order
        and len(place) == 1
        and any(t in _PRODUCT_TOKENS for t in collapsed)
        and not other
    ):
        body = sorted(t for t in collapsed if t in _PRODUCT_TOKENS)
        return " ".join(place + body)

    # Near-me shop/store families: sort body tokens, keep near_me last; shop/store → shop.
    if policy.family_safe_word_order and "near_me" in collapsed and not other:
        body = sorted(
            t
            for t in collapsed
            if t != "near_me" and t in (_PRODUCT_TOKENS | {"shop"})
        )
        # Ensure shop presence is normalized: if any shop appeared, keep one shop token.
        if "shop" in collapsed or any(t == "shop" for t in body):
            body = sorted(t for t in body if t != "shop") + ["shop"]
        return " ".join(body + ["near_me"])

    # Default: preserve token order after singular/shop normalization only.
    return " ".join(collapsed)


def family_label_from_key(family_key: str) -> str:
    return family_key.replace("near_me", "near me").strip() or family_key


def _naturalness_score(normalized: str, family_key: str) -> float:
    """Higher is better. Prefer place-first local queries over awkward reversals."""
    tokens = _tokens(normalized)
    score = 0.0
    # Prefer shorter display forms within a family.
    score += max(0.0, 12.0 - len(tokens)) * 0.05
    place = [t for t in tokens if t in _PLACE_TOKENS]
    if place:
        # Prefer place token earlier in the phrase.
        idx = tokens.index(place[0])
        score += max(0.0, 3.0 - idx)
        if tokens and tokens[0] in _PLACE_TOKENS:
            score += 1.0
    if "near" in tokens and "me" in tokens:
        # Prefer "... near me" ending.
        if len(tokens) >= 2 and tokens[-2:] == ["near", "me"]:
            score += 1.5
    # Prefer singular shop phrasing closeness to family key length.
    score += 0.1 * (1.0 / (1 + abs(len(normalized) - len(family_key))))
    return score


def _intent_fit(candidate: Dict[str, Any]) -> float:
    eligibility = candidate.get("eligibility_status") or ""
    if eligibility == EligibilityStatus.ELIGIBLE.value:
        return 1.0
    if eligibility == EligibilityStatus.ELIGIBLE_WITH_REVIEW.value:
        return 0.7
    if eligibility == EligibilityStatus.PENDING_CLASSIFICATION.value:
        return 0.4
    return 0.1


def choose_family_primary(
    members: Sequence[Dict[str, Any]],
    *,
    family_key: str,
) -> Dict[str, Any]:
    """Deterministic primary: naturalness → intent → actionability → evidence → impressions."""

    def sort_key(cand: Dict[str, Any]) -> Tuple:
        return (
            _naturalness_score(cand.get("normalized_keyword") or "", family_key),
            _intent_fit(cand),
            float(cand.get("target_actionability_score") or 0.0),
            float(cand.get("evidence_confidence_score") or 0.0),
            int(cand.get("gsc_clicks") or 0),
            int(cand.get("gsc_impressions") or 0),
            # Stable final tie-break: lexicographic normalized keyword ascending
            # inverted via negative-less string compare by using keyword itself last ascending.
            cand.get("normalized_keyword") or "",
        )

    # Highest tuple wins; for keyword string we want ascending so negate by sorting reverse
    # on numeric parts only — easiest: sort reverse=True but put keyword as last with reverse
    # meaning we'd get Z first. Use ascending keyword by flipping: sort with reverse on a
    # key that uses negative keyword via custom.
    ranked = sorted(
        members,
        key=lambda c: (
            _naturalness_score(c.get("normalized_keyword") or "", family_key),
            _intent_fit(c),
            float(c.get("target_actionability_score") or 0.0),
            float(c.get("evidence_confidence_score") or 0.0),
            int(c.get("gsc_clicks") or 0),
            int(c.get("gsc_impressions") or 0),
        ),
        reverse=True,
    )
    # Stable among equal scores: prefer lexicographically smaller keyword.
    top_score = None
    best: Optional[Dict[str, Any]] = None
    for cand in ranked:
        score = (
            _naturalness_score(cand.get("normalized_keyword") or "", family_key),
            _intent_fit(cand),
            float(cand.get("target_actionability_score") or 0.0),
            float(cand.get("evidence_confidence_score") or 0.0),
            int(cand.get("gsc_clicks") or 0),
            int(cand.get("gsc_impressions") or 0),
        )
        if top_score is None or score > top_score:
            top_score = score
            best = cand
        elif score == top_score and (cand.get("normalized_keyword") or "") < (
            best.get("normalized_keyword") or ""
        ):
            best = cand
    assert best is not None
    return best


def _family_aggregates(members: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    clicks = sum(int(m.get("gsc_clicks") or 0) for m in members)
    impressions = sum(int(m.get("gsc_impressions") or 0) for m in members)
    # Reconstruct impression-weighted position from candidate weighted fields.
    rows = []
    for m in members:
        impr = int(m.get("gsc_impressions") or 0)
        pos = m.get("gsc_weighted_position")
        if impr > 0 and pos is not None:
            rows.append({"impressions": impr, "position": float(pos), "clicks": int(m.get("gsc_clicks") or 0)})
    return {
        "family_gsc_clicks": clicks,
        "family_gsc_impressions": impressions,
        "family_weighted_ctr": weighted_ctr(clicks, impressions),
        "family_weighted_position": impression_weighted_position(rows) if rows else None,
    }


def derive_families_for_build(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    policy: Optional[CatalogueSelectionPolicy] = None,
    refresh_targets: bool = True,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    selection_policy = policy or policy_from_config(config)
    confidence_floor = (
        selection_policy.family_min_confidence if min_confidence is None else float(min_confidence)
    )

    if refresh_targets:
        evaluate_targets_for_build(store, config, build_id=build_id, apply_ga4_discount=False)

    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? ORDER BY normalized_keyword",
            (build_id,),
        )
    ]
    if not candidates:
        raise DataQualityError(f"No keyword candidates for build {build_id}")

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for cand in candidates:
        key = family_signature(cand["normalized_keyword"], selection_policy)
        if not key:
            key = cand["normalized_keyword"]
        groups[key].append(cand)

    now = utc_now_iso()
    family_rows: List[Dict[str, Any]] = []
    examples: Dict[str, List[str]] = {}
    multi_member = 0
    demoted_variants = 0

    store.delete_keyword_families_for_build(build_id)

    for family_key, members in sorted(groups.items(), key=lambda item: item[0]):
        primary = choose_family_primary(members, family_key=family_key)
        member_ids = [m["candidate_id"] for m in members]
        confidence = 1.0 if len(members) == 1 else 0.95
        auto_ok = confidence >= confidence_floor
        aggregates = _family_aggregates(members)
        family_id = natural_key([build_id, family_key])
        label = family_label_from_key(family_key)
        routing = primary.get("routing_bucket")
        intent = primary.get("search_intent")
        lane = primary.get("strategic_lane")
        target = primary.get("reviewed_target_page") or primary.get("proposed_target_page") or primary.get(
            "primary_observed_page"
        )

        family_rows.append(
            {
                "family_id": family_id,
                "build_id": build_id,
                "family_key": family_key,
                "family_label": label,
                "primary_candidate_id": primary["candidate_id"] if auto_ok else None,
                "routing_bucket": routing,
                "search_intent": intent,
                "strategic_lane": lane,
                "member_candidate_ids_json": member_ids,
                "member_count": len(members),
                "primary_target_page": target,
                "family_method": FAMILY_METHOD,
                "family_confidence": confidence,
                "approval_status": "draft",
                "created_at": now,
                "updated_at": now,
                **aggregates,
            }
        )
        if len(members) > 1:
            multi_member += 1
            if len(examples) < 12:
                examples[family_key] = [m["normalized_keyword"] for m in members]

        for member in members:
            is_primary = member["candidate_id"] == primary["candidate_id"] and auto_ok
            role = FamilyRole.PRIMARY.value if is_primary else FamilyRole.VARIANT.value
            fields: Dict[str, Any] = {
                "family_id": family_id,
                "family_role": role,
                "family_method": FAMILY_METHOD,
                "family_confidence": confidence,
                "updated_at": now,
            }
            if (
                not is_primary
                and auto_ok
                and (member.get("eligibility_status") or "")
                in {
                    EligibilityStatus.ELIGIBLE.value,
                    EligibilityStatus.ELIGIBLE_WITH_REVIEW.value,
                    EligibilityStatus.PENDING_CLASSIFICATION.value,
                    "",
                    None,
                }
            ):
                # Non-primary variants are not auto-selected portfolio representatives.
                fields["eligibility_status"] = EligibilityStatus.INELIGIBLE_DUPLICATE_VARIANT.value
                fields["eligibility_reasons_json"] = [
                    "non_primary_family_variant",
                    f"family_key:{family_key}",
                    f"primary_candidate_id:{primary['candidate_id']}",
                ]
                demoted_variants += 1
            store.update_keyword_candidate(member["candidate_id"], fields)

    store.insert_keyword_families(family_rows)
    ga4_report = apply_ga4_shared_discount(store, build_id=build_id)

    report = {
        "build_id": build_id,
        "methodology_version": SELECTION_METHODOLOGY_V2,
        "family_method": FAMILY_METHOD,
        "family_count": len(family_rows),
        "multi_member_family_count": multi_member,
        "singleton_family_count": len(family_rows) - multi_member,
        "candidate_count": len(candidates),
        "demoted_duplicate_variants": demoted_variants,
        "examples": examples,
        "ga4_shared_discount": ga4_report,
        "derived_at": now,
    }
    store.update_catalogue_build(build_id, {"family_report_json": report})
    return report


def unsafe_merge_pairs() -> List[Tuple[str, str]]:
    """Regression pairs that must remain separate families under conservative rules."""
    return [
        ("walking shoes", "best walking shoes for women"),
        ("shoes singapore", "kids shoes singapore"),
        ("comfortable shoes", "comfortable sandals singapore"),
    ]
