"""Human review helpers for LLM-assisted AI-question phrasings."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..enums import CandidateDecision, QuestionSourceType
from ..exceptions import ConfigurationError, DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def export_llm_phrasing_review(
    store: TrackingStore,
    *,
    question_build_id: str,
    output: str,
) -> Dict[str, Any]:
    """Export template + LLM phrasings for human pick in Lark/CSV."""
    store.migrate()
    rows = [
        dict(r)
        for r in store.fetchall(
            """
            SELECT * FROM ai_question_candidates
            WHERE build_id = ? AND decision = ?
            ORDER BY created_at ASC
            """,
            (question_build_id, CandidateDecision.SELECTED.value),
        )
    ]
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "question_candidate_id",
        "parent_keyword",
        "source_candidate_id",
        "template_question",
        "phrasing_1",
        "phrasing_2",
        "human_pick",  # template|1|2|custom
        "custom_question",
        "proposed_target_page",
        "intent",
        "llm_rationale",
        "risk_flags",
        "reviewed_by",
        "decision",  # selected|rejected
        "decision_reason",
    ]
    out_rows: List[Dict[str, Any]] = []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            sources = _load_json(row.get("source_candidate_ids"), [])
            source_id = sources[0] if sources else ""
            parent_keyword = ""
            if source_id:
                parent = store.fetchall(
                    "SELECT normalized_keyword, canonical_keyword FROM keyword_candidates WHERE candidate_id = ?",
                    (source_id,),
                )
                if parent:
                    parent_keyword = parent[0]["normalized_keyword"] or parent[0]["canonical_keyword"] or ""
            pilot = _load_json(row.get("pilot_results_json"), {})
            phrasings = pilot.get("llm_phrasings") if isinstance(pilot, dict) else []
            if not isinstance(phrasings, list):
                phrasings = []
            template = ""
            if isinstance(pilot, dict):
                template = pilot.get("template_question") or row.get("question") or ""
            else:
                template = row.get("question") or ""
            record = {
                "question_candidate_id": row["question_candidate_id"],
                "parent_keyword": parent_keyword,
                "source_candidate_id": source_id,
                "template_question": template,
                "phrasing_1": phrasings[0] if len(phrasings) > 0 else "",
                "phrasing_2": phrasings[1] if len(phrasings) > 1 else "",
                "human_pick": "",
                "custom_question": "",
                "proposed_target_page": row.get("proposed_target_page") or "",
                "intent": row.get("intent") or "",
                "llm_rationale": (pilot.get("llm_phrasing_rationale") if isinstance(pilot, dict) else "") or "",
                "risk_flags": json.dumps(
                    (pilot.get("llm_phrasing_risk_flags") if isinstance(pilot, dict) else []) or []
                ),
                "reviewed_by": "",
                "decision": row.get("decision") or "selected",
                "decision_reason": row.get("decision_reason") or "",
            }
            writer.writerow(record)
            out_rows.append(record)
    return {
        "question_build_id": question_build_id,
        "output": str(path),
        "rows": len(out_rows),
        "with_phrasings": sum(1 for r in out_rows if r["phrasing_1"]),
    }


def import_llm_phrasing_decisions(
    store: TrackingStore,
    *,
    question_build_id: str,
    input_path: str,
) -> Dict[str, Any]:
    """Apply human phrasing picks. Sets source_type=llm_assisted when an LLM phrasing is chosen."""
    store.migrate()
    path = Path(input_path)
    if not path.exists():
        raise ConfigurationError(f"Missing review file: {input_path}")
    now = utc_now_iso()
    updated = 0
    rejected = 0
    with path.open(encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            qid = (raw.get("question_candidate_id") or "").strip()
            if not qid:
                continue
            rows = store.fetchall(
                "SELECT * FROM ai_question_candidates WHERE question_candidate_id = ? AND build_id = ?",
                (qid, question_build_id),
            )
            if not rows:
                raise DataQualityError(f"Unknown question_candidate_id for build: {qid}")
            decision = (raw.get("decision") or "selected").strip().lower()
            reviewed_by = (raw.get("reviewed_by") or "").strip() or "reviewer"
            reason = (raw.get("decision_reason") or "").strip()
            if decision == "rejected":
                row0 = dict(rows[0])
                pilot = _load_json(row0.get("pilot_results_json"), {})
                if not isinstance(pilot, dict):
                    pilot = {}
                if reason:
                    pilot["rejection_reason"] = reason
                store.update_ai_question_candidate(
                    qid,
                    {
                        "decision": CandidateDecision.REJECTED.value,
                        "decision_reason": reason or "rejected_in_phrasing_review",
                        "reviewed_by": reviewed_by,
                        "reviewed_at": now,
                        "pilot_results_json": pilot,
                        "updated_at": now,
                    },
                )
                rejected += 1
                continue

            pick = (raw.get("human_pick") or "").strip().lower()
            custom = (raw.get("custom_question") or "").strip()
            row0 = dict(rows[0])
            template = (raw.get("template_question") or row0.get("question") or "").strip()
            p1 = (raw.get("phrasing_1") or "").strip()
            p2 = (raw.get("phrasing_2") or "").strip()

            if pick in {"1", "phrasing_1", "p1"} and p1:
                chosen, source_type = p1, QuestionSourceType.LLM_ASSISTED.value
            elif pick in {"2", "phrasing_2", "p2"} and p2:
                chosen, source_type = p2, QuestionSourceType.LLM_ASSISTED.value
            elif pick in {"custom"} and custom:
                chosen, source_type = custom, QuestionSourceType.LLM_ASSISTED.value
            elif pick in {"template", "0", "template_question"} and template:
                chosen, source_type = template, QuestionSourceType.SYNTHETIC_DRAFT.value
            elif custom:
                chosen, source_type = custom, QuestionSourceType.LLM_ASSISTED.value
            elif p1:
                chosen, source_type = p1, QuestionSourceType.LLM_ASSISTED.value
            else:
                chosen, source_type = template, QuestionSourceType.SYNTHETIC_DRAFT.value

            if not chosen:
                raise DataQualityError(f"No usable phrasing for {qid}")

            pilot = _load_json(row0.get("pilot_results_json"), {})
            if not isinstance(pilot, dict):
                pilot = {}
            pilot["human_pick"] = pick or "auto"
            pilot["chosen_question"] = chosen

            store.update_ai_question_candidate(
                qid,
                {
                    "question": chosen,
                    "source_type": source_type,
                    "decision": CandidateDecision.SELECTED.value,
                    "decision_reason": reason or "phrasing_review_selected",
                    "human_validated_hypothesis": 1,
                    "reviewed_by": reviewed_by,
                    "reviewed_at": now,
                    "pilot_results_json": pilot,
                    "transformation_method": (
                        "llm_assisted_selected"
                        if source_type == QuestionSourceType.LLM_ASSISTED.value
                        else row0.get("transformation_method")
                    ),
                    "updated_at": now,
                },
            )
            updated += 1
    return {
        "question_build_id": question_build_id,
        "updated": updated,
        "rejected": rejected,
        "input": str(path),
    }
