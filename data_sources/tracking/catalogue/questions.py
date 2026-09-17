"""Deterministic AI-question derivation with GSC/keyword lineage (Phase 8)."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import TrackingConfig
from ..enums import (
    CandidateDecision,
    CatalogueBuildStatus,
    CatalogueBuildType,
    LlmReviewStage,
    PilotStatus,
    QuestionGateStatus,
    QuestionSourceType,
)
from ..exceptions import ConfigurationError, DataQualityError
from ..storage import TrackingStore
from ..transforms.brand_label import BrandClassifier
from ..transforms.normalize import natural_key, normalize_catalogue_query, sha256_hex, utc_now_iso
from . import METHODOLOGY_VERSION, PROVISIONAL_CATALOGUE_META
from .question_review import DEFAULT_QUESTION_FAMILY_TARGETS

QUESTION_PROMPT_VERSION = "ai_question_templates_v1"

# Phase 14 decision-family mix (sum = 20). Legacy aliases map into these buckets.
DEFAULT_INTENT_MIX: Tuple[Tuple[str, int], ...] = DEFAULT_QUESTION_FAMILY_TARGETS

# Soft comfort framing; avoid cure/treat/FDA absolutes for YMYL adjacency.
_YMYL_BLOCKLIST = ("cure", "cures", "treat", "treats", "fda-approved", "diagnose", "heal")


def _topic_phrase(normalized_keyword: str) -> str:
    text = normalized_keyword.replace(" singapore", "").strip()
    return text or normalized_keyword


def _render_question(intent: str, keyword: str, page_type: str) -> Tuple[str, str]:
    topic = _topic_phrase(keyword)
    if intent == "recommendation":
        return (
            f"What are the most comfortable {topic} options for everyday wear in Singapore?",
            "context_expansion",
        )
    if intent == "problem_situation":
        if page_type == "article":
            return (
                f"Which shoes feel better for long days on your feet when dealing with {topic}?",
                "context_expansion",
            )
        return (
            f"What should I look for in shoes if I need more comfort for {topic} at work?",
            "direct_rewrite",
        )
    if intent == "product_selection":
        return (
            f"How do I choose {topic} that stay comfortable in Singapore's heat and humidity?",
            "context_expansion",
        )
    if intent == "feature_education":
        return (
            f"What cushioning and fit features matter most when shopping for {topic}?",
            "merged_queries",
        )
    if intent == "comparison":
        return (
            f"How do cushioned everyday shoes compare with {topic} for all-day comfort?",
            "comparison_expansion",
        )
    if intent == "work_lifestyle":
        return (
            f"What {topic} work for long office days and after-work walks in Singapore?",
            "context_expansion",
        )
    return (f"What should I know before buying {topic} in Singapore?", "direct_rewrite")


def _is_safe_question(text: str) -> bool:
    lowered = text.lower()
    return not any(token in lowered for token in _YMYL_BLOCKLIST)


def build_ai_questions(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    keyword_build_id: str,
    count: int = 20,
    status: str = CatalogueBuildStatus.DRAFT.value,
    created_by: str = "ai-question-builder",
    intent_mix: Optional[Sequence[Tuple[str, int]]] = None,
) -> Dict[str, Any]:
    store.migrate()
    if count <= 0:
        raise ConfigurationError("count must be positive")
    if status not in {s.value for s in CatalogueBuildStatus}:
        raise ConfigurationError(f"Invalid status: {status}")

    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (keyword_build_id,))
    if not builds:
        raise DataQualityError(f"Unknown keyword build_id {keyword_build_id}")

    clusters = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM catalogue_clusters WHERE build_id = ? ORDER BY gsc_impressions DESC",
            (keyword_build_id,),
        )
    ]
    if not clusters:
        raise DataQualityError(
            "No clusters found. Run catalogue derive-clusters before build-ai-questions."
        )

    selected = [
        dict(r)
        for r in store.fetchall(
            """
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND decision = ?
              AND cluster_id IS NOT NULL AND cluster_id != ''
            ORDER BY final_selection_score DESC, gsc_impressions DESC
            """,
            (keyword_build_id, CandidateDecision.SELECTED.value),
        )
    ]
    if not selected:
        raise DataQualityError("No selected keyword candidates with cluster membership")

    classifier = BrandClassifier.from_config(config)
    mix = list(intent_mix or DEFAULT_INTENT_MIX)
    # Scale mix to requested count while preserving proportions when count != 20.
    mix_total = sum(n for _i, n in mix) or 1
    planned: List[str] = []
    for intent, n in mix:
        planned.extend([intent] * max(1, round(n * count / mix_total)))
    while len(planned) > count:
        planned.pop()
    while len(planned) < count:
        planned.append(mix[len(planned) % len(mix)][0])

    now = utc_now_iso()
    build_id = str(uuid.uuid4())
    fingerprint = sha256_hex(
        "|".join(
            [
                keyword_build_id,
                str(count),
                QUESTION_PROMPT_VERSION,
                ",".join(c["candidate_id"] for c in selected[:20]),
            ]
        )
    )
    store.insert_catalogue_build(
        {
            "build_id": build_id,
            "build_type": CatalogueBuildType.AI_QUESTION.value,
            "status": status,
            "source_window_start": builds[0]["source_window_start"],
            "source_window_end": builds[0]["source_window_end"],
            "gsc_source_run_ids": [],
            "ga4_source_run_ids": [],
            "serper_source_run_ids": [],
            "source_fingerprint": fingerprint,
            "methodology_version": f"{METHODOLOGY_VERSION}+{QUESTION_PROMPT_VERSION}",
            "created_by": created_by,
            "created_at": now,
            "notes": f"Derived from keyword build {keyword_build_id}. Provisional ref: {PROVISIONAL_CATALOGUE_META}",
            "funnel_json": {},
            "parent_build_id": keyword_build_id,
        }
    )

    cluster_by_id = {c["cluster_id"]: c for c in clusters}
    questions_out: List[Dict[str, Any]] = []
    sources_out: List[Dict[str, Any]] = []
    rejected = 0
    used_texts = set()

    for idx, intent in enumerate(planned):
        candidate = selected[idx % len(selected)]
        cluster = cluster_by_id.get(candidate["cluster_id"])
        if not cluster:
            rejected += 1
            continue
        page = (
            candidate.get("reviewed_target_page")
            or candidate.get("proposed_target_page")
            or candidate.get("primary_observed_page")
            or cluster.get("primary_target_page")
            or ""
        )
        page_type = candidate.get("page_type") or "other"
        question, method = _render_question(intent, candidate["normalized_keyword"], page_type)
        if not _is_safe_question(question) or classifier.is_brand(question):
            # Deterministic fallback without brand/YMYL risk.
            question = (
                f"How can I find comfortable everyday shoes in Singapore related to "
                f"{_topic_phrase(candidate['normalized_keyword'])}?"
            )
            method = "context_expansion"
        if classifier.is_brand(question) or not _is_safe_question(question):
            rejected += 1
            continue
        norm = normalize_catalogue_query(question)
        if norm in used_texts:
            question = f"{question.rstrip('?')} for daily walking?"
            norm = normalize_catalogue_query(question)
        if norm in used_texts:
            rejected += 1
            continue
        used_texts.add(norm)

        q_id = natural_key([build_id, norm])
        gsc_sources = store.fetchall(
            """
            SELECT * FROM keyword_candidate_sources
            WHERE candidate_id = ?
            ORDER BY impressions DESC
            """,
            (candidate["candidate_id"],),
        )
        if not gsc_sources:
            rejected += 1
            continue

        evidence_refs = [f"candidate:{candidate['candidate_id']}", f"cluster:{cluster['cluster_id']}"]
        evidence_refs.extend(f"gsc:{src['gsc_natural_key']}" for src in gsc_sources[:3])
        # Template wording is assistance only — labelled synthetic_draft until validated.
        questions_out.append(
            {
                "question_candidate_id": q_id,
                "build_id": build_id,
                "question": question,
                "cluster_id": cluster["cluster_id"],
                "intent": intent,
                "family_bucket": intent,
                "proposed_target_page": page,
                "transformation_method": method,
                "source_keyword_ids": [],
                "source_candidate_ids": [candidate["candidate_id"]],
                "source_type": QuestionSourceType.SYNTHETIC_DRAFT.value,
                "source_reference": f"template:{QUESTION_PROMPT_VERSION}:{intent}",
                "source_evidence_refs_json": evidence_refs,
                "persona": "singapore_comfort_footwear_shopper",
                "situation": intent,
                "decision_to_make": "choose_comfortable_footwear",
                "constraints_json": ["non_branded", "singapore_context"],
                "expected_answer_elements_json": [
                    "comfort and fit considerations",
                    "use-case or situation fit",
                    "practical shopping criteria",
                ],
                "naturalness_status": QuestionGateStatus.PENDING.value,
                "distinctness_status": QuestionGateStatus.PENDING.value,
                "safety_status": (
                    QuestionGateStatus.PASSED.value
                    if _is_safe_question(question)
                    else QuestionGateStatus.FAILED.value
                ),
                "pilot_status": PilotStatus.NOT_RUN.value,
                "pilot_results_json": {},
                "review_stage": LlmReviewStage.EVIDENCE_READY.value,
                "human_validated_hypothesis": 0,
                "decision": CandidateDecision.SELECTED.value
                if len(questions_out) < count
                else CandidateDecision.PENDING.value,
                "decision_reason": "deterministic_template_shortlist_synthetic_draft",
                "created_at": now,
                "updated_at": now,
            }
        )
        for src in gsc_sources:
            sources_out.append(
                {
                    "question_source_id": natural_key([q_id, src["gsc_natural_key"]]),
                    "question_candidate_id": q_id,
                    "keyword_id": None,
                    "candidate_id": candidate["candidate_id"],
                    "gsc_natural_key": src["gsc_natural_key"],
                    "gsc_run_id": src["gsc_run_id"],
                    "raw_gsc_query": src["raw_query"],
                    "relationship": "direct",
                    "created_at": now,
                }
            )
        if len(questions_out) >= count:
            break

    # Ensure selected count equals requested when enough candidates existed.
    for i, row in enumerate(questions_out):
        if i < count:
            row["decision"] = CandidateDecision.SELECTED.value
        else:
            row["decision"] = CandidateDecision.PENDING.value

    store.insert_ai_question_candidates(questions_out)
    store.insert_ai_question_sources(sources_out)

    selected_count = sum(1 for q in questions_out if q["decision"] == CandidateDecision.SELECTED.value)
    store.update_catalogue_build(
        build_id,
        {
            "funnel_json": {
                "requested": count,
                "generated": len(questions_out),
                "selected": selected_count,
                "rejected_during_generation": rejected,
                "intent_mix": dict(mix),
                "prompt_version": QUESTION_PROMPT_VERSION,
            }
        },
    )
    return {
        "build_id": build_id,
        "parent_build_id": keyword_build_id,
        "status": status,
        "prompt_version": QUESTION_PROMPT_VERSION,
        "inserted": {"questions": len(questions_out), "sources": len(sources_out)},
        "selected": selected_count,
        "rejected_during_generation": rejected,
        "note": "Questions are draft/review candidates; approval is a separate human action.",
    }
