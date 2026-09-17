"""Mandatory, non-authoritative LLM assessment orchestration (Phase 14)."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from ..config import TrackingConfig
from ..costs import CostLedger
from ..enums import CandidateDecision, LlmAssessmentType, LlmReviewStage, LlmValidationStatus, SemanticAuthority
from ..exceptions import ConfigurationError, CostLimitExceeded, DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import canonical_json, sha256_hex, utc_now_iso
from .client import DEFAULT_MODEL, LlmClient
from .cost import estimate_batch_cost, estimate_tokens
from .schemas import (
    ALLOWED_ACTIONABILITY,
    ALLOWED_BUSINESS_RELEVANCE,
    ALLOWED_SEARCH_INTENTS,
    prompt_version_for,
    validate_assessment_output,
)

SYSTEM_PROMPTS = {
    LlmAssessmentType.SEMANTIC_REVIEW.value: (
        "You assist SEO catalogue review. Use only supplied evidence. "
        "Never invent search volume, GSC/GA4 metrics, rankings, page URLs outside the "
        "allowlist, source references, or approval status. Return JSON only."
    ),
    LlmAssessmentType.POOL_SEMANTIC.value: (
        "You perform mid-funnel semantic assessment for a comfort-footwear catalogue. "
        "Decide intent, customer need, business relevance, family membership, whether this "
        "row is the family representative, actionability, and a target page ONLY from the "
        "supplied allowlist (or set no_suitable_target=true). "
        "Enums (exact strings only): "
        "search_intent="
        "navigational_brand|navigational_competitor|local_store|transactional_category|"
        "commercial_investigation|problem_solution|informational|campaign_event|ambiguous; "
        "business_relevance=relevant|irrelevant|location_only|pending_review; "
        "actionability=optimize_existing|consolidate_competing_pages|create_new_page|"
        "protect_existing|monitor_only|no_action; "
        "confidence=high|medium|low (never a number). "
        "assumptions, risk_flags, and semantic_duplicates must be JSON arrays of strings. "
        "is_family_representative and no_suitable_target must be booleans. "
        "Never invent measured metrics, evidence IDs, or pages outside the allowlist. "
        "Return JSON only."
    ),
    LlmAssessmentType.QUESTION_REWRITE.value: (
        "Rewrite into natural non-branded customer language using only supplied evidence. "
        "Do not invent demand metrics. Avoid medical cure/treat/FDA claims. Return JSON only."
    ),
    LlmAssessmentType.ANSWER_RUBRIC.value: (
        "Draft expected answer elements for an AI-question benchmark from supplied evidence only. "
        "Do not invent metrics or page URLs. Keep YMYL framing safe. Return JSON only."
    ),
    LlmAssessmentType.OPPORTUNITY_DIAGNOSIS.value: (
        "Diagnose a page/SERP gap from supplied evidence only. Propose an allowed action type. "
        "Never invent measured facts or evidence IDs. Return JSON only."
    ),
}


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _subject_rows(
    store: TrackingStore,
    *,
    build_id: str,
    assessment_type: str,
    scope: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if assessment_type in {
        LlmAssessmentType.SEMANTIC_REVIEW.value,
        LlmAssessmentType.POOL_SEMANTIC.value,
    }:
        if scope == "reviewed_shortlist":
            rows = store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ? AND decision = ?
                ORDER BY (portfolio_slot IS NULL), portfolio_slot ASC,
                         selection_score_v2 DESC, gsc_impressions DESC
                """,
                (build_id, CandidateDecision.SELECTED.value),
            )
        elif scope == "alternates":
            rows = store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ? AND alternate_rank IS NOT NULL
                ORDER BY alternate_rank ASC
                """,
                (build_id,),
            )
        elif scope == "eligible_pool":
            rows = store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ?
                  AND eligibility_status IN ('eligible', 'eligible_with_review', 'pending_classification')
                ORDER BY gsc_impressions DESC, gsc_clicks DESC, normalized_keyword ASC
                """,
                (build_id,),
            )
        elif scope == "eligible_nonbrand":
            rows = store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ?
                  AND eligibility_status IN ('eligible', 'eligible_with_review', 'pending_classification')
                  AND COALESCE(brand_status, 'non_branded') = 'non_branded'
                  AND COALESCE(routing_bucket, '') NOT IN ('branded_benchmark', 'irrelevant')
                ORDER BY gsc_impressions DESC, gsc_clicks DESC, normalized_keyword ASC
                """,
                (build_id,),
            )
        elif scope == "preselected_pool":
            rows = store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ?
                  AND COALESCE(serp_preselected, 0) = 1
                ORDER BY COALESCE(serp_preselect_rank, 9999) ASC, gsc_impressions DESC
                """,
                (build_id,),
            )
        else:
            raise ConfigurationError(
                f"Unsupported scope for {assessment_type}: {scope}. "
                "Use reviewed_shortlist|alternates|eligible_pool|eligible_nonbrand|preselected_pool"
            )
        return [dict(r) for r in rows[:limit]]

    if assessment_type in {
        LlmAssessmentType.QUESTION_REWRITE.value,
        LlmAssessmentType.ANSWER_RUBRIC.value,
    }:
        rows = store.fetchall(
            """
            SELECT * FROM ai_question_candidates
            WHERE build_id = ?
            ORDER BY created_at ASC, question_candidate_id ASC
            """,
            (build_id,),
        )
        if scope == "reviewed_shortlist":
            rows = [r for r in rows if r["decision"] == CandidateDecision.SELECTED.value]
        return [dict(r) for r in rows[:limit]]

    if assessment_type == LlmAssessmentType.OPPORTUNITY_DIAGNOSIS.value:
        rows = store.fetchall(
            """
            SELECT * FROM opportunities
            WHERE report_id = ? OR opportunity_id = ?
            ORDER BY COALESCE(portfolio_rank, 9999) ASC, created_at ASC
            """,
            (build_id, build_id),
        )
        return [dict(r) for r in rows[:limit]]

    raise ConfigurationError(f"Unsupported assessment_type: {assessment_type}")


