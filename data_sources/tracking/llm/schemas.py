"""Strict input/output schemas for bounded LLM assessments."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

SEMANTIC_REVIEW_PROMPT_VERSION = "semantic_review_v1"
QUESTION_REWRITE_PROMPT_VERSION = "question_rewrite_v1"
ANSWER_RUBRIC_PROMPT_VERSION = "answer_rubric_v1"
OPPORTUNITY_DIAGNOSIS_PROMPT_VERSION = "opportunity_diagnosis_v1"

ALLOWED_ACTION_TYPES = {
    "title_meta_rewrite",
    "internal_linking",
    "answer_section",
    "content_expansion",
    "product_mapping",
    "content_consolidation",
    "technical_fix",
    "new_page",
    "geo_evidence_upgrade",
    "manual_investigation",
}

# Measured facts the model must never invent or overwrite.
FORBIDDEN_FACT_KEYS = {
    "search_volume",
    "gsc_clicks",
    "gsc_impressions",
    "gsc_ctr",
    "gsc_position",
    "ga4_sessions",
    "ga4_purchases",
    "ga4_revenue",
    "serper_position",
    "approval_status",
    "priority_score",
    "expected_incremental_clicks",
    "estimated_cost",
}


def prompt_version_for(assessment_type: str) -> str:
    mapping = {
        "semantic_review": SEMANTIC_REVIEW_PROMPT_VERSION,
        "question_rewrite": QUESTION_REWRITE_PROMPT_VERSION,
        "answer_rubric": ANSWER_RUBRIC_PROMPT_VERSION,
        "opportunity_diagnosis": OPPORTUNITY_DIAGNOSIS_PROMPT_VERSION,
    }
    if assessment_type not in mapping:
        raise ValueError(f"Unknown assessment_type: {assessment_type}")
    return mapping[assessment_type]


def _as_str_list(value: Any, field: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{field} must be a list")
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field} items must be non-empty strings")
            continue
        out.append(item.strip())
    return out


def _contains_forbidden_facts(payload: Dict[str, Any], errors: List[str]) -> None:
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in FORBIDDEN_FACT_KEYS:
                    errors.append(f"model must not invent measured field '{key}'")
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def validate_assessment_output(
    assessment_type: str,
    output: Any,
    *,
    allowed_target_pages: Optional[Sequence[str]] = None,
    allowed_evidence_refs: Optional[Sequence[str]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Validate model JSON. Returns (ok, errors, normalized_output)."""
    errors: List[str] = []
    if not isinstance(output, dict):
        return False, ["output must be a JSON object"], {}

    _contains_forbidden_facts(output, errors)
    allow_pages: Set[str] = {p for p in (allowed_target_pages or []) if p}
    allow_refs: Set[str] = {r for r in (allowed_evidence_refs or []) if r}
    normalized: Dict[str, Any] = {}

    if assessment_type == "semantic_review":
        for key in ("customer_need", "intent_summary", "business_relevance_rationale"):
            value = output.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{key} must be a non-empty string")
            else:
                normalized[key] = value.strip()
        normalized["possible_semantic_duplicates"] = _as_str_list(
            output.get("possible_semantic_duplicates"), "possible_semantic_duplicates", errors
        )
        page = output.get("recommended_target_page")
        if page is not None:
            if not isinstance(page, str) or not page.strip():
                errors.append("recommended_target_page must be a string when present")
            elif allow_pages and page.strip() not in allow_pages:
                errors.append("recommended_target_page outside supplied allowlist")
            else:
                normalized["recommended_target_page"] = page.strip()
        confidence = output.get("confidence", "medium")
        if confidence not in {"high", "medium", "low"}:
            errors.append("confidence must be high|medium|low")
        else:
            normalized["confidence"] = confidence
        normalized["assumptions"] = _as_str_list(output.get("assumptions"), "assumptions", errors)
        normalized["risk_flags"] = _as_str_list(output.get("risk_flags"), "risk_flags", errors)

    elif assessment_type == "question_rewrite":
        rewritten = output.get("rewritten_question")
        if not isinstance(rewritten, str) or not rewritten.strip():
            errors.append("rewritten_question must be a non-empty string")
        else:
            normalized["rewritten_question"] = rewritten.strip()
        natural = output.get("naturalness", "pending")
        if natural not in {"passed", "failed", "pending"}:
            errors.append("naturalness must be passed|failed|pending")
        else:
            normalized["naturalness"] = natural
        normalized["rationale"] = (
            output.get("rationale").strip()
            if isinstance(output.get("rationale"), str)
            else ""
        )
        normalized["risk_flags"] = _as_str_list(output.get("risk_flags"), "risk_flags", errors)

    elif assessment_type == "answer_rubric":
        elements = _as_str_list(
            output.get("expected_answer_elements"), "expected_answer_elements", errors
        )
        if len(elements) < 2:
            errors.append("expected_answer_elements requires at least 2 items")
        normalized["expected_answer_elements"] = elements
        normalized["ymyl_notes"] = (
            output.get("ymyl_notes").strip()
            if isinstance(output.get("ymyl_notes"), str)
            else ""
        )
        normalized["risk_flags"] = _as_str_list(output.get("risk_flags"), "risk_flags", errors)

    elif assessment_type == "opportunity_diagnosis":
        problem = output.get("problem")
        if not isinstance(problem, str) or not problem.strip():
            errors.append("problem must be a non-empty string")
        else:
            normalized["problem"] = problem.strip()
        normalized["diagnosis"] = _as_str_list(output.get("diagnosis"), "diagnosis", errors)
        action_type = output.get("proposed_action_type")
        if action_type not in ALLOWED_ACTION_TYPES:
            errors.append("proposed_action_type not in allowlist")
        else:
            normalized["proposed_action_type"] = action_type
        normalized["proposed_actions"] = _as_str_list(
            output.get("proposed_actions"), "proposed_actions", errors
        )
        refs = _as_str_list(output.get("evidence_refs"), "evidence_refs", errors)
        if allow_refs:
            invented = [r for r in refs if r not in allow_refs]
            if invented:
                errors.append(f"invented evidence_refs: {invented[:5]}")
        normalized["evidence_refs"] = refs
        normalized["assumptions"] = _as_str_list(output.get("assumptions"), "assumptions", errors)
        normalized["risk_flags"] = _as_str_list(output.get("risk_flags"), "risk_flags", errors)
        confidence = output.get("confidence", "medium")
        if confidence not in {"high", "medium", "low"}:
            errors.append("confidence must be high|medium|low")
        else:
            normalized["confidence"] = confidence
        page = output.get("recommended_target_page")
        if page is not None:
            if not isinstance(page, str) or not page.strip():
                errors.append("recommended_target_page must be a string when present")
            elif allow_pages and page.strip() not in allow_pages:
                errors.append("recommended_target_page outside supplied allowlist")
            else:
                normalized["recommended_target_page"] = page.strip()
    else:
        errors.append(f"Unknown assessment_type: {assessment_type}")

    return (len(errors) == 0, errors, normalized)
