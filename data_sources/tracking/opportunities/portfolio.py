"""Deduplicate and construct constrained action portfolio (up to ten)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..enums import OpportunityActionType, OpportunityCategory, OpportunitySourceType
from .impact import priority_from_inputs
from .targets import canonicalize_target_url, validate_target_for_portfolio

EXISTING_PAGE_ACTIONS = {
    OpportunityActionType.TITLE_META_REWRITE.value,
    OpportunityActionType.INTERNAL_LINKING.value,
    OpportunityActionType.ANSWER_SECTION.value,
    OpportunityActionType.CONTENT_EXPANSION.value,
    OpportunityActionType.PRODUCT_MAPPING.value,
    OpportunityActionType.CONTENT_CONSOLIDATION.value,
    OpportunityActionType.TECHNICAL_FIX.value,
    OpportunityActionType.GEO_EVIDENCE_UPGRADE.value,
    OpportunityActionType.MANUAL_INVESTIGATION.value,
}

# Actions that can coexist on the same page without merging.
NON_OVERLAPPING_ACTIONS = {
    (
        OpportunityActionType.TITLE_META_REWRITE.value,
        OpportunityActionType.TECHNICAL_FIX.value,
    ),
    (
        OpportunityActionType.TECHNICAL_FIX.value,
        OpportunityActionType.TITLE_META_REWRITE.value,
    ),
    (
        OpportunityActionType.GEO_EVIDENCE_UPGRADE.value,
        OpportunityActionType.TITLE_META_REWRITE.value,
    ),
    (
        OpportunityActionType.TITLE_META_REWRITE.value,
        OpportunityActionType.GEO_EVIDENCE_UPGRADE.value,
    ),
}

SOURCE_CONCENTRATION_WARN = 0.40


def _priority(row: Dict[str, Any]) -> float:
    result = priority_from_inputs(
        estimated_incremental_clicks=float(row.get("expected_incremental_clicks") or 0),
        confidence_value=float(row.get("confidence_value") or 0),
        estimated_cost=row.get("estimated_cost"),
        effort_value=row.get("effort_value"),
    )
    row["priority_score"] = result["priority_score"]
    row["priority_inputs_json"] = result
    return float(result["priority_score"])


def _merge_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    page = canonicalize_target_url(row.get("target_page") or "") or (row.get("target_page") or "")
    page = page.strip().lower()
    action = row.get("action_type") or ""
    family = row.get("family_id") or row.get("cluster_id") or ""
    return (page, action, family)


def merge_overlapping_actions(candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge same page+action(+family) supported by multiple benchmarks into one opportunity."""
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        item = dict(row)
        if item.get("target_page"):
            item["target_page"] = canonicalize_target_url(item["target_page"]) or item["target_page"]
        groups[_merge_key(item)].append(item)

    merged: List[Dict[str, Any]] = []
    merge_events = []
    for key, items in groups.items():
        if len(items) == 1:
            item = items[0]
            item["merge_group_id"] = None
            merged.append(item)
            continue
        primary = max(items, key=_priority)
        benchmarks = []
        refs = []
        for item in items:
            benchmarks.extend(item.get("benchmark_ids_json") or [])
            refs.extend(item.get("source_row_references_json") or [])
        primary = dict(primary)
        primary["benchmark_ids_json"] = sorted(set(str(b) for b in benchmarks if b))
        primary["source_row_references_json"] = refs
        # Preserve max raw gain among members rather than summing (avoid double count).
        primary["expected_incremental_clicks"] = max(
            float(i.get("expected_incremental_clicks") or 0) for i in items
        )
        primary["merge_group_id"] = f"{key[0]}|{key[1]}|{key[2]}"
        evidence = dict(primary.get("supporting_evidence_json") or {})
        evidence["merged_from_count"] = len(items)
        evidence["merged_benchmarks"] = primary["benchmark_ids_json"]
        primary["supporting_evidence_json"] = evidence
        _priority(primary)
        merged.append(primary)
        merge_events.append(
            {
                "merge_group_id": primary["merge_group_id"],
                "members": len(items),
                "action_type": key[1],
                "target_page": key[0],
            }
        )
    return {"merged": merged, "merge_events": merge_events, "input_count": len(candidates)}


