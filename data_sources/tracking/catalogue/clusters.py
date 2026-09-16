"""Deterministic catalogue cluster derivation (Phase 8)."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import canonical_page_key, infer_page_type, natural_key, utc_now_iso

COMMERCIAL_PAGE_TYPES = frozenset({"collection", "product"})
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "for",
        "to",
        "of",
        "in",
        "and",
        "or",
        "with",
        "on",
        "at",
        "singapore",
        "best",
        "most",
        "how",
        "what",
        "which",
        "do",
        "i",
        "my",
    }
)


def _intent_for_page(page_type: str, keyword: str) -> str:
    text = (keyword or "").lower()
    if any(token in text for token in (" vs ", " versus ", "alternative", "compared")):
        return "comparison"
    if page_type in COMMERCIAL_PAGE_TYPES:
        if any(token in text for token in ("buy", "shop", "best", "recommend")):
            return "commercial_recommendation"
        return "commercial_selection"
    if any(token in text for token in ("how", "why", "what", "guide", "tips")):
        return "informational_education"
    return "informational_discovery"


def _topic_token(normalized_keyword: str) -> str:
    tokens = [t for t in normalized_keyword.split() if t and t not in STOPWORDS]
    # Prefer footwear nouns when present.
    for preferred in ("shoes", "flats", "loafers", "sandals", "sneakers", "boots", "heels"):
        if preferred in tokens:
            return preferred
    return tokens[0] if tokens else "general"


def _path_family(page: str) -> str:
    key = canonical_page_key(page)
    if not key or "/" not in key:
        return "other"
    path = "/" + key.split("/", 1)[1]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "home"
    if parts[0] in {"collections", "products", "blogs", "blog"}:
        return "/".join(parts[:2]) if len(parts) > 1 else parts[0]
    return parts[0]


def _cluster_key(candidate: Dict[str, Any]) -> Tuple[str, str, str]:
    page = candidate.get("reviewed_target_page") or candidate.get("proposed_target_page") or candidate.get(
        "primary_observed_page"
    ) or ""
    page_type = candidate.get("page_type") or infer_page_type(page)
    intent = _intent_for_page(page_type, candidate.get("normalized_keyword") or "")
    # Separate informational vs commercial even with shared tokens.
    channel = "commercial" if page_type in COMMERCIAL_PAGE_TYPES else "informational"
    family = _path_family(page)
    topic = _topic_token(candidate.get("normalized_keyword") or "")
    return channel, family or topic, intent


def _cluster_name(channel: str, family: str, intent: str) -> str:
    family_label = family.replace("/", " ").replace("-", " ").strip() or "general"
    return f"{channel}: {family_label} ({intent})"


def derive_clusters(
    store: TrackingStore,
    *,
    build_id: str,
    decisions: Optional[Sequence[str]] = None,
    reviewed_by: str = "cluster-builder",
) -> Dict[str, Any]:
    """Derive reviewable clusters from selected/pending keyword candidates."""
    store.migrate()
    builds = store.fetchall("SELECT build_id FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    wanted = list(decisions) if decisions else ["selected", "pending"]
    placeholders = ",".join("?" * len(wanted))
    candidates = [
        dict(row)
        for row in store.fetchall(
            f"""
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND decision IN ({placeholders})
            ORDER BY final_selection_score DESC, gsc_impressions DESC
            """,
            [build_id, *wanted],
        )
    ]
    if not candidates:
        raise DataQualityError(f"No candidates with decisions {wanted} for build {build_id}")

    # Replace prior draft clusters for this build (idempotent regenerate).
    store.execute("DELETE FROM catalogue_clusters WHERE build_id = ?", (build_id,))

    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for cand in candidates:
        groups[_cluster_key(cand)].append(cand)

    now = utc_now_iso()
    created = []
    for key, members in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        channel, family, intent = key
        member_ids = [m["candidate_id"] for m in members]
        pages = []
        for m in members:
            page = m.get("reviewed_target_page") or m.get("proposed_target_page") or m.get("primary_observed_page")
            if page:
                pages.append(page)
        # Primary target = strongest GSC evidence page among members.
        primary = sorted(
            members,
            key=lambda m: (int(m.get("gsc_impressions") or 0), int(m.get("gsc_clicks") or 0)),
            reverse=True,
        )[0]
        primary_page = (
            primary.get("reviewed_target_page")
            or primary.get("proposed_target_page")
            or primary.get("primary_observed_page")
            or ""
        )
        supporting = sorted({p for p in pages if canonical_page_key(p) != canonical_page_key(primary_page)})
        gsc_clicks = sum(int(m.get("gsc_clicks") or 0) for m in members)
        gsc_impr = sum(int(m.get("gsc_impressions") or 0) for m in members)
        ga4_sessions = None
        ga4_purchases = None
        ga4_revenue = None
        if any(m.get("ga4_organic_sessions") is not None for m in members):
            ga4_sessions = sum(int(m.get("ga4_organic_sessions") or 0) for m in members)
            ga4_purchases = sum(int(m.get("ga4_purchases") or 0) for m in members)
            ga4_revenue = format(
                sum(float(m.get("ga4_revenue") or 0) for m in members),
                "f",
            )
        cluster_id = natural_key([build_id, channel, family, intent])
        name = _cluster_name(channel, family, intent)
        rationale = (
            f"Grouped by {channel} intent, page family '{family}', and intent class '{intent}'. "
            f"Not word-overlap-only; informational/commercial channels are separated."
        )
        store.insert_catalogue_cluster(
            {
                "cluster_id": cluster_id,
                "build_id": build_id,
                "cluster_name": name,
                "primary_intent": intent,
                "primary_target_page": primary_page,
                "member_candidate_ids": member_ids,
                "supporting_pages_json": supporting,
                "gsc_clicks": gsc_clicks,
                "gsc_impressions": gsc_impr,
                "ga4_sessions": ga4_sessions,
                "ga4_purchases": ga4_purchases,
                "ga4_revenue": ga4_revenue,
                "rationale": rationale,
                "method": "deterministic_rules",
                "approval_status": "draft",
                "reviewed_by": reviewed_by,
                "reviewed_at": now,
                "created_at": now,
            }
        )
        for member_id in member_ids:
            store.update_keyword_candidate(member_id, {"cluster_id": cluster_id, "updated_at": now})
        created.append(
            {
                "cluster_id": cluster_id,
                "cluster_name": name,
                "intent": intent,
                "members": len(member_ids),
                "primary_target_page": primary_page,
            }
        )

    return {
        "build_id": build_id,
        "clusters_created": len(created),
        "candidates_clustered": len(candidates),
        "clusters": created,
        "note": "Clusters are draft until catalogue approve; not production-active yet.",
    }
