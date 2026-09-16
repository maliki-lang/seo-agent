"""Old catalogue (candidate-v0.1) comparison against evidence-derived builds."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..catalogs import load_keyword_catalog, load_question_catalog
from ..config import TrackingConfig
from ..enums import CandidateDecision
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import natural_key, normalize_catalogue_query, utc_now_iso
from . import PROVISIONAL_CATALOGUE_VERSION


def compare_with_provisional(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    keyword_build_id: str,
    question_build_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (keyword_build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {keyword_build_id}")

    store.execute("DELETE FROM catalogue_comparisons WHERE build_id = ?", (keyword_build_id,))
    now = utc_now_iso()
    selected = {
        normalize_catalogue_query(r["normalized_keyword"]): dict(r)
        for r in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (keyword_build_id, CandidateDecision.SELECTED.value),
        )
    }
    all_candidates = {
        normalize_catalogue_query(r["normalized_keyword"]): dict(r)
        for r in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ?",
            (keyword_build_id,),
        )
    }

    rows: List[Dict[str, Any]] = []
    counts = {"retained": 0, "modified": 0, "replaced": 0, "rejected": 0, "deferred": 0}

    for item in load_keyword_catalog(config):
        norm = normalize_catalogue_query(item.keyword)
        matched = selected.get(norm) or all_candidates.get(norm)
        if matched and matched["decision"] == CandidateDecision.SELECTED.value:
            old_page = (item.target_page or "").rstrip("/")
            new_page = (
                matched.get("reviewed_target_page")
                or matched.get("proposed_target_page")
                or matched.get("primary_observed_page")
                or ""
            ).rstrip("/")
            if old_page == new_page or not old_page:
                decision = "retained"
                reason = "exact_normalized_match_in_selected_shortlist"
            else:
                decision = "modified"
                reason = "same_keyword_selected_with_updated_target_page"
        elif matched and matched["decision"] == CandidateDecision.REJECTED.value:
            decision = "rejected"
            reason = matched.get("decision_reason") or "matched_candidate_rejected"
        elif matched and matched["decision"] == CandidateDecision.DEFERRED.value:
            decision = "deferred"
            reason = matched.get("decision_reason") or "matched_candidate_deferred"
        elif matched:
            decision = "deferred"
            reason = f"matched_candidate_decision_{matched['decision']}"
        else:
            # No GSC-derived candidate — cannot keep as production v1 without evidence.
            decision = "replaced"
            reason = "no_gsc_derived_candidate; provisional item not evidenced for v1"
        counts[decision] += 1
        rows.append(
            {
                "comparison_id": natural_key([keyword_build_id, "keyword", item.keyword_id]),
                "build_id": keyword_build_id,
                "provisional_type": "keyword",
                "provisional_id": item.keyword_id,
                "provisional_text": item.keyword,
                "provisional_cluster": item.cluster,
                "provisional_target_page": item.target_page,
                "decision": decision,
                "decision_reason": reason,
                "matched_candidate_id": matched["candidate_id"] if matched else None,
                "matched_question_id": None,
                "source_references_json": [matched["candidate_id"]] if matched else [],
                "created_at": now,
            }
        )

    q_build = question_build_id
    if not q_build:
        children = store.fetchall(
            """
            SELECT build_id FROM catalogue_builds
            WHERE parent_build_id = ? AND build_type = 'ai_question'
            ORDER BY created_at DESC LIMIT 1
            """,
            (keyword_build_id,),
        )
        q_build = children[0]["build_id"] if children else None

    if q_build:
        selected_q = {
            normalize_catalogue_query(r["question"]): dict(r)
            for r in store.fetchall(
                "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
                (q_build, CandidateDecision.SELECTED.value),
            )
        }
        for item in load_question_catalog(config):
            norm = normalize_catalogue_query(item.question)
            matched_q = selected_q.get(norm)
            if matched_q:
                decision = "retained"
                reason = "exact_normalized_question_in_selected_set"
            else:
                decision = "replaced"
                reason = "provisional_question_replaced_by_evidence_derived_set"
            counts[decision] = counts.get(decision, 0) + 1
            rows.append(
                {
                    "comparison_id": natural_key([keyword_build_id, "ai_question", item.question_id]),
                    "build_id": keyword_build_id,
                    "provisional_type": "ai_question",
                    "provisional_id": item.question_id,
                    "provisional_text": item.question,
                    "provisional_cluster": item.cluster,
                    "provisional_target_page": item.target_page,
                    "decision": decision,
                    "decision_reason": reason,
                    "matched_candidate_id": None,
                    "matched_question_id": matched_q["question_candidate_id"] if matched_q else None,
                    "source_references_json": [matched_q["question_candidate_id"]] if matched_q else [],
                    "created_at": now,
                }
            )

    store.insert_catalogue_comparisons(rows)
    return {
        "build_id": keyword_build_id,
        "question_build_id": q_build,
        "provisional_version": PROVISIONAL_CATALOGUE_VERSION,
        "counts": counts,
        "rows": len(rows),
    }
