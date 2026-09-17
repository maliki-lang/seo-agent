"""Coverage-constrained portfolio selection (Phase 13)."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..config import TrackingConfig
from ..enums import (
    BrandStatus,
    CandidateDecision,
    EligibilityStatus,
    FamilyRole,
    StrategicLane,
    TargetPageStatus,
)
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from . import SELECTION_METHODOLOGY_V2
from .policy import CatalogueSelectionPolicy, policy_from_config
from .quality_gates import evaluate_portfolio_gates
from .scoring import hard_exclusion_reasons, score_candidate_v2, score_v2_update_fields
from .selection_helpers import (
    ACTIONABLE_TARGETS,
    coverage_tags,
    incremental_coverage_for_candidate,
    is_broad_head_term,
)

_ELIGIBLE = {
    EligibilityStatus.ELIGIBLE.value,
    EligibilityStatus.ELIGIBLE_WITH_REVIEW.value,
}

# Residual / redistributed fills must clear this floor; better to underfill than pad with junk.
_MIN_SCORE_FOR_EXTRA_FILL = 0.45

_LANE_ORDER = [
    StrategicLane.NEED_STATE.value,
    StrategicLane.PRODUCT_CATEGORY.value,
    StrategicLane.USE_CASE_AUDIENCE.value,
    StrategicLane.LOCAL_STORE.value,
    StrategicLane.COMMERCIAL_DISCOVERY.value,
    StrategicLane.COMPETITOR_DISCOVERY.value,
    StrategicLane.STRATEGIC_GAP.value,
    StrategicLane.INFORMATIONAL_EDITORIAL.value,
]


def _parse_json_field(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _has_serper(cand: Dict[str, Any]) -> bool:
    return cand.get("serper_position") is not None and bool(
        cand.get("serper_run_id")
        or cand.get("serper_collected_at")
        or cand.get("serper_validation_score") is not None
    )


def _rank_tuple(
    cand: Dict[str, Any],
    policy: CatalogueSelectionPolicy,
    *,
    covered: Set[str],
    require_serper: bool,
) -> Tuple:
    incr = incremental_coverage_for_candidate(cand, policy=policy, covered=covered)
    breakdown = score_candidate_v2(
        cand,
        score_weights=policy.score_weights,
        penalties=policy.penalties,
        min_impressions=policy.min_impressions,
        require_serper=require_serper,
        incremental_coverage_score=incr,
    )
    score = breakdown.selection_score_v2
    preselected_boost = 1 if int(cand.get("serp_preselected") or 0) else 0
    actionable_boost = 1 if (cand.get("target_page_status") or "") in ACTIONABLE_TARGETS else 0
    return (
        score if score is not None else -1.0,
        actionable_boost,
        preselected_boost,
        float(cand.get("gsc_impressions") or 0),
        float(cand.get("gsc_clicks") or 0),
        cand.get("normalized_keyword") or "",
        incr,
        breakdown,
    )


def _lane_cap_allows(
    lane: str,
    *,
    lane_counts: Dict[str, int],
    broad_count: int,
    is_broad: bool,
    policy: CatalogueSelectionPolicy,
) -> bool:
    cap = policy.lane_caps.get(lane)
    if cap is not None and lane_counts[lane] >= cap:
        return False
    broad_cap = policy.lane_caps.get("broad_head_term")
    if is_broad and broad_cap is not None and broad_count >= broad_cap:
        return False
    return True


def _eligible_primaries(
    candidates: Sequence[Dict[str, Any]],
    *,
    policy: CatalogueSelectionPolicy,
    require_serper: bool,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for cand in candidates:
        if (cand.get("family_role") or "") != FamilyRole.PRIMARY.value:
            continue
        if (cand.get("eligibility_status") or "") not in _ELIGIBLE:
            continue
        brand = cand.get("brand_status") or ""
        if brand in {BrandStatus.BRANDED.value, BrandStatus.AMBIGUOUS_BRAND.value}:
            continue
        target_status = cand.get("target_page_status") or ""
        if target_status == TargetPageStatus.NO_SENSIBLE_TARGET.value:
            continue
        # Auto-select only actionable targets; homepage/manual stay in exception review.
        if target_status not in ACTIONABLE_TARGETS:
            continue
        exclusions = list(hard_exclusion_reasons(cand))
        if require_serper and not _has_serper(cand):
            exclusions.append("missing_required_serper_validation")
        if exclusions:
            continue
        lane = cand.get("strategic_lane") or StrategicLane.STRATEGIC_GAP.value
        enriched = dict(cand)
        enriched["strategic_lane"] = lane
        out.append(enriched)
    return out


def _clear_portfolio_fields(
    store: TrackingStore,
    candidates: Sequence[Dict[str, Any]],
    *,
    now: str,
) -> None:
    for cand in candidates:
        fields: Dict[str, Any] = {
            "alternate_rank": None,
            "selection_rank_within_lane": None,
            "portfolio_slot": None,
            "review_group": None,
            "updated_at": now,
        }
        if cand.get("decision") == CandidateDecision.SELECTED.value:
            fields["decision"] = CandidateDecision.PENDING.value
            existing = cand.get("decision_reason") or ""
            prefix = "cleared_for_portfolio_v2"
            fields["decision_reason"] = f"{prefix};{existing}" if existing else prefix
        store.update_keyword_candidate(cand["candidate_id"], fields)


def _branded_benchmarks(
    candidates: Sequence[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    branded = [
        c
        for c in candidates
        if (c.get("brand_status") or "") == BrandStatus.BRANDED.value
        and (c.get("family_role") or "") in {"", FamilyRole.PRIMARY.value, None}
    ]
    branded.sort(
        key=lambda c: (
            float(c.get("gsc_impressions") or 0),
            float(c.get("gsc_clicks") or 0),
            c.get("normalized_keyword") or "",
        ),
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    used_families: Set[str] = set()
    for cand in branded:
        fam = cand.get("family_id") or cand["candidate_id"]
        if fam in used_families:
            continue
        used_families.add(fam)
        out.append(
            {
                "candidate_id": cand["candidate_id"],
                "normalized_keyword": cand.get("normalized_keyword"),
                "family_id": cand.get("family_id"),
                "gsc_impressions": cand.get("gsc_impressions"),
                "gsc_clicks": cand.get("gsc_clicks"),
            }
        )
        if len(out) >= limit:
            break
    return out


def _manual_exceptions(
    candidates: Sequence[Dict[str, Any]], selected_ids: Set[str]
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for cand in candidates:
        if cand["candidate_id"] in selected_ids:
            continue
        reasons = []
        if (cand.get("brand_status") or "") == BrandStatus.AMBIGUOUS_BRAND.value:
            reasons.append("ambiguous_brand")
        if int(cand.get("claims_review_required") or 0):
            reasons.append("claims_review_required")
        if (cand.get("target_page_status") or "") == TargetPageStatus.HOMEPAGE_UNRESOLVED.value:
            reasons.append("homepage_unresolved")
        if (cand.get("eligibility_status") or "") == EligibilityStatus.ELIGIBLE_WITH_REVIEW.value:
            reasons.append("eligible_with_review")
        if not reasons:
            continue
        if (cand.get("family_role") or "") == FamilyRole.VARIANT.value:
            continue
        rows.append(
            {
                "candidate_id": cand["candidate_id"],
                "normalized_keyword": cand.get("normalized_keyword") or "",
                "reasons": ",".join(reasons),
            }
        )
    rows.sort(key=lambda r: r["normalized_keyword"])
    return rows[:100]


def select_portfolio(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    policy: Optional[CatalogueSelectionPolicy] = None,
    require_serper: Optional[bool] = None,
) -> Dict[str, Any]:
    """Rewrite decision=selected into a quota/cap-constrained family-primary portfolio."""
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    families = store.fetchall("SELECT * FROM keyword_families WHERE build_id = ?", (build_id,))
    if not families:
        raise DataQualityError(
            f"No keyword_families for build {build_id}. Run catalogue derive-families first."
        )

    selection_policy = policy or policy_from_config(config)
    must_serper = (
        selection_policy.require_serper_for_selection
        if require_serper is None
        else bool(require_serper)
    )
    now = utc_now_iso()

    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
    ]
    previous_selected = [
        {
            "candidate_id": c["candidate_id"],
            "normalized_keyword": c.get("normalized_keyword"),
            "strategic_lane": c.get("strategic_lane"),
            "brand_status": c.get("brand_status"),
        }
        for c in candidates
        if c.get("decision") == CandidateDecision.SELECTED.value
    ]
    previous_ids = {r["candidate_id"] for r in previous_selected}

    _clear_portfolio_fields(store, candidates, now=now)
    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
    ]

    primaries = _eligible_primaries(
        candidates, policy=selection_policy, require_serper=must_serper
    )
    by_lane: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for cand in primaries:
        by_lane[cand["strategic_lane"]].append(cand)

    selected: List[Dict[str, Any]] = []
    selection_meta: Dict[str, Dict[str, Any]] = {}
    lane_counts: Dict[str, int] = defaultdict(int)
    lane_rank: Dict[str, int] = defaultdict(int)
    used_families: Set[str] = set()
    covered: Set[str] = set()
    broad_count = 0
    shortages: Dict[str, int] = {}

    def try_add(cand: Dict[str, Any], *, reason: str) -> bool:
        nonlocal broad_count
        fam_id = cand.get("family_id")
        if fam_id and fam_id in used_families:
            return False
        lane = cand["strategic_lane"]
        is_broad = is_broad_head_term(cand, selection_policy)
        if not _lane_cap_allows(
            lane,
            lane_counts=lane_counts,
            broad_count=broad_count,
            is_broad=is_broad,
            policy=selection_policy,
        ):
            return False
        rank_info = _rank_tuple(
            cand, selection_policy, covered=covered, require_serper=must_serper
        )
        score, _, _, _, _, _, incr, breakdown = rank_info
        if breakdown.hard_excluded or score is None:
            return False
        selected.append(cand)
        lane_counts[lane] += 1
        lane_rank[lane] += 1
        if fam_id:
            used_families.add(fam_id)
        if is_broad:
            broad_count += 1
        tags = coverage_tags(cand, selection_policy)
        covered.update(tags)
        selection_meta[cand["candidate_id"]] = {
            "reason": reason,
            "incremental_coverage_score": incr,
            "selection_score_v2": breakdown.selection_score_v2,
            "breakdown": breakdown,
            "lane_rank": lane_rank[lane],
            "coverage_tags_added": sorted(tags),
            "is_broad_head_term": is_broad,
        }
        return True

    for lane in _LANE_ORDER:
        quota = int(selection_policy.lane_quotas.get(lane, 0))
        if quota <= 0:
            continue
        pool = list(by_lane.get(lane, []))
        filled = 0
        while filled < quota:
            best = None
            best_key = None
            for cand in pool:
                if cand["candidate_id"] in selection_meta:
                    continue
                fam_id = cand.get("family_id")
                if fam_id and fam_id in used_families:
                    continue
                is_broad = is_broad_head_term(cand, selection_policy)
                if not _lane_cap_allows(
                    lane,
                    lane_counts=lane_counts,
                    broad_count=broad_count,
                    is_broad=is_broad,
                    policy=selection_policy,
                ):
                    continue
                key = _rank_tuple(
                    cand, selection_policy, covered=covered, require_serper=must_serper
                )[:6]
                if best is None or key > best_key:
                    best = cand
                    best_key = key
            if best is None:
                break
            if try_add(best, reason=f"lane_quota:{lane}"):
                filled += 1
            else:
                break
        shortages[lane] = max(0, quota - filled)

    remaining = selection_policy.selected_limit - len(selected)
    for lane in selection_policy.fallback_order:
        if remaining <= 0:
            break
        pool = list(by_lane.get(lane, []))
        while remaining > 0:
            best = None
            best_key = None
            for cand in pool:
                if cand["candidate_id"] in selection_meta:
                    continue
                fam_id = cand.get("family_id")
                if fam_id and fam_id in used_families:
                    continue
                is_broad = is_broad_head_term(cand, selection_policy)
                if not _lane_cap_allows(
                    lane,
                    lane_counts=lane_counts,
                    broad_count=broad_count,
                    is_broad=is_broad,
                    policy=selection_policy,
                ):
                    continue
                key = _rank_tuple(
                    cand, selection_policy, covered=covered, require_serper=must_serper
                )
                if key[0] < _MIN_SCORE_FOR_EXTRA_FILL:
                    continue
                cmp_key = key[:6]
                if best is None or cmp_key > best_key:
                    best = cand
                    best_key = cmp_key
            if best is None:
                break
            if try_add(best, reason=f"fallback_redistribute:{lane}"):
                remaining -= 1
            else:
                break

    if len(selected) < selection_policy.selected_limit:
        leftovers = [c for c in primaries if c["candidate_id"] not in selection_meta]
        while len(selected) < selection_policy.selected_limit:
            best = None
            best_key = None
            for cand in leftovers:
                if cand["candidate_id"] in selection_meta:
                    continue
                fam_id = cand.get("family_id")
                if fam_id and fam_id in used_families:
                    continue
                lane = cand["strategic_lane"]
                is_broad = is_broad_head_term(cand, selection_policy)
                if not _lane_cap_allows(
                    lane,
                    lane_counts=lane_counts,
                    broad_count=broad_count,
                    is_broad=is_broad,
                    policy=selection_policy,
                ):
                    continue
                key = _rank_tuple(
                    cand, selection_policy, covered=covered, require_serper=must_serper
                )
                if key[0] < _MIN_SCORE_FOR_EXTRA_FILL:
                    continue
                cmp_key = key[:6]
                if best is None or cmp_key > best_key:
                    best = cand
                    best_key = cmp_key
            if best is None:
                break
            if not try_add(best, reason="residual_fill"):
                break

    selected = selected[: selection_policy.selected_limit]
    selected_ids = {c["candidate_id"] for c in selected}

    alternate_candidates: List[Dict[str, Any]] = []
    for primary in selected:
        lane = primary["strategic_lane"]
        best_alt = None
        best_key = None
        for cand in by_lane.get(lane, []):
            if cand["candidate_id"] in selected_ids:
                continue
            if cand["candidate_id"] in {a["candidate_id"] for a in alternate_candidates}:
                continue
            fam_id = cand.get("family_id")
            if fam_id and fam_id in used_families:
                continue
            key = _rank_tuple(
                cand, selection_policy, covered=covered, require_serper=must_serper
            )[:6]
            if best_alt is None or key > best_key:
                best_alt = cand
                best_key = key
        if best_alt is not None:
            alternate_candidates.append(best_alt)
            fam_id = best_alt.get("family_id")
            if fam_id:
                used_families.add(fam_id)

    if len(alternate_candidates) < selection_policy.alternate_limit:
        leftovers = [
            c
            for c in primaries
            if c["candidate_id"] not in selected_ids
            and c["candidate_id"] not in {a["candidate_id"] for a in alternate_candidates}
        ]
        leftovers.sort(
            key=lambda c: _rank_tuple(
                c, selection_policy, covered=covered, require_serper=must_serper
            )[:6],
            reverse=True,
        )
        for cand in leftovers:
            if len(alternate_candidates) >= selection_policy.alternate_limit:
                break
            fam_id = cand.get("family_id")
            if fam_id and fam_id in used_families:
                continue
            alternate_candidates.append(cand)
            if fam_id:
                used_families.add(fam_id)

    alternate_candidates = alternate_candidates[: selection_policy.alternate_limit]

    for slot, cand in enumerate(selected, start=1):
        meta = selection_meta[cand["candidate_id"]]
        breakdown = meta["breakdown"]
        fields = score_v2_update_fields(breakdown)
        reasons = _parse_json_field(fields.get("selection_reasons_json")) or {}
        if not isinstance(reasons, dict):
            reasons = {"reasons": reasons}
        reasons.update(
            {
                "portfolio_reason": meta["reason"],
                "coverage_tags_added": meta["coverage_tags_added"],
                "is_broad_head_term": meta["is_broad_head_term"],
                "selection_sequence": slot,
            }
        )
        fields.update(
            {
                "decision": CandidateDecision.SELECTED.value,
                "decision_reason": f"selected_portfolio_v2:{meta['reason']}",
                "methodology_version": SELECTION_METHODOLOGY_V2,
                "selection_rank_within_lane": meta["lane_rank"],
                "portfolio_slot": slot,
                "alternate_rank": None,
                "review_group": "proposed_55",
                "final_selection_score": breakdown.selection_score_v2,
                "selection_reasons_json": reasons,
                "updated_at": now,
            }
        )
        store.update_keyword_candidate(cand["candidate_id"], fields)

    for rank, cand in enumerate(alternate_candidates, start=1):
        incr = incremental_coverage_for_candidate(
            cand, policy=selection_policy, covered=covered
        )
        breakdown = score_candidate_v2(
            cand,
            score_weights=selection_policy.score_weights,
            penalties=selection_policy.penalties,
            min_impressions=selection_policy.min_impressions,
            require_serper=must_serper,
            incremental_coverage_score=incr,
        )
        fields = score_v2_update_fields(breakdown)
        reasons = _parse_json_field(fields.get("selection_reasons_json")) or {}
        if not isinstance(reasons, dict):
            reasons = {"reasons": reasons}
        reasons["portfolio_reason"] = "alternate"
        fields.update(
            {
                "decision": CandidateDecision.PENDING.value,
                "decision_reason": "alternate_portfolio_v2",
                "methodology_version": SELECTION_METHODOLOGY_V2,
                "portfolio_slot": None,
                "alternate_rank": rank,
                "review_group": "alternates_15",
                "selection_reasons_json": reasons,
                "updated_at": now,
            }
        )
        store.update_keyword_candidate(cand["candidate_id"], fields)

    branded = _branded_benchmarks(
        candidates, limit=selection_policy.branded_benchmark_limit
    )
    for item in branded:
        store.update_keyword_candidate(
            item["candidate_id"],
            {"review_group": "branded_benchmark", "updated_at": now},
        )

    exception_rows = _manual_exceptions(candidates, selected_ids)
    for row in exception_rows:
        store.update_keyword_candidate(
            row["candidate_id"],
            {"review_group": "manual_review_exceptions", "updated_at": now},
        )

    new_ids = {c["candidate_id"] for c in selected}
    removed = [r for r in previous_selected if r["candidate_id"] not in new_ids]
    added = [
        {
            "candidate_id": c["candidate_id"],
            "normalized_keyword": c.get("normalized_keyword"),
            "strategic_lane": c.get("strategic_lane"),
        }
        for c in selected
        if c["candidate_id"] not in previous_ids
    ]

    comparison = {
        "methodology_label": f"v1_selected_vs_{SELECTION_METHODOLOGY_V2}",
        "previous_selected_count": len(previous_selected),
        "new_selected_count": len(selected),
        "overlap_count": len(previous_ids & new_ids),
        "removed_count": len(removed),
        "added_count": len(added),
        "removed_examples": removed[:15],
        "added_examples": added[:15],
        "lane_counts": dict(lane_counts),
        "competitor_count": lane_counts.get(StrategicLane.COMPETITOR_DISCOVERY.value, 0),
        "local_count": lane_counts.get(StrategicLane.LOCAL_STORE.value, 0),
        "unique_family_count": len({c.get("family_id") for c in selected if c.get("family_id")}),
        "actionable_target_count": sum(
            1 for c in selected if (c.get("target_page_status") or "") in ACTIONABLE_TARGETS
        ),
        "serper_validated_count": sum(1 for c in selected if _has_serper(c)),
        "broad_head_term_count": broad_count,
    }

    report: Dict[str, Any] = {
        "build_id": build_id,
        "methodology_version": SELECTION_METHODOLOGY_V2,
        "policy_fingerprint": selection_policy.fingerprint(),
        "policy_version": selection_policy.policy_version,
        "require_serper": must_serper,
        "selected_limit": selection_policy.selected_limit,
        "alternate_limit": selection_policy.alternate_limit,
        "selected_count": len(selected),
        "alternate_count": len(alternate_candidates),
        "eligible_primary_count": len(primaries),
        "eligible_shortage": max(0, selection_policy.selected_limit - len(selected)),
        "lane_quotas": dict(selection_policy.lane_quotas),
        "lane_counts": dict(lane_counts),
        "lane_shortages": shortages,
        "broad_head_term_count": broad_count,
        "selected_candidate_ids": [c["candidate_id"] for c in selected],
        "alternate_candidate_ids": [c["candidate_id"] for c in alternate_candidates],
        "branded_benchmarks": branded,
        "manual_review_exceptions_count": len(exception_rows),
        "comparison_v1_v2": comparison,
        "selected_at": now,
    }

    quality = evaluate_portfolio_gates(
        store,
        config,
        build_id=build_id,
        policy=selection_policy,
        portfolio_report=report,
    )
    report["quality_gate"] = quality

    store.update_catalogue_build(
        build_id,
        {
            "portfolio_report_json": report,
            "quality_exceptions_json": {
                "manual_review_exceptions": exception_rows,
                "branded_benchmarks": branded,
            },
            "selection_policy_version": selection_policy.policy_version,
            "selection_policy_json": selection_policy.to_dict(),
        },
    )
    return report