def _allowlisted_pages(row: Dict[str, Any]) -> List[str]:
    pages = []
    for key in (
        "reviewed_target_page",
        "proposed_target_page",
        "primary_observed_page",
        "serper_ranking_url",
        "ga4_match_page",
    ):
        value = (row.get(key) or "").strip()
        if value and value not in pages:
            pages.append(value)
    # Supporting observed pages JSON if present.
    raw = row.get("observed_pages_json")
    if isinstance(raw, str) and raw:
        try:
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    url = item if isinstance(item, str) else (item or {}).get("page") or (item or {}).get("url")
                    if url and url not in pages:
                        pages.append(str(url))
        except (TypeError, json.JSONDecodeError):
            pass
    return pages


def _build_packet(row: Dict[str, Any], assessment_type: str) -> Dict[str, Any]:
    if assessment_type in {
        LlmAssessmentType.SEMANTIC_REVIEW.value,
        LlmAssessmentType.POOL_SEMANTIC.value,
    }:
        pages = _allowlisted_pages(row)
        refs = [
            f"candidate:{row['candidate_id']}",
            f"family:{row.get('family_id') or ''}",
            f"cluster:{row.get('cluster_id') or ''}",
        ]
        for page in pages:
            refs.append(f"page:{page}")
        packet = {
            "subject_id": row["candidate_id"],
            "subject_type": "keyword_candidate",
            "keyword": row.get("canonical_keyword") or row.get("normalized_keyword"),
            "deterministic_hints": {
                "search_intent": row.get("search_intent"),
                "strategic_lane": row.get("strategic_lane"),
                "business_relevance_status": row.get("business_relevance_status"),
                "business_relevance_reason": row.get("business_relevance_reason"),
                "family_id": row.get("family_id"),
                "family_role": row.get("family_role"),
                "target_page_status": row.get("target_page_status"),
                "routing_bucket": row.get("routing_bucket"),
                "note": "Hints only — LLM owns semantic decisions when assessment is valid.",
            },
            "allowlisted_target_pages": pages,
            "evidence_refs": [r for r in refs if not r.endswith(":")],
            "supplied_metrics": {
                "gsc_clicks": row.get("gsc_clicks"),
                "gsc_impressions": row.get("gsc_impressions"),
                "gsc_weighted_ctr": row.get("gsc_weighted_ctr"),
                "gsc_weighted_position": row.get("gsc_weighted_position"),
                "ga4_organic_sessions": row.get("ga4_organic_sessions"),
                "serper_position": row.get("serper_position"),
            },
            "note": "Metrics above are supplied facts. Do not invent additional measured values.",
        }
        if assessment_type == LlmAssessmentType.POOL_SEMANTIC.value:
            packet["required_decisions"] = [
                "search_intent",
                "customer_need",
                "business_relevance",
                "family_key",
                "is_family_representative",
                "actionability",
                "recommended_target_page_or_no_suitable_target",
            ]
        return packet

    if assessment_type in {
        LlmAssessmentType.QUESTION_REWRITE.value,
        LlmAssessmentType.ANSWER_RUBRIC.value,
    }:
        page = row.get("proposed_target_page") or ""
        refs = [f"question:{row['question_candidate_id']}", f"cluster:{row.get('cluster_id') or ''}"]
        if page:
            refs.append(f"page:{page}")
        return {
            "subject_id": row["question_candidate_id"],
            "subject_type": "question_candidate",
            "question": row.get("question"),
            "intent": row.get("intent"),
            "source_type": row.get("source_type"),
            "allowlisted_target_pages": [p for p in [page] if p],
            "evidence_refs": [r for r in refs if not r.endswith(":")],
            "existing_expected_answer_elements": _load_json(
                row.get("expected_answer_elements_json"), []
            ),
        }

    # opportunity_diagnosis
    evidence = _load_json(row.get("supporting_evidence_json"), {})
    refs = _load_json(row.get("source_row_references_json"), [])
    page = row.get("target_page") or ""
    return {
        "subject_id": row.get("opportunity_id"),
        "subject_type": "opportunity",
        "problem": row.get("problem"),
        "category": row.get("category"),
        "proposed_action": row.get("proposed_action"),
        "allowlisted_target_pages": [p for p in [page] if p],
        "evidence_refs": [str(r) for r in refs],
        "supporting_evidence": evidence,
        "supplied_scores": {
            "impact_score": row.get("impact_score"),
            "priority_score": row.get("priority_score"),
            "confidence_label": row.get("confidence_label"),
        },
        "note": "Scores above are supplied facts. Do not invent additional measured values.",
    }


