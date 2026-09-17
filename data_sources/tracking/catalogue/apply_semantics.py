"""Apply validated LLM pool-semantic fields and regroup families (Phase 14b)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from ..enums import (
    BusinessRelevanceStatus,
    EligibilityStatus,
    FamilyRole,
    ProposedAction,
    SemanticAuthority,
    TargetPageStatus,
)
from ..storage import TrackingStore
from ..transforms.normalize import natural_key, utc_now_iso

_INTENT_TO_LANE = {
    "problem_solution": "need_state",
    "transactional_category": "product_category",
    "commercial_investigation": "commercial_discovery",
    "informational": "informational_editorial",
    "local_store": "local_store",
    "navigational_competitor": "competitor_discovery",
    "navigational_brand": "competitor_discovery",
    "campaign_event": "strategic_gap",
    "ambiguous": "strategic_gap",
}

_ACTIONABILITY_TO_TARGET = {
    ProposedAction.OPTIMIZE_EXISTING.value: TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
    ProposedAction.CONSOLIDATE_COMPETING_PAGES.value: TargetPageStatus.MULTIPLE_PAGES_COMPETING.value,
    ProposedAction.CREATE_NEW_PAGE.value: TargetPageStatus.APPROVED_NEW_PAGE.value,
    ProposedAction.PROTECT_EXISTING.value: TargetPageStatus.OBSERVED_PAGE_SUITABLE.value,
    ProposedAction.MONITOR_ONLY.value: TargetPageStatus.MANUAL_REVIEW.value,
    ProposedAction.NO_ACTION.value: TargetPageStatus.NO_SENSIBLE_TARGET.value,
}


def write_pool_semantic_fields(
    store: TrackingStore,
    *,
    candidate_id: str,
    normalized: Dict[str, Any],
    assessment_id: str,
) -> None:
    """Persist validated LLM semantic fields; never overwrite measured metrics."""
    now = utc_now_iso()
    no_target = bool(normalized.get("no_suitable_target"))
    fields: Dict[str, Any] = {
        "llm_customer_need": normalized.get("customer_need"),
        "llm_intent": normalized.get("search_intent"),
        "llm_business_relevance": normalized.get("business_relevance"),
        "llm_business_relevance_rationale": normalized.get("business_relevance_rationale"),
        "llm_family_key": (normalized.get("family_key") or "").strip().lower(),
        "llm_is_representative": 1 if normalized.get("is_family_representative") else 0,
        "llm_semantic_duplicates_json": normalized.get("semantic_duplicates") or [],
        "llm_recommended_target_page": normalized.get("recommended_target_page"),
        "llm_no_suitable_target": 1 if no_target else 0,
        "llm_actionability": normalized.get("actionability"),
        "llm_actionability_rationale": normalized.get("actionability_rationale"),
        "llm_semantic_confidence": normalized.get("confidence"),
        "semantic_authority": SemanticAuthority.LLM.value,
        "llm_pool_assessment_id": assessment_id,
        "llm_assessment_id": assessment_id,
        "llm_review_stage": "assessment_validated",
        "updated_at": now,
        # Authoritative semantic labels for downstream selection when LLM-valid.
        "search_intent": normalized.get("search_intent"),
        "business_relevance_status": normalized.get("business_relevance"),
        "business_relevance_reason": normalized.get("business_relevance_rationale"),
        "proposed_action": normalized.get("actionability"),
    }
    lane = _INTENT_TO_LANE.get(normalized.get("search_intent") or "")
    if lane:
        fields["strategic_lane"] = lane

    if no_target:
        fields["target_page_status"] = TargetPageStatus.NO_SENSIBLE_TARGET.value
        fields["eligibility_status"] = EligibilityStatus.INELIGIBLE_NO_ACTIONABLE_TARGET.value
    else:
        page = normalized.get("recommended_target_page")
        if page:
            fields["proposed_target_page"] = page
        action = normalized.get("actionability") or ""
        fields["target_page_status"] = _ACTIONABILITY_TO_TARGET.get(
            action, TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value
        )
        # Relevance gate: irrelevant stays out of discovery portfolio.
        if normalized.get("business_relevance") == BusinessRelevanceStatus.IRRELEVANT.value:
            fields["eligibility_status"] = EligibilityStatus.INELIGIBLE_IRRELEVANT.value
        elif (fields.get("eligibility_status") or "") not in {
            EligibilityStatus.ELIGIBLE.value,
            EligibilityStatus.ELIGIBLE_WITH_REVIEW.value,
        }:
            # Promote to eligible-with-review when LLM says relevant and target exists.
            if normalized.get("business_relevance") == BusinessRelevanceStatus.RELEVANT.value:
                fields["eligibility_status"] = EligibilityStatus.ELIGIBLE_WITH_REVIEW.value

    store.update_keyword_candidate(candidate_id, fields)


def mark_awaiting_or_invalid(
    store: TrackingStore,
    *,
    candidate_id: str,
    authority: str,
    assessment_id: Optional[str] = None,
) -> None:
    now = utc_now_iso()
    fields: Dict[str, Any] = {
        "semantic_authority": authority,
        "updated_at": now,
        "llm_review_stage": (
            "awaiting_llm_assessment"
            if authority == SemanticAuthority.AWAITING_LLM.value
            else "llm_output_invalid"
        ),
    }
    if assessment_id:
        fields["llm_assessment_id"] = assessment_id
        fields["llm_pool_assessment_id"] = assessment_id
    store.update_keyword_candidate(candidate_id, fields)


def mark_deterministic_fallback(store: TrackingStore, *, build_id: str) -> Dict[str, Any]:
    """Label candidates still without LLM authority as deterministic_fallback."""
    now = utc_now_iso()
    rows = store.fetchall(
        """
        SELECT candidate_id, semantic_authority FROM keyword_candidates
        WHERE build_id = ?
        """,
        (build_id,),
    )
    marked = 0
    for row in rows:
        auth = row["semantic_authority"]
        if auth in {SemanticAuthority.LLM.value, SemanticAuthority.LLM_INVALID.value}:
            continue
        if auth == SemanticAuthority.AWAITING_LLM.value:
            continue
        store.update_keyword_candidate(
            row["candidate_id"],
            {
                "semantic_authority": SemanticAuthority.DETERMINISTIC_FALLBACK.value,
                "updated_at": now,
            },
        )
        marked += 1
    return {"build_id": build_id, "marked_deterministic_fallback": marked}


def apply_llm_family_regroup(store: TrackingStore, *, build_id: str) -> Dict[str, Any]:
    """
    Regroup LLM-assessed candidates by llm_family_key and assign family primary.
    Deterministic-fallback candidates keep existing family_id/role.
    """
    now = utc_now_iso()
    llm_rows = [
        dict(r)
        for r in store.fetchall(
            """
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND semantic_authority = ?
            """,
            (build_id, SemanticAuthority.LLM.value),
        )
    ]
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in llm_rows:
        key = (row.get("llm_family_key") or "").strip().lower()
        if not key:
            key = f"singleton:{row['candidate_id']}"
        groups[key].append(row)

    family_rows: List[Dict[str, Any]] = []
    families = 0
    primaries = 0
    for family_key, members in groups.items():
        family_id = natural_key([build_id, "llm", family_key])
        reps = [m for m in members if int(m.get("llm_is_representative") or 0) == 1]
        if len(reps) == 1:
            primary = reps[0]
        else:
            ranked = sorted(
                members,
                key=lambda m: (
                    int(m.get("llm_is_representative") or 0),
                    float(m.get("gsc_impressions") or 0),
                    float(m.get("gsc_clicks") or 0),
                    m.get("normalized_keyword") or "",
                ),
                reverse=True,
            )
            primary = ranked[0]
        member_ids = []
        clicks = 0
        impressions = 0
        for member in members:
            role = (
                FamilyRole.PRIMARY.value
                if member["candidate_id"] == primary["candidate_id"]
                else FamilyRole.VARIANT.value
            )
            store.update_keyword_candidate(
                member["candidate_id"],
                {
                    "family_id": family_id,
                    "family_role": role,
                    "family_method": "llm_pool_semantic_v1",
                    "family_confidence": 0.85
                    if member.get("llm_semantic_confidence") == "high"
                    else 0.7
                    if member.get("llm_semantic_confidence") == "medium"
                    else 0.55,
                    "updated_at": now,
                },
            )
            member_ids.append(member["candidate_id"])
            clicks += int(member.get("gsc_clicks") or 0)
            impressions += int(member.get("gsc_impressions") or 0)
            if role == FamilyRole.PRIMARY.value:
                primaries += 1
        family_rows.append(
            {
                "family_id": family_id,
                "build_id": build_id,
                "family_key": family_key,
                "family_label": family_key.replace("_", " "),
                "primary_candidate_id": primary["candidate_id"],
                "routing_bucket": primary.get("routing_bucket"),
                "search_intent": primary.get("llm_intent") or primary.get("search_intent"),
                "strategic_lane": primary.get("strategic_lane"),
                "member_candidate_ids_json": member_ids,
                "member_count": len(member_ids),
                "family_gsc_clicks": clicks,
                "family_gsc_impressions": impressions,
                "family_weighted_ctr": (clicks / impressions) if impressions else None,
                "family_weighted_position": primary.get("gsc_weighted_position"),
                "primary_target_page": primary.get("llm_recommended_target_page")
                or primary.get("proposed_target_page"),
                "family_method": "llm_pool_semantic_v1",
                "family_confidence": 0.8,
                "approval_status": "draft",
                "reviewed_by": None,
                "reviewed_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        families += 1

    # Replace LLM-derived family rows for this build (keep non-llm families if any remain).
    store.execute(
        "DELETE FROM keyword_families WHERE build_id = ? AND family_method = ?",
        (build_id, "llm_pool_semantic_v1"),
    )
    if family_rows:
        store.insert_keyword_families(family_rows)

    return {
        "build_id": build_id,
        "llm_candidates": len(llm_rows),
        "llm_families": families,
        "llm_primaries": primaries,
    }
