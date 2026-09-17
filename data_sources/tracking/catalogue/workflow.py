"""Catalogue review export/import, approval, and activation workflow."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ..config import TrackingConfig
from ..enums import CandidateDecision, CatalogueBuildStatus
from ..exceptions import ConfigurationError, DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import natural_key, utc_now_iso
from . import PROVISIONAL_CATALOGUE_VERSION
from .compare import compare_with_provisional
from .quality_gates import evaluate_portfolio_gates
from .policy import policy_from_config

DEFAULT_APPROVED_QUESTION_COUNT = 20
DEFAULT_APPROVED_KEYWORD_MIN = 1


def _resolve_question_build(store: TrackingStore, keyword_build_id: str, question_build_id: Optional[str]) -> str:
    if question_build_id:
        rows = store.fetchall(
            "SELECT build_id FROM catalogue_builds WHERE build_id = ? AND build_type = 'ai_question'",
            (question_build_id,),
        )
        if not rows:
            raise DataQualityError(f"Unknown ai_question build_id {question_build_id}")
        return question_build_id
    children = store.fetchall(
        """
        SELECT build_id FROM catalogue_builds
        WHERE parent_build_id = ? AND build_type = 'ai_question'
        ORDER BY created_at DESC LIMIT 1
        """,
        (keyword_build_id,),
    )
    if not children:
        raise DataQualityError(
            f"No ai_question build linked to keyword build {keyword_build_id}. "
            "Run catalogue build-ai-questions first."
        )
    return children[0]["build_id"]


def export_review(
    store: TrackingStore,
    *,
    build_id: str,
    output: str,
    question_build_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")
    q_build = None
    try:
        q_build = _resolve_question_build(store, build_id, question_build_id)
    except DataQualityError:
        q_build = None

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_type",
        "id",
        "build_id",
        "text",
        "decision",
        "decision_reason",
        "review_group",
        "cluster_id",
        "proposed_target_page",
        "reviewed_target_page",
        "gsc_clicks",
        "gsc_impressions",
        "gsc_weighted_ctr",
        "gsc_weighted_position",
        "ga4_organic_sessions",
        "ga4_purchases",
        "ga4_revenue",
        "ga4_match_status",
        "ga4_match_page",
        "ga4_shared_page",
        "serper_position",
        "final_selection_score",
        "source_refs",
        # selection v2
        "routing_bucket",
        "brand_match_type",
        "brand_confidence",
        "competitor_name",
        "search_intent",
        "strategic_lane",
        "eligibility_status",
        "family_id",
        "family_role",
        "family_primary",
        "primary_observed_page",
        "target_page_status",
        "proposed_action",
        "claims_review_required",
        "selection_score_v2",
        "selection_rank_within_lane",
        "portfolio_slot",
        "alternate_rank",
        "selection_reasons",
        "eligibility_reasons",
        "serper_validation_status",
        "serp_visibility_score",
        "serp_opportunity_score",
        "serp_target_alignment_score",
        "score_confidence",
        # Phase 14b mid-funnel LLM semantics
        "semantic_authority",
        "llm_intent",
        "llm_business_relevance",
        "llm_customer_need",
        "llm_family_key",
        "llm_is_representative",
        "llm_actionability",
        "llm_recommended_target_page",
        "llm_no_suitable_target",
        "llm_semantic_confidence",
        "family_method",
    ]
    rows_out: List[Dict[str, Any]] = []
    for cand_row in store.fetchall(
        """
        SELECT * FROM keyword_candidates
        WHERE build_id = ?
        ORDER BY
            CASE decision WHEN 'selected' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
            portfolio_slot IS NULL, portfolio_slot,
            alternate_rank IS NULL, alternate_rank,
            COALESCE(selection_score_v2, final_selection_score) DESC
        """,
        (build_id,),
    ):
        cand = dict(cand_row)
        sources = store.fetchall(
            "SELECT gsc_natural_key FROM keyword_candidate_sources WHERE candidate_id = ?",
            (cand["candidate_id"],),
        )
        serper_status = ""
        if cand.get("serper_collected_at") or cand.get("serper_run_id"):
            serper_status = "validated" if cand.get("serper_position") is not None else "validated_absent"
        elif int(cand.get("serp_preselected") or 0):
            serper_status = "preselected_pending"
        rows_out.append(
            {
                "row_type": "keyword",
                "id": cand["candidate_id"],
                "build_id": build_id,
                "text": cand["canonical_keyword"],
                "decision": cand["decision"],
                "decision_reason": cand["decision_reason"] or "",
                "review_group": cand["review_group"] or "",
                "cluster_id": cand["cluster_id"] or "",
                "proposed_target_page": cand["proposed_target_page"] or "",
                "reviewed_target_page": cand["reviewed_target_page"] or "",
                "gsc_clicks": cand["gsc_clicks"],
                "gsc_impressions": cand["gsc_impressions"],
                "gsc_weighted_ctr": cand["gsc_weighted_ctr"],
                "gsc_weighted_position": cand["gsc_weighted_position"],
                "ga4_organic_sessions": cand["ga4_organic_sessions"],
                "ga4_purchases": cand["ga4_purchases"],
                "ga4_revenue": cand["ga4_revenue"],
                "ga4_match_status": cand["ga4_match_status"] or "",
                "ga4_match_page": cand["ga4_match_page"] or "",
                "ga4_shared_page": (
                    ""
                    if cand["ga4_shared_page"] is None
                    else int(cand["ga4_shared_page"])
                ),
                "serper_position": cand["serper_position"],
                "final_selection_score": cand["final_selection_score"],
                "source_refs": "|".join(s["gsc_natural_key"] for s in sources),
                "routing_bucket": cand["routing_bucket"] or "",
                "brand_match_type": cand["brand_match_type"] or "",
                "brand_confidence": cand["brand_confidence"] if cand["brand_confidence"] is not None else "",
                "competitor_name": cand["competitor_name"] or "",
                "search_intent": cand["search_intent"] or "",
                "strategic_lane": cand["strategic_lane"] or "",
                "eligibility_status": cand["eligibility_status"] or "",
                "family_id": cand["family_id"] or "",
                "family_role": cand["family_role"] or "",
                "family_primary": "1" if (cand["family_role"] or "") == "primary" else "0",
                "primary_observed_page": cand["proposed_target_page"] or cand["ga4_match_page"] or "",
                "target_page_status": cand["target_page_status"] or "",
                "proposed_action": cand["proposed_action"] or "",
                "claims_review_required": int(cand["claims_review_required"] or 0),
                "selection_score_v2": cand["selection_score_v2"] if cand["selection_score_v2"] is not None else "",
                "selection_rank_within_lane": cand["selection_rank_within_lane"]
                if cand["selection_rank_within_lane"] is not None
                else "",
                "portfolio_slot": cand["portfolio_slot"] if cand["portfolio_slot"] is not None else "",
                "alternate_rank": cand["alternate_rank"] if cand["alternate_rank"] is not None else "",
                "selection_reasons": cand["selection_reasons_json"] or "",
                "eligibility_reasons": cand["eligibility_reasons_json"] or "",
                "serper_validation_status": serper_status,
                "serp_visibility_score": cand["serp_visibility_score"]
                if cand["serp_visibility_score"] is not None
                else "",
                "serp_opportunity_score": cand["serp_opportunity_score"]
                if cand["serp_opportunity_score"] is not None
                else "",
                "serp_target_alignment_score": cand["serp_target_alignment_score"]
                if cand["serp_target_alignment_score"] is not None
                else "",
                "score_confidence": cand["score_confidence"] if cand["score_confidence"] is not None else "",
                "semantic_authority": cand.get("semantic_authority") or "",
                "llm_intent": cand.get("llm_intent") or "",
                "llm_business_relevance": cand.get("llm_business_relevance") or "",
                "llm_customer_need": cand.get("llm_customer_need") or "",
                "llm_family_key": cand.get("llm_family_key") or "",
                "llm_is_representative": (
                    ""
                    if cand.get("llm_is_representative") is None
                    else int(cand.get("llm_is_representative") or 0)
                ),
                "llm_actionability": cand.get("llm_actionability") or "",
                "llm_recommended_target_page": cand.get("llm_recommended_target_page") or "",
                "llm_no_suitable_target": int(cand.get("llm_no_suitable_target") or 0),
                "llm_semantic_confidence": cand.get("llm_semantic_confidence") or "",
                "family_method": cand.get("family_method") or "",
            }
        )
    if q_build:
        for q in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ? ORDER BY intent, question",
            (q_build,),
        ):
            sources = store.fetchall(
                "SELECT gsc_natural_key FROM ai_question_sources WHERE question_candidate_id = ?",
                (q["question_candidate_id"],),
            )
            rows_out.append(
                {
                    "row_type": "ai_question",
                    "id": q["question_candidate_id"],
                    "build_id": q_build,
                    "text": q["question"],
                    "decision": q["decision"],
                    "decision_reason": q["decision_reason"] or "",
                    "review_group": "",
                    "cluster_id": q["cluster_id"] or "",
                    "proposed_target_page": q["proposed_target_page"] or "",
                    "reviewed_target_page": "",
                    "gsc_clicks": "",
                    "gsc_impressions": "",
                    "gsc_weighted_ctr": "",
                    "gsc_weighted_position": "",
                    "ga4_organic_sessions": "",
                    "ga4_purchases": "",
                    "ga4_revenue": "",
                    "ga4_match_status": "",
                    "ga4_match_page": "",
                    "ga4_shared_page": "",
                    "serper_position": "",
                    "final_selection_score": "",
                    "source_refs": "|".join(s["gsc_natural_key"] for s in sources),
                    "routing_bucket": "",
                    "brand_match_type": "",
                    "brand_confidence": "",
                    "competitor_name": "",
                    "search_intent": "",
                    "strategic_lane": "",
                    "eligibility_status": "",
                    "family_id": "",
                    "family_role": "",
                    "family_primary": "",
                    "primary_observed_page": "",
                    "target_page_status": "",
                    "proposed_action": "",
                    "claims_review_required": "",
                    "selection_score_v2": "",
                    "selection_rank_within_lane": "",
                    "portfolio_slot": "",
                    "alternate_rank": "",
                    "selection_reasons": "",
                    "eligibility_reasons": "",
                    "serper_validation_status": "",
                    "serp_visibility_score": "",
                    "serp_opportunity_score": "",
                    "serp_target_alignment_score": "",
                    "score_confidence": "",
                    "semantic_authority": "",
                    "llm_intent": "",
                    "llm_business_relevance": "",
                    "llm_customer_need": "",
                    "llm_family_key": "",
                    "llm_is_representative": "",
                    "llm_actionability": "",
                    "llm_recommended_target_page": "",
                    "llm_no_suitable_target": "",
                    "llm_semantic_confidence": "",
                    "family_method": "",
                }
            )

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    return {
        "build_id": build_id,
        "question_build_id": q_build,
        "output": str(path),
        "rows": len(rows_out),
    }


def import_decisions(
    store: TrackingStore,
    *,
    build_id: str,
    input_path: str,
    question_build_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    path = Path(input_path)
    if not path.exists():
        raise ConfigurationError(f"Review file not found: {path}")
    q_build = None
    try:
        q_build = _resolve_question_build(store, build_id, question_build_id)
    except DataQualityError:
        q_build = None

    valid_decisions = {d.value for d in CandidateDecision}
    seen: Set[str] = set()
    updated = {"keyword": 0, "ai_question": 0}
    rejected_rows = 0
    now = utc_now_iso()

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row_type = (raw.get("row_type") or "").strip()
            row_id = (raw.get("id") or "").strip()
            decision = (raw.get("decision") or "").strip()
            if not row_type or not row_id:
                rejected_rows += 1
                continue
            if row_id in seen:
                raise DataQualityError(f"Duplicate review row id: {row_id}")
            seen.add(row_id)
            if decision not in valid_decisions:
                raise DataQualityError(f"Invalid decision '{decision}' for {row_id}")
            reason = (raw.get("decision_reason") or "").strip() or "imported_from_review"
            reviewed_page = (raw.get("reviewed_target_page") or "").strip() or None

            if row_type == "keyword":
                existing = store.fetchall(
                    "SELECT candidate_id FROM keyword_candidates WHERE candidate_id = ? AND build_id = ?",
                    (row_id, build_id),
                )
                if not existing:
                    raise DataQualityError(f"Unknown keyword candidate_id {row_id} for build {build_id}")
                payload = {
                    "decision": decision,
                    "decision_reason": reason,
                    "updated_at": now,
                }
                if reviewed_page:
                    payload["reviewed_target_page"] = reviewed_page
                cluster_id = (raw.get("cluster_id") or "").strip()
                if cluster_id:
                    payload["cluster_id"] = cluster_id
                store.update_keyword_candidate(row_id, payload)
                updated["keyword"] += 1
            elif row_type == "ai_question":
                if not q_build:
                    raise DataQualityError("AI question rows present but no question build is linked")
                existing = store.fetchall(
                    """
                    SELECT question_candidate_id FROM ai_question_candidates
                    WHERE question_candidate_id = ? AND build_id = ?
                    """,
                    (row_id, q_build),
                )
                if not existing:
                    raise DataQualityError(f"Unknown question_candidate_id {row_id}")
                store.update_ai_question_candidate(
                    row_id,
                    {
                        "decision": decision,
                        "decision_reason": reason,
                        "reviewed_by": "review_import",
                        "reviewed_at": now,
                        "updated_at": now,
                        "proposed_target_page": (raw.get("proposed_target_page") or "").strip()
                        or None,
                    },
                )
                updated["ai_question"] += 1
            else:
                raise DataQualityError(f"Unknown row_type '{row_type}'")

    store.update_catalogue_build(build_id, {"status": CatalogueBuildStatus.REVIEW.value})
    if q_build:
        store.update_catalogue_build(q_build, {"status": CatalogueBuildStatus.REVIEW.value})
    return {
        "build_id": build_id,
        "question_build_id": q_build,
        "updated": updated,
        "rejected_rows": rejected_rows,
        "imported_ids": len(seen),
    }


def approve_catalogue(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    approved_by: str,
    question_build_id: Optional[str] = None,
    keyword_minimum: Optional[int] = None,
    question_count: int = DEFAULT_APPROVED_QUESTION_COUNT,
) -> Dict[str, Any]:
    store.migrate()
    if not (approved_by or "").strip():
        raise ConfigurationError("approved-by is required")
    q_build = _resolve_question_build(store, build_id, question_build_id)
    min_keywords = keyword_minimum
    if min_keywords is None:
        min_keywords = int(getattr(config, "catalogue_selected_limit", DEFAULT_APPROVED_KEYWORD_MIN) or 1)

    selected_kw = store.fetchall(
        "SELECT candidate_id, cluster_id, proposed_target_page, reviewed_target_page FROM keyword_candidates "
        "WHERE build_id = ? AND decision = ?",
        (build_id, CandidateDecision.SELECTED.value),
    )
    if len(selected_kw) < min_keywords:
        raise DataQualityError(
            f"Approval requires at least {min_keywords} selected keywords; found {len(selected_kw)}"
        )

    # Block approval when a v2 portfolio quality gate has failed.
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    portfolio_json = builds[0]["portfolio_report_json"] if builds else None
    if portfolio_json:
        gate = evaluate_portfolio_gates(
            store,
            config,
            build_id=build_id,
            policy=policy_from_config(config),
        )
        if gate.get("gate_status") == "failed":
            raise DataQualityError(
                f"Portfolio quality gate failed ({gate.get('error_count')} errors); "
                "fix selected portfolio before approve"
            )

    # Every selected keyword needs a decision (already filtered) and a cluster.
    missing_cluster = [r["candidate_id"] for r in selected_kw if not r["cluster_id"]]
    if missing_cluster:
        raise DataQualityError(
            f"{len(missing_cluster)} selected keywords lack cluster membership; derive-clusters first"
        )

    selected_q = store.fetchall(
        "SELECT question_candidate_id, cluster_id FROM ai_question_candidates "
        "WHERE build_id = ? AND decision = ?",
        (q_build, CandidateDecision.SELECTED.value),
    )
    if len(selected_q) != question_count:
        raise DataQualityError(
            f"Approval requires exactly {question_count} selected AI questions; found {len(selected_q)}"
        )
    for q in selected_q:
        if not q["cluster_id"]:
            raise DataQualityError(f"Question {q['question_candidate_id']} missing cluster_id")

    from .question_review import evaluate_question_gates

    q_gates = evaluate_question_gates(
        store, config, question_build_id=q_build, production_count=question_count
    )
    if q_gates.get("gate_status") == "failed":
        raise DataQualityError(
            f"AI-question quality gate failed "
            f"(critical={q_gates.get('critical_failures')}, errors={q_gates.get('error_failures')}); "
            "complete LLM assessment, pilot, and review before approve"
        )

    # Mark clusters reviewed/approved for this build.
    now = utc_now_iso()
    store.execute(
        """
        UPDATE catalogue_clusters
        SET approval_status = 'approved', reviewed_by = ?, reviewed_at = ?
        WHERE build_id = ?
        """,
        (approved_by, now, build_id),
    )
    store.update_catalogue_build(
        build_id,
        {
            "status": CatalogueBuildStatus.APPROVED.value,
            "approved_by": approved_by,
            "approved_at": now,
        },
    )
    store.update_catalogue_build(
        q_build,
        {
            "status": CatalogueBuildStatus.APPROVED.value,
            "approved_by": approved_by,
            "approved_at": now,
        },
    )
    comparison = compare_with_provisional(
        store, config, keyword_build_id=build_id, question_build_id=q_build
    )
    return {
        "build_id": build_id,
        "question_build_id": q_build,
        "status": CatalogueBuildStatus.APPROVED.value,
        "approved_by": approved_by,
        "approved_at": now,
        "selected_keywords": len(selected_kw),
        "selected_questions": len(selected_q),
        "comparison": comparison,
        "note": "Approved but not activated. Run catalogue activate --confirm separately.",
    }


def activate_catalogue(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    confirm: bool,
    question_build_id: Optional[str] = None,
    catalogue_version: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    if not confirm:
        raise ConfigurationError("Activation requires --confirm")
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")
    if builds[0]["status"] not in {
        CatalogueBuildStatus.APPROVED.value,
        CatalogueBuildStatus.ACTIVATED.value,
    }:
        raise DataQualityError("Build must be approved before activation")
    q_build = _resolve_question_build(store, build_id, question_build_id)
    q_builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (q_build,))
    if q_builds[0]["status"] not in {
        CatalogueBuildStatus.APPROVED.value,
        CatalogueBuildStatus.ACTIVATED.value,
    }:
        raise DataQualityError("Question build must be approved before activation")

    version = catalogue_version or f"evidence-v1-{build_id[:8]}"
    now = utc_now_iso()

    selected_kw = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )
    ]
    selected_q = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
            (q_build, CandidateDecision.SELECTED.value),
        )
    ]

    # Transactional activation via single connection.
    with store.connection() as conn:
        # Expire previously activated evidence catalogues only (keep provisional candidate-v0.1).
        conn.execute(
            """
            UPDATE keyword_catalog
            SET active = 0,
                valid_to = COALESCE(valid_to, ?),
                approval_status = CASE
                    WHEN catalogue_version = ? THEN approval_status
                    WHEN approval_status = 'activated' THEN 'superseded'
                    ELSE approval_status
                END,
                updated_at = ?
            WHERE catalogue_version != ?
              AND approval_status = 'activated'
              AND (valid_to IS NULL OR valid_to = '')
            """,
            (now, PROVISIONAL_CATALOGUE_VERSION, now, PROVISIONAL_CATALOGUE_VERSION),
        )
        conn.execute(
            """
            UPDATE ai_question_catalog
            SET active = 0,
                valid_to = COALESCE(valid_to, ?),
                approval_status = CASE
                    WHEN catalogue_version = ? THEN approval_status
                    WHEN approval_status = 'activated' THEN 'superseded'
                    ELSE approval_status
                END,
                updated_at = ?
            WHERE catalogue_version != ?
              AND approval_status = 'activated'
              AND (valid_to IS NULL OR valid_to = '')
            """,
            (now, PROVISIONAL_CATALOGUE_VERSION, now, PROVISIONAL_CATALOGUE_VERSION),
        )

        for cand in selected_kw:
            keyword = cand["canonical_keyword"]
            keyword_id = natural_key([version, keyword, "sgp", "all"])
            cluster_rows = conn.execute(
                "SELECT cluster_name FROM catalogue_clusters WHERE cluster_id = ?",
                (cand["cluster_id"],),
            ).fetchone()
            cluster_name = cluster_rows["cluster_name"] if cluster_rows else "unclustered"
            target = (
                cand.get("reviewed_target_page")
                or cand.get("proposed_target_page")
                or cand.get("primary_observed_page")
                or ""
            )
            conn.execute(
                """
                INSERT INTO keyword_catalog(
                    keyword_id, keyword, cluster, target_page, country, device, language,
                    active, valid_from, valid_to, created_at, updated_at,
                    catalogue_version, build_id, approval_status, approved_by, approved_at
                ) VALUES (?, ?, ?, ?, 'sgp', 'all', 'en', 1, ?, NULL, ?, ?, ?, ?, 'activated', ?, ?)
                ON CONFLICT(keyword_id) DO UPDATE SET
                    keyword = excluded.keyword,
                    cluster = excluded.cluster,
                    target_page = excluded.target_page,
                    active = 1,
                    valid_from = excluded.valid_from,
                    valid_to = NULL,
                    updated_at = excluded.updated_at,
                    catalogue_version = excluded.catalogue_version,
                    build_id = excluded.build_id,
                    approval_status = 'activated',
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at
                """,
                (
                    keyword_id,
                    keyword,
                    cluster_name,
                    target,
                    now,
                    now,
                    now,
                    version,
                    build_id,
                    builds[0]["approved_by"],
                    builds[0]["approved_at"],
                ),
            )

        for q in selected_q:
            question_id = natural_key([version, q["question"]])
            cluster_rows = conn.execute(
                "SELECT cluster_name FROM catalogue_clusters WHERE cluster_id = ?",
                (q["cluster_id"],),
            ).fetchone()
            cluster_name = cluster_rows["cluster_name"] if cluster_rows else "unclustered"
            conn.execute(
                """
                INSERT INTO ai_question_catalog(
                    question_id, question, cluster, target_page, locale, active,
                    valid_from, valid_to, created_at, updated_at,
                    catalogue_version, build_id, approval_status, approved_by, approved_at
                ) VALUES (?, ?, ?, ?, 'en-SG', 1, ?, NULL, ?, ?, ?, ?, 'activated', ?, ?)
                ON CONFLICT(question_id) DO UPDATE SET
                    question = excluded.question,
                    cluster = excluded.cluster,
                    target_page = excluded.target_page,
                    active = 1,
                    valid_from = excluded.valid_from,
                    valid_to = NULL,
                    updated_at = excluded.updated_at,
                    catalogue_version = excluded.catalogue_version,
                    build_id = excluded.build_id,
                    approval_status = 'activated',
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at
                """,
                (
                    question_id,
                    q["question"],
                    cluster_name,
                    q["proposed_target_page"],
                    now,
                    now,
                    now,
                    version,
                    q_build,
                    q_builds[0]["approved_by"],
                    q_builds[0]["approved_at"],
                ),
            )

        conn.execute(
            """
            UPDATE catalogue_builds
            SET status = ?, activated_at = ?
            WHERE build_id IN (?, ?)
            """,
            (CatalogueBuildStatus.ACTIVATED.value, now, build_id, q_build),
        )
        # Idempotent: supersede other activated builds of same type except these.
        conn.execute(
            """
            UPDATE catalogue_builds
            SET status = ?
            WHERE build_type = 'keyword'
              AND status = ?
              AND build_id != ?
            """,
            (
                CatalogueBuildStatus.SUPERSEDED.value,
                CatalogueBuildStatus.ACTIVATED.value,
                build_id,
            ),
        )

    provisional_keywords = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_catalog WHERE catalogue_version = ?",
        (PROVISIONAL_CATALOGUE_VERSION,),
    )[0]["n"]
    active_evidence = store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_catalog WHERE catalogue_version = ? AND active = 1",
        (version,),
    )[0]["n"]
    return {
        "build_id": build_id,
        "question_build_id": q_build,
        "catalogue_version": version,
        "status": CatalogueBuildStatus.ACTIVATED.value,
        "activated_at": now,
        "activated_keywords": active_evidence,
        "activated_questions": len(selected_q),
        "provisional_rows_still_queryable": int(provisional_keywords),
        "note": "Activation is idempotent on keyword_id/question_id natural keys; provisional candidate-v0.1 retained.",
    }