def estimate_assessment_cost(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    assessment_type: str,
    scope: str = "reviewed_shortlist",
    limit: int = 55,
    provider: str = "openai",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    if assessment_type not in {t.value for t in LlmAssessmentType}:
        raise ConfigurationError(f"Invalid assessment_type: {assessment_type}")
    subjects = _subject_rows(
        store, build_id=build_id, assessment_type=assessment_type, scope=scope, limit=limit
    )
    prompt_version = prompt_version_for(assessment_type)
    resolved_model = model or config.openai_visibility_model or DEFAULT_MODEL
    eligible = []
    blocked = []
    for row in subjects:
        packet = _build_packet(row, assessment_type)
        subject_type = packet["subject_type"]
        subject_id = packet["subject_id"]
        fingerprint = sha256_hex(canonical_json(packet))
        existing = store.fetchall(
            """
            SELECT assessment_id, validation_status FROM llm_assessments
            WHERE assessment_type = ? AND subject_type = ? AND subject_id = ?
              AND prompt_version = ? AND input_fingerprint = ?
            """,
            (assessment_type, subject_type, subject_id, prompt_version, fingerprint),
        )
        stage = row.get("llm_review_stage") or row.get("review_stage")
        if existing and existing[0]["validation_status"] == LlmValidationStatus.VALID.value:
            blocked.append(
                {
                    "subject_id": subject_id,
                    "reason": "identical_assessment_already_valid",
                    "assessment_id": existing[0]["assessment_id"],
                }
            )
            continue
        if stage == LlmReviewStage.APPROVED.value:
            blocked.append({"subject_id": subject_id, "reason": "already_human_approved"})
            continue
        eligible.append(
            {
                "subject_id": subject_id,
                "subject_type": subject_type,
                "input_tokens_estimate": estimate_tokens(canonical_json(packet)) + 120,
            }
        )
    cost = estimate_batch_cost(len(eligible))
    return {
        "dry_run": True,
        "build_id": build_id,
        "assessment_type": assessment_type,
        "scope": scope,
        "prompt_version": prompt_version,
        "provider": provider,
        "model": resolved_model,
        "subjects_eligible": len(eligible),
        "subjects_blocked": len(blocked),
        "eligible": eligible,
        "blocked": blocked,
        **cost,
        "note": "No provider call is made during dry-run.",
    }


def assess_subjects(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    assessment_type: str,
    scope: str = "reviewed_shortlist",
    limit: int = 55,
    dry_run: bool = False,
    provider: str = "openai",
    model: Optional[str] = None,
    client: Optional[LlmClient] = None,
    cost_ledger: Optional[CostLedger] = None,
) -> Dict[str, Any]:
    if dry_run:
        return estimate_assessment_cost(
            store,
            config,
            build_id=build_id,
            assessment_type=assessment_type,
            scope=scope,
            limit=limit,
            provider=provider,
            model=model,
        )

    store.migrate()
    prompt_version = prompt_version_for(assessment_type)
    llm = client or LlmClient(config, provider=provider, model=model)
    ledger = cost_ledger or CostLedger(config.daily_cost_cap_usd)
    subjects = _subject_rows(
        store, build_id=build_id, assessment_type=assessment_type, scope=scope, limit=limit
    )
    if not subjects:
        raise DataQualityError(f"No subjects found for build_id={build_id} scope={scope}")

    results: List[Dict[str, Any]] = []
    created = 0
    reused = 0
    invalid = 0
    blocked = 0

    for row in subjects:
        packet = _build_packet(row, assessment_type)
        subject_type = packet["subject_type"]
        subject_id = str(packet["subject_id"])
        fingerprint = sha256_hex(canonical_json(packet))
        existing = store.fetchall(
            """
            SELECT * FROM llm_assessments
            WHERE assessment_type = ? AND subject_type = ? AND subject_id = ?
              AND prompt_version = ? AND input_fingerprint = ?
            """,
            (assessment_type, subject_type, subject_id, prompt_version, fingerprint),
        )
        if existing and existing[0]["validation_status"] == LlmValidationStatus.VALID.value:
            reused += 1
            results.append(
                {
                    "subject_id": subject_id,
                    "assessment_id": existing[0]["assessment_id"],
                    "status": "reused_valid",
                }
            )
            continue

        # Mark awaiting before call so unavailable model leaves an auditable block.
        _set_review_stage(
            store,
            subject_type=subject_type,
            subject_id=subject_id,
            stage=LlmReviewStage.AWAITING_LLM_ASSESSMENT.value,
            assessment_id=None,
        )

        user_payload = {
            "assessment_type": assessment_type,
            "prompt_version": prompt_version,
            "evidence_packet": packet,
            "required_output_keys_hint": _output_hint(assessment_type),
        }
        if assessment_type == LlmAssessmentType.POOL_SEMANTIC.value:
            user_payload["allowed_enums"] = {
                "search_intent": sorted(ALLOWED_SEARCH_INTENTS),
                "business_relevance": sorted(ALLOWED_BUSINESS_RELEVANCE),
                "actionability": sorted(ALLOWED_ACTIONABILITY),
                "confidence": ["high", "medium", "low"],
            }
        try:
            completion = llm.complete(
                system=SYSTEM_PROMPTS[assessment_type],
                user=canonical_json(user_payload),
            )
        except Exception as exc:  # noqa: BLE001 — leave item blocked, continue others
            blocked += 1
            results.append(
                {
                    "subject_id": subject_id,
                    "status": LlmReviewStage.AWAITING_LLM_ASSESSMENT.value,
                    "error": str(exc),
                }
            )
            continue

        cost = Decimal(str(completion.get("cost_usd") or "0"))
        try:
            ledger.add(cost, source=f"llm:{assessment_type}")
        except CostLimitExceeded as exc:
            blocked += 1
            results.append(
                {
                    "subject_id": subject_id,
                    "status": LlmReviewStage.AWAITING_LLM_ASSESSMENT.value,
                    "error": str(exc),
                }
            )
            break

        ok, errors, normalized = validate_assessment_output(
            assessment_type,
            completion.get("output") or {},
            allowed_target_pages=packet.get("allowlisted_target_pages") or [],
            allowed_evidence_refs=packet.get("evidence_refs") or [],
        )
        assessment_id = str(uuid.uuid4())
        validation_status = (
            LlmValidationStatus.VALID.value if ok else LlmValidationStatus.INVALID.value
        )
        store.insert_llm_assessment(
            {
                "assessment_id": assessment_id,
                "assessment_type": assessment_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "build_id": build_id,
                "prompt_version": prompt_version,
                "provider": completion.get("provider") or llm.provider,
                "model": completion.get("model") or llm.model,
                "input_fingerprint": fingerprint,
                "input_evidence_refs_json": packet.get("evidence_refs") or [],
                "redacted_input_json": packet,
                "output_json": normalized if ok else (completion.get("output") or {}),
                "validation_status": validation_status,
                "validation_errors_json": errors,
                "cost_usd": float(cost),
                "latency_ms": completion.get("latency_ms"),
                "created_at": utc_now_iso(),
            }
        )
        created += 1
        if ok:
            stage = LlmReviewStage.ASSESSMENT_VALIDATED.value
            _apply_validated_side_effects(
                store,
                assessment_type=assessment_type,
                subject_type=subject_type,
                subject_id=subject_id,
                normalized=normalized,
                assessment_id=assessment_id,
            )
        else:
            stage = LlmReviewStage.LLM_OUTPUT_INVALID.value
            invalid += 1
            if (
                assessment_type == LlmAssessmentType.POOL_SEMANTIC.value
                and subject_type == "keyword_candidate"
            ):
                from ..catalogue.apply_semantics import mark_awaiting_or_invalid

                mark_awaiting_or_invalid(
                    store,
                    candidate_id=subject_id,
                    authority=SemanticAuthority.LLM_INVALID.value,
                    assessment_id=assessment_id,
                )
        _set_review_stage(
            store,
            subject_type=subject_type,
            subject_id=subject_id,
            stage=stage,
            assessment_id=assessment_id,
        )
        results.append(
            {
                "subject_id": subject_id,
                "assessment_id": assessment_id,
                "status": stage,
                "validation_status": validation_status,
                "validation_errors": errors,
            }
        )

    family_report = None
    if assessment_type == LlmAssessmentType.POOL_SEMANTIC.value and not dry_run:
        from ..catalogue.apply_semantics import apply_llm_family_regroup, mark_deterministic_fallback

        family_report = apply_llm_family_regroup(store, build_id=build_id)
        mark_deterministic_fallback(store, build_id=build_id)

    return {
        "dry_run": False,
        "build_id": build_id,
        "assessment_type": assessment_type,
        "scope": scope,
        "prompt_version": prompt_version,
        "provider": llm.provider,
        "model": llm.model,
        "created": created,
        "reused": reused,
        "invalid": invalid,
        "blocked": blocked,
        "spent_usd": float(ledger.spent),
        "family_regroup": family_report,
        "results": results,
        "note": (
            "Pool-semantic LLM owns intent/relevance/family/target decisions when valid; "
            "measured facts and portfolio quotas remain deterministic. Human approval still required."
        ),
    }


def _output_hint(assessment_type: str) -> Sequence[str]:
    if assessment_type == LlmAssessmentType.SEMANTIC_REVIEW.value:
        return [
            "customer_need",
            "intent_summary",
            "business_relevance_rationale",
            "possible_semantic_duplicates",
            "recommended_target_page",
            "confidence",
            "assumptions",
            "risk_flags",
        ]
    if assessment_type == LlmAssessmentType.POOL_SEMANTIC.value:
        return [
            "customer_need",
            "search_intent",
            "business_relevance",
            "business_relevance_rationale",
            "family_key",
            "is_family_representative",
            "semantic_duplicates",
            "actionability",
            "actionability_rationale",
            "recommended_target_page",
            "no_suitable_target",
            "confidence",
            "assumptions",
            "risk_flags",
        ]
    if assessment_type == LlmAssessmentType.QUESTION_REWRITE.value:
        return ["rewritten_question", "naturalness", "rationale", "risk_flags"]
    if assessment_type == LlmAssessmentType.ANSWER_RUBRIC.value:
        return ["expected_answer_elements", "ymyl_notes", "risk_flags"]
    return [
        "problem",
        "diagnosis",
        "proposed_action_type",
        "proposed_actions",
        "evidence_refs",
        "assumptions",
        "risk_flags",
        "confidence",
    ]


def _set_review_stage(
    store: TrackingStore,
    *,
    subject_type: str,
    subject_id: str,
    stage: str,
    assessment_id: Optional[str],
) -> None:
    now = utc_now_iso()
    if subject_type == "keyword_candidate":
        fields: Dict[str, Any] = {"llm_review_stage": stage, "updated_at": now}
        if assessment_id:
            fields["llm_assessment_id"] = assessment_id
        if stage == LlmReviewStage.AWAITING_LLM_ASSESSMENT.value:
            fields["semantic_authority"] = SemanticAuthority.AWAITING_LLM.value
        store.update_keyword_candidate(subject_id, fields)
    elif subject_type == "question_candidate":
        fields = {"review_stage": stage, "updated_at": now}
        if assessment_id:
            fields["llm_assessment_id"] = assessment_id
        store.update_ai_question_candidate(subject_id, fields)


def _apply_validated_side_effects(
    store: TrackingStore,
    *,
    assessment_type: str,
    subject_type: str,
    subject_id: str,
    normalized: Dict[str, Any],
    assessment_id: str,
) -> None:
    """Attach validated interpretive fields only — never overwrite measured metrics."""
    now = utc_now_iso()
    if subject_type == "question_candidate":
        fields: Dict[str, Any] = {
            "llm_assessment_id": assessment_id,
            "updated_at": now,
        }
        if assessment_type == LlmAssessmentType.QUESTION_REWRITE.value:
            rewritten = normalized.get("rewritten_question")
            if rewritten:
                fields["question"] = rewritten
            natural = normalized.get("naturalness")
            if natural in {"passed", "failed", "pending"}:
                fields["naturalness_status"] = natural
            if normalized.get("risk_flags"):
                fields["safety_status"] = "failed"
            elif natural == "passed":
                fields["safety_status"] = "passed"
        if assessment_type == LlmAssessmentType.ANSWER_RUBRIC.value:
            fields["expected_answer_elements_json"] = normalized.get("expected_answer_elements") or []
            if normalized.get("risk_flags"):
                fields["safety_status"] = "failed"
            else:
                fields["safety_status"] = "passed"
        store.update_ai_question_candidate(subject_id, fields)
    elif subject_type == "keyword_candidate":
        if assessment_type == LlmAssessmentType.POOL_SEMANTIC.value:
            from ..catalogue.apply_semantics import write_pool_semantic_fields

            write_pool_semantic_fields(
                store,
                candidate_id=subject_id,
                normalized=normalized,
                assessment_id=assessment_id,
            )
        else:
            store.update_keyword_candidate(
                subject_id,
                {"llm_assessment_id": assessment_id, "updated_at": now},
            )
