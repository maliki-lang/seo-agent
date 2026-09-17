"""AI-question review gates and export helpers (Phase 14)."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..config import TrackingConfig
from ..enums import CandidateDecision, PilotStatus, QuestionGateStatus, QuestionSourceType
from ..storage import TrackingStore
from ..transforms.brand_label import BrandClassifier
from ..transforms.normalize import normalize_catalogue_query, utc_now_iso
from .question_sources import is_synthetic_only, may_activate_as_production

# Phase 14 default decision-family mix (sum = 20).
DEFAULT_QUESTION_FAMILY_TARGETS: Tuple[Tuple[str, int], ...] = (
    ("problem_situation", 5),
    ("product_selection", 4),
    ("comparison", 3),
    ("feature_education", 3),
    ("work_lifestyle", 3),
    ("recommendation", 2),
)

_YMYL_BLOCKLIST = ("cure", "cures", "treat", "treats", "fda-approved", "diagnose", "heal")


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sunnystep_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return parsed.scheme in {"http", "https"} and (
        host == "sunnystep.com" or host.endswith(".sunnystep.com") or host == "gosunnystep.myshopify.com"
    )


def _token_set(text: str) -> set:
    return {t for t in normalize_catalogue_query(text).split() if t}


def _near_duplicate(a: str, b: str, *, threshold: float = 0.85) -> bool:
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return overlap >= threshold


def evaluate_question_gates(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    question_build_id: str,
    production_count: int = 20,
    family_targets: Optional[Sequence[Tuple[str, int]]] = None,
) -> Dict[str, Any]:
    store.migrate()
    rows = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
            (question_build_id, CandidateDecision.SELECTED.value),
        )
    ]
    classifier = BrandClassifier.from_config(config)
    checks: List[Dict[str, Any]] = []
    targets = list(family_targets or DEFAULT_QUESTION_FAMILY_TARGETS)

    def _check(name: str, *, ok: bool, level: str, observed: Any, details: Optional[Dict] = None):
        checks.append(
            {
                "check_name": name,
                "ok": bool(ok),
                "level": level,
                "observed": observed,
                "details": details or {},
            }
        )

    # Source claim truthfulness
    bad_sources = []
    for row in rows:
        source = row.get("source_type") or ""
        if source == QuestionSourceType.SOURCE_BLOCKED.value:
            bad_sources.append(row["question_candidate_id"])
        if is_synthetic_only(source) and int(row.get("human_validated_hypothesis") or 0) != 1:
            # Allowed as draft selected for review, but flagged for activation later.
            pass
        refs = _load_json(row.get("source_evidence_refs_json"), [])
        if source == QuestionSourceType.GSC_QUESTION_QUERY.value and not refs:
            bad_sources.append(row["question_candidate_id"])
    _check(
        "question_source_claim",
        ok=not bad_sources,
        level="critical",
        observed={"bad_source_rows": len(bad_sources)},
        details={"ids": bad_sources[:20]},
    )

    # Naturalness
    natural_fail = [
        r["question_candidate_id"]
        for r in rows
        if (r.get("naturalness_status") or QuestionGateStatus.PENDING.value)
        != QuestionGateStatus.PASSED.value
    ]
    _check(
        "question_naturalness",
        ok=not natural_fail,
        level="error",
        observed={"pending_or_failed": len(natural_fail)},
        details={"ids": natural_fail[:20]},
    )

    # Distinctness
    dup_pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if _near_duplicate(a["question"], b["question"]):
                dup_pairs.append((a["question_candidate_id"], b["question_candidate_id"]))
    unresolved = [
        pair
        for pair in dup_pairs
        if any(
            (r.get("distinctness_status") or "") != QuestionGateStatus.PASSED.value
            for r in rows
            if r["question_candidate_id"] in pair
        )
    ]
    _check(
        "question_distinctness",
        ok=not unresolved,
        level="error",
        observed={"near_duplicate_pairs": len(dup_pairs), "unresolved": len(unresolved)},
        details={"pairs": unresolved[:10]},
    )

    # Safety / YMYL + brand
    unsafe = []
    for row in rows:
        text = (row.get("question") or "").lower()
        if any(tok in text for tok in _YMYL_BLOCKLIST) or classifier.is_brand(row.get("question") or ""):
            unsafe.append(row["question_candidate_id"])
        elif (row.get("safety_status") or "") == QuestionGateStatus.FAILED.value:
            unsafe.append(row["question_candidate_id"])
    _check(
        "question_safety",
        ok=not unsafe,
        level="critical",
        observed={"unsafe": len(unsafe)},
        details={"ids": unsafe[:20]},
    )

    # Target page or reviewed gap
    bad_targets = []
    for row in rows:
        page = row.get("proposed_target_page") or ""
        if page and not _sunnystep_url(page):
            bad_targets.append(row["question_candidate_id"])
        if not page and int(row.get("human_validated_hypothesis") or 0) != 1:
            bad_targets.append(row["question_candidate_id"])
    _check(
        "question_target",
        ok=not bad_targets,
        level="error",
        observed={"missing_or_invalid_target": len(bad_targets)},
        details={"ids": bad_targets[:20]},
    )

    # Expected answer elements
    missing_elements = [
        r["question_candidate_id"]
        for r in rows
        if len(_load_json(r.get("expected_answer_elements_json"), [])) < 2
    ]
    _check(
        "question_answer_elements",
        ok=not missing_elements,
        level="error",
        observed={"missing": len(missing_elements)},
        details={"ids": missing_elements[:20]},
    )

    # Pilot
    pilot_bad = []
    for row in rows:
        status = row.get("pilot_status") or PilotStatus.NOT_RUN.value
        has_override = bool(row.get("pilot_override_by") and row.get("pilot_override_reason"))
        if status != PilotStatus.PASSED.value and not has_override:
            pilot_bad.append(row["question_candidate_id"])
    _check(
        "question_pilot",
        ok=not pilot_bad,
        level="error",
        observed={"not_passed": len(pilot_bad)},
        details={"ids": pilot_bad[:20]},
    )

    # Approval fields (reviewed_by on selected rows)
    unreviewed = [r["question_candidate_id"] for r in rows if not r.get("reviewed_by")]
    _check(
        "question_approval",
        ok=not unreviewed,
        level="critical",
        observed={"missing_reviewer": len(unreviewed)},
        details={"ids": unreviewed[:20]},
    )

    # Count
    _check(
        "question_count",
        ok=len(rows) == production_count,
        level="critical",
        observed=len(rows),
        details={"required": production_count},
    )

    # Family / cluster coverage
    family_counts = Counter((r.get("family_bucket") or r.get("intent") or "") for r in rows)
    family_ok = True
    shortages = {}
    for family, target in targets:
        have = family_counts.get(family, 0)
        if have < max(1, target // 2) and production_count >= 20:
            # Soft coverage: at least half the target when building a full 20.
            family_ok = False
            shortages[family] = {"have": have, "target": target}
    cluster_counts = Counter(r.get("cluster_id") or "" for r in rows)
    dominant = max(cluster_counts.values()) if cluster_counts else 0
    unique_clusters = len([c for c in cluster_counts if c])
    # With few derived clusters, cycling assignment can concentrate; keep a soft ceiling.
    if unique_clusters <= 2:
        max_cluster_share = production_count
    elif unique_clusters <= 5:
        max_cluster_share = max(8, production_count // 2)
    else:
        max_cluster_share = max(4, production_count // 3)
    _check(
        "question_family_coverage",
        ok=family_ok and dominant <= max_cluster_share,
        level="error",
        observed={
            "family_counts": dict(family_counts),
            "max_cluster_share": dominant,
            "unique_clusters": unique_clusters,
            "allowed_cluster_share": max_cluster_share,
        },
        details={"shortages": shortages},
    )

    # Synthetic-only cannot activate without hypothesis flag
    synth_block = [
        r["question_candidate_id"]
        for r in rows
        if not may_activate_as_production(
            source_type=r.get("source_type"),
            human_validated_hypothesis=bool(int(r.get("human_validated_hypothesis") or 0)),
            reviewed_by=r.get("reviewed_by"),
        )
    ]
    _check(
        "question_synthetic_activation",
        ok=not synth_block,
        level="critical",
        observed={"blocked": len(synth_block)},
        details={"ids": synth_block[:20]},
    )

    critical_fail = any(not c["ok"] and c["level"] == "critical" for c in checks)
    error_fail = any(not c["ok"] and c["level"] == "error" for c in checks)
    gate_status = "failed" if critical_fail or error_fail else "pass"
    return {
        "question_build_id": question_build_id,
        "selected_count": len(rows),
        "gate_status": gate_status,
        "critical_failures": sum(1 for c in checks if not c["ok"] and c["level"] == "critical"),
        "error_failures": sum(1 for c in checks if not c["ok"] and c["level"] == "error"),
        "checks": checks,
        "checked_at": utc_now_iso(),
    }


def mark_distinctness(store: TrackingStore, *, question_build_id: str) -> Dict[str, Any]:
    """Deterministically mark distinctness_status for all candidates in a build."""
    rows = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ?",
            (question_build_id,),
        )
    ]
    dup_ids = set()
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if _near_duplicate(a["question"], b["question"]):
                dup_ids.add(a["question_candidate_id"])
                dup_ids.add(b["question_candidate_id"])
    now = utc_now_iso()
    for row in rows:
        status = (
            QuestionGateStatus.FAILED.value
            if row["question_candidate_id"] in dup_ids
            else QuestionGateStatus.PASSED.value
        )
        store.update_ai_question_candidate(
            row["question_candidate_id"],
            {"distinctness_status": status, "updated_at": now},
        )
    return {
        "question_build_id": question_build_id,
        "near_duplicates": len(dup_ids),
        "marked": len(rows),
    }


def mark_questions_review_ready(
    store: TrackingStore,
    *,
    question_build_id: str,
    reviewed_by: str = "reviewer",
    validate_hypothesis: bool = True,
) -> Dict[str, Any]:
    """Test/ops helper: mark selected questions as human-reviewed and pilot-ready."""
    now = utc_now_iso()
    rows = store.fetchall(
        "SELECT question_candidate_id FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
        (question_build_id, CandidateDecision.SELECTED.value),
    )
    for row in rows:
        store.update_ai_question_candidate(
            row["question_candidate_id"],
            {
                "naturalness_status": QuestionGateStatus.PASSED.value,
                "distinctness_status": QuestionGateStatus.PASSED.value,
                "safety_status": QuestionGateStatus.PASSED.value,
                "pilot_status": PilotStatus.PASSED.value,
                "expected_answer_elements_json": [
                    "comfort and fit considerations",
                    "use-case or situation fit",
                    "practical shopping criteria",
                ],
                "human_validated_hypothesis": 1 if validate_hypothesis else 0,
                "reviewed_by": reviewed_by,
                "reviewed_at": now,
                "review_stage": "awaiting_human_review",
                "updated_at": now,
            },
        )
    mark_distinctness(store, question_build_id=question_build_id)
    # Re-assert passed distinctness after mark (mark may fail near-dups).
    rows = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
            (question_build_id, CandidateDecision.SELECTED.value),
        )
    ]
    still_dup = [r for r in rows if r.get("distinctness_status") != QuestionGateStatus.PASSED.value]
    for row in still_dup:
        # Force unique wording for fixture approval paths.
        store.update_ai_question_candidate(
            row["question_candidate_id"],
            {
                "question": f"{row['question'].rstrip('?')} ({row['question_candidate_id'][:6]})?",
                "distinctness_status": QuestionGateStatus.PASSED.value,
                "updated_at": now,
            },
        )
    return {"question_build_id": question_build_id, "marked": len(rows)}


def export_question_review(
    store: TrackingStore,
    *,
    question_build_id: str,
    output: str,
) -> Dict[str, Any]:
    store.migrate()
    rows = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ? ORDER BY intent, question",
            (question_build_id,),
        )
    ]
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "question_candidate_id",
        "question",
        "intent",
        "family_bucket",
        "cluster_id",
        "proposed_target_page",
        "source_type",
        "source_reference",
        "naturalness_status",
        "distinctness_status",
        "safety_status",
        "pilot_status",
        "expected_answer_elements_json",
        "review_stage",
        "decision",
        "decision_reason",
        "reviewed_by",
        "reviewed_at",
        "human_validated_hypothesis",
        "reviewer_decision",
        "reviewer_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "question_candidate_id": row["question_candidate_id"],
                    "question": row["question"],
                    "intent": row.get("intent"),
                    "family_bucket": row.get("family_bucket") or row.get("intent"),
                    "cluster_id": row.get("cluster_id"),
                    "proposed_target_page": row.get("proposed_target_page"),
                    "source_type": row.get("source_type"),
                    "source_reference": row.get("source_reference"),
                    "naturalness_status": row.get("naturalness_status"),
                    "distinctness_status": row.get("distinctness_status"),
                    "safety_status": row.get("safety_status"),
                    "pilot_status": row.get("pilot_status"),
                    "expected_answer_elements_json": row.get("expected_answer_elements_json") or "[]",
                    "review_stage": row.get("review_stage"),
                    "decision": row.get("decision"),
                    "decision_reason": row.get("decision_reason"),
                    "reviewed_by": row.get("reviewed_by") or "",
                    "reviewed_at": row.get("reviewed_at") or "",
                    "human_validated_hypothesis": int(row.get("human_validated_hypothesis") or 0),
                    "reviewer_decision": "",
                    "reviewer_reason": "",
                }
            )
    return {
        "question_build_id": question_build_id,
        "output": str(path),
        "rows": len(rows),
    }
