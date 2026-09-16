"""Lane-balanced Serper preselection pool (Phase 12)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import EligibilityStatus, FamilyRole, StrategicLane
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from . import SELECTION_METHODOLOGY_V2
from .policy import CatalogueSelectionPolicy, policy_from_config
from .scoring import score_candidate_v2, score_v2_update_fields

_ELIGIBLE = {
    EligibilityStatus.ELIGIBLE.value,
    EligibilityStatus.ELIGIBLE_WITH_REVIEW.value,
}

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


def _allocate_lane_slots(lane_quotas: Dict[str, int], limit: int, selected_limit: int) -> Dict[str, int]:
    """Scale portfolio quotas to the Serper preselection limit; keep deterministic."""
    if limit <= 0:
        return {lane: 0 for lane in lane_quotas}
    scale = limit / max(selected_limit, 1)
    raw = {lane: max(0, int(round(quota * scale))) for lane, quota in lane_quotas.items()}
    # Guarantee at least one slot for each configured lane when limit allows.
    if limit >= len(raw):
        for lane in raw:
            if raw[lane] == 0:
                raw[lane] = 1
    total = sum(raw.values())
    lanes_desc = sorted(raw.keys(), key=lambda l: (-raw[l], l))
    # Shrink without infinite-looping when every lane is already at zero/one floor.
    guard = 0
    while total > limit and guard < limit + len(raw) + 10:
        progressed = False
        for lane in lanes_desc:
            if total <= limit:
                break
            if raw[lane] > 0:
                raw[lane] -= 1
                total -= 1
                progressed = True
        if not progressed:
            break
        guard += 1
    guard = 0
    while total < limit and guard < limit + len(raw) + 10:
        progressed = False
        for lane in lanes_desc:
            if total >= limit:
                break
            raw[lane] += 1
            total += 1
            progressed = True
        if not progressed:
            break
        guard += 1
    return raw


def _preliminary_rank_key(cand: Dict[str, Any], policy: CatalogueSelectionPolicy) -> Tuple:
    breakdown = score_candidate_v2(
        cand,
        score_weights=policy.score_weights,
        penalties=policy.penalties,
        min_impressions=policy.min_impressions,
        require_serper=False,
        incremental_coverage_score=float(cand.get("incremental_coverage_score") or 0.5),
    )
    score = breakdown.selection_score_v2
    return (
        score if score is not None else -1.0,
        float(cand.get("gsc_impressions") or 0),
        float(cand.get("gsc_clicks") or 0),
        cand.get("normalized_keyword") or "",
    )


def preselect_serp_pool(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    limit: Optional[int] = None,
    policy: Optional[CatalogueSelectionPolicy] = None,
) -> Dict[str, Any]:
    """Mark a lane-balanced set of distinct family primaries for Serper validation."""
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    selection_policy = policy or policy_from_config(config)
    pool_limit = int(limit if limit is not None else selection_policy.serper_preselection_limit)
    if pool_limit < 1:
        raise DataQualityError("serper preselection limit must be >= 1")

    families = store.fetchall(
        "SELECT * FROM keyword_families WHERE build_id = ?",
        (build_id,),
    )
    if not families:
        raise DataQualityError(
            f"No keyword_families for build {build_id}. Run catalogue derive-families first."
        )

    candidates = [
        dict(row)
        for row in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
    ]
    by_id = {c["candidate_id"]: c for c in candidates}
    now = utc_now_iso()

    # Clear previous preselection flags for this build.
    for cand in candidates:
        if int(cand.get("serp_preselected") or 0) or cand.get("serp_preselect_rank") is not None:
            store.update_keyword_candidate(
                cand["candidate_id"],
                {"serp_preselected": 0, "serp_preselect_rank": None, "updated_at": now},
            )

    primaries: List[Dict[str, Any]] = []
    for fam in families:
        pid = fam["primary_candidate_id"]
        if not pid or pid not in by_id:
            continue
        cand = by_id[pid]
        if (cand.get("family_role") or "") not in {"", FamilyRole.PRIMARY.value, None}:
            if cand.get("family_role") != FamilyRole.PRIMARY.value:
                continue
        if (cand.get("eligibility_status") or "") not in _ELIGIBLE:
            continue
        if (cand.get("brand_status") or "") in {"branded", "ambiguous_brand"}:
            continue
        lane = cand.get("strategic_lane") or fam["strategic_lane"] or StrategicLane.STRATEGIC_GAP.value
        enriched = dict(cand)
        enriched["strategic_lane"] = lane
        primaries.append(enriched)

    by_lane: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for cand in primaries:
        by_lane[cand["strategic_lane"]].append(cand)

    for lane, rows in by_lane.items():
        rows.sort(key=lambda c: _preliminary_rank_key(c, selection_policy), reverse=True)

    slots = _allocate_lane_slots(
        selection_policy.lane_quotas,
        pool_limit,
        selection_policy.selected_limit,
    )
    # Ensure lanes that appear in data but not quotas get residual consideration later.
    selected: List[Dict[str, Any]] = []
    lane_selected_counts: Dict[str, int] = defaultdict(int)
    used_families = set()

    for lane in _LANE_ORDER:
        want = slots.get(lane, 0)
        for cand in by_lane.get(lane, []):
            if lane_selected_counts[lane] >= want:
                break
            fam_id = cand.get("family_id")
            if fam_id and fam_id in used_families:
                continue
            selected.append(cand)
            lane_selected_counts[lane] += 1
            if fam_id:
                used_families.add(fam_id)

    # Fill remaining slots from leftover primaries by global preliminary score.
    if len(selected) < pool_limit:
        leftovers = []
        selected_ids = {c["candidate_id"] for c in selected}
        for lane, rows in by_lane.items():
            for cand in rows:
                if cand["candidate_id"] in selected_ids:
                    continue
                fam_id = cand.get("family_id")
                if fam_id and fam_id in used_families:
                    continue
                leftovers.append(cand)
        leftovers.sort(key=lambda c: _preliminary_rank_key(c, selection_policy), reverse=True)
        for cand in leftovers:
            if len(selected) >= pool_limit:
                break
            # Respect lane caps from policy when filling residuals.
            lane = cand["strategic_lane"]
            cap = selection_policy.lane_caps.get(lane)
            if cap is not None and lane_selected_counts[lane] >= cap:
                continue
            selected.append(cand)
            lane_selected_counts[lane] += 1
            fam_id = cand.get("family_id")
            if fam_id:
                used_families.add(fam_id)

    selected = selected[:pool_limit]
    for rank, cand in enumerate(selected, start=1):
        # Persist preliminary v2 components (Serper still missing).
        breakdown = score_candidate_v2(
            cand,
            score_weights=selection_policy.score_weights,
            penalties=selection_policy.penalties,
            min_impressions=selection_policy.min_impressions,
            require_serper=False,
        )
        fields = score_v2_update_fields(breakdown)
        fields.update(
            {
                "serp_preselected": 1,
                "serp_preselect_rank": rank,
                "selection_rank_within_lane": None,
                "updated_at": now,
            }
        )
        store.update_keyword_candidate(cand["candidate_id"], fields)

    report = {
        "build_id": build_id,
        "methodology_version": SELECTION_METHODOLOGY_V2,
        "policy_fingerprint": selection_policy.fingerprint(),
        "requested_limit": pool_limit,
        "preselected_count": len(selected),
        "eligible_primary_count": len(primaries),
        "lane_slots": slots,
        "lane_selected_counts": dict(lane_selected_counts),
        "candidate_ids": [c["candidate_id"] for c in selected],
        "note": (
            "Serper validation is NOT run by preselect-serp. "
            "Run catalogue validate-serp --pool preselected explicitly."
        ),
        "preselected_at": now,
    }
    store.update_catalogue_build(build_id, {"preselection_report_json": report})
    return report