def select_top_ten(
    candidates: Sequence[Dict[str, Any]],
    *,
    limit: int = 10,
    min_existing_page: int = 6,
    max_new_page: int = 2,
    max_per_cluster: int = 2,
    max_per_page: int = 1,
    minimum_incremental_clicks: float = 1.0,
    minimum_priority_score: float = 0.01,
) -> Dict[str, Any]:
    """
    Constrained portfolio selection.
    Returns fewer than `limit` when fewer candidates pass absolute eligibility gates.
    GEO-only opportunities cannot displace materially stronger traffic opportunities.
    """
    scored = []
    pre_rejected: List[Dict[str, Any]] = []
    for row in candidates:
        item = dict(row)
        if item.get("target_page"):
            item["target_page"] = canonicalize_target_url(item["target_page"]) or item["target_page"]
        _priority(item)
        ok, reason = validate_target_for_portfolio(
            item,
            minimum_incremental_clicks=minimum_incremental_clicks,
            minimum_priority_score=minimum_priority_score,
        )
        if not ok:
            pre_rejected.append(
                {
                    "reason": reason,
                    "problem": item.get("problem"),
                    "action_type": item.get("action_type"),
                    "source_type": item.get("source_type"),
                }
            )
            continue
        scored.append(item)

    traffic = [
        r
        for r in scored
        if r.get("category") == OpportunityCategory.SEO.value
        and float(r.get("expected_incremental_clicks") or 0) >= float(minimum_incremental_clicks)
    ]
    geo = [r for r in scored if r.get("category") == OpportunityCategory.GEO.value]
    other = [r for r in scored if r not in traffic and r not in geo]

    traffic.sort(key=lambda r: (-float(r["priority_score"]), r.get("problem") or ""))
    geo.sort(key=lambda r: (-float(r["priority_score"]), r.get("problem") or ""))
    other.sort(key=lambda r: (-float(r["priority_score"]), r.get("problem") or ""))

    selected: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = list(pre_rejected)
    cluster_counts: Dict[str, int] = defaultdict(int)
    page_actions: Dict[str, List[str]] = defaultdict(list)
    new_page_count = 0

    def _can_add(row: Dict[str, Any]) -> Optional[str]:
        nonlocal new_page_count
        cluster = row.get("cluster_id") or ""
        page = (row.get("target_page") or "").strip().lower()
        action = row.get("action_type") or ""
        if cluster and cluster_counts[cluster] >= max_per_cluster:
            return "cluster_cap"
        if action == OpportunityActionType.NEW_PAGE.value:
            if new_page_count >= max_new_page:
                return "new_page_cap"
        if page:
            existing_actions = page_actions[page]
            if existing_actions:
                if len(existing_actions) >= max_per_page:
                    pair = (existing_actions[0], action)
                    if pair not in NON_OVERLAPPING_ACTIONS:
                        return "page_action_overlap"
        return None

    def _add(row: Dict[str, Any]) -> bool:
        nonlocal new_page_count
        reason = _can_add(row)
        if reason:
            rejected.append(
                {"reason": reason, "problem": row.get("problem"), "action_type": row.get("action_type")}
            )
            return False
        selected.append(row)
        cluster = row.get("cluster_id") or ""
        if cluster:
            cluster_counts[cluster] += 1
        page = (row.get("target_page") or "").strip().lower()
        if page:
            page_actions[page].append(row.get("action_type") or "")
        if row.get("action_type") == OpportunityActionType.NEW_PAGE.value:
            new_page_count += 1
        return True

    # Prefer traffic opportunities first — do not force-fill with zero-impact items.
    for row in traffic:
        if len(selected) >= limit:
            break
        _add(row)

    for row in other:
        if len(selected) >= limit:
            break
        _add(row)

    strongest_traffic = float(traffic[0]["priority_score"]) if traffic else 0.0
    for row in geo:
        if len(selected) >= limit:
            break
        if strongest_traffic and float(row["priority_score"]) < strongest_traffic * 0.5 and len(
            [s for s in selected if float(s.get("expected_incremental_clicks") or 0) > 0]
        ) >= min(limit, max(1, min_existing_page)):
            rejected.append(
                {
                    "reason": "geo_cannot_displace_stronger_traffic",
                    "problem": row.get("problem"),
                    "action_type": row.get("action_type"),
                }
            )
            continue
        _add(row)

    existing_count = sum(
        1
        for s in selected
        if s.get("action_type") in EXISTING_PAGE_ACTIONS
        and s.get("action_type") != OpportunityActionType.NEW_PAGE.value
    )
    coverage_warning = None
    if existing_count < min_existing_page and len(selected) >= limit:
        coverage_warning = {
            "existing_page_selected": existing_count,
            "min_existing_page": min_existing_page,
            "note": "fewer existing-page opportunities than preferred minimum",
        }

    source_counts = Counter(s.get("source_type") or OpportunitySourceType.SOURCE_BLOCKED.value for s in selected)
    concentration_warning = None
    if selected:
        top_source, top_count = source_counts.most_common(1)[0]
        share = top_count / len(selected)
        if share > SOURCE_CONCENTRATION_WARN:
            concentration_warning = {
                "source_type": top_source,
                "share": round(share, 4),
                "count": top_count,
                "threshold": SOURCE_CONCENTRATION_WARN,
                "note": "one source type exceeds 40% of the selected portfolio",
            }

    for idx, row in enumerate(selected, start=1):
        row["portfolio_rank"] = idx

    return {
        "selected": selected,
        "rejected": rejected,
        "coverage_warning": coverage_warning,
        "concentration_warning": concentration_warning,
        "counts": {
            "input": len(candidates),
            "eligible_after_gates": len(scored),
            "selected": len(selected),
            "rejected": len(rejected),
            "existing_page": existing_count,
            "new_page": new_page_count,
            "geo": sum(1 for s in selected if s.get("category") == OpportunityCategory.GEO.value),
        },
    }
