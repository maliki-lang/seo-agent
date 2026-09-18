"""Question source-policy helpers (Phase 14)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from ..enums import QuestionSourceType

# Sources that currently exist in-repo with truthful evidence paths.
AVAILABLE_SOURCES: Set[str] = {
    QuestionSourceType.GSC_QUESTION_QUERY.value,
    QuestionSourceType.APPROVED_KEYWORD_FAMILY.value,
    QuestionSourceType.SYNTHETIC_TEMPLATE.value,
    QuestionSourceType.LLM_REWRITE.value,
    QuestionSourceType.LLM_ASSISTED.value,
    QuestionSourceType.SYNTHETIC_DRAFT.value,
    QuestionSourceType.BUSINESS_NOMINATED.value,
}

# Sources that must be deferred until an ingestion path exists.
BLOCKED_SOURCES: Set[str] = {
    QuestionSourceType.CUSTOMER_SUPPORT.value,
    QuestionSourceType.SITE_SEARCH.value,
    QuestionSourceType.PRODUCT_REVIEW.value,
    QuestionSourceType.PEOPLE_ALSO_ASK.value,
    QuestionSourceType.COMPETITOR_FAQ.value,
}


def classify_source_availability(source_type: str) -> Dict[str, Any]:
    if source_type in BLOCKED_SOURCES:
        return {
            "source_type": QuestionSourceType.SOURCE_BLOCKED.value,
            "requested_source_type": source_type,
            "status": "blocked",
            "reason": f"{source_type} ingestion is not available; deferred without claiming customer validation",
        }
    if source_type in AVAILABLE_SOURCES:
        return {
            "source_type": source_type,
            "requested_source_type": source_type,
            "status": "available",
            "reason": "source path exists",
        }
    return {
        "source_type": QuestionSourceType.SOURCE_BLOCKED.value,
        "requested_source_type": source_type,
        "status": "blocked",
        "reason": f"unknown source_type {source_type}",
    }


def is_synthetic_only(source_type: Optional[str]) -> bool:
    return (source_type or "") in {
        QuestionSourceType.SYNTHETIC_TEMPLATE.value,
        QuestionSourceType.SYNTHETIC_DRAFT.value,
        QuestionSourceType.LLM_REWRITE.value,
        QuestionSourceType.LLM_ASSISTED.value,
    }


def may_activate_as_production(
    *,
    source_type: Optional[str],
    human_validated_hypothesis: bool,
    reviewed_by: Optional[str],
) -> bool:
    """Template/LLM wording alone is not demand proof."""
    if is_synthetic_only(source_type) and not human_validated_hypothesis:
        return False
    if source_type == QuestionSourceType.SOURCE_BLOCKED.value:
        return False
    if not reviewed_by:
        return False
    return True
