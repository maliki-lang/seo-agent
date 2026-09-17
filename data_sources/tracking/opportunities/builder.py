"""Phase 15 opportunity portfolio builder with optional LLM diagnosis."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, Optional

from ..config import TrackingConfig
from ..enums import LlmAssessmentType, OpportunityReviewStatus
from ..exceptions import ConfigurationError
from ..llm.assess import assess_subjects
from ..llm.client import LlmClient
from ..reports.metrics_calc import resolve_baseline_window
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .detectors import detect_all
from .gates import evaluate_opportunity_gates
from .impact import priority_from_inputs
from .portfolio import merge_overlapping_actions, select_top_ten


def build_opportunity_portfolio(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    catalogue_version: Optional[str] = None,
    build_id: Optional[str] = None,
    period_end: Optional[date] = None,
    limit: int = 10,
    llm_assist: bool = False,
    llm_client: Optional[LlmClient] = None,
    owner: str = "seo-agent",
    report_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    if not catalogue_version and not build_id:
        raise ConfigurationError("opportunities build requires --catalogue-version or --build-id")

    period_start, end = resolve_baseline_window(store, config, period_end)
    report = report_id or f"opp-v2-{utc_now_iso()}"
    detected = detect_all(
        store,
        period_start=period_start,
        period_end=end,
        catalogue_version=catalogue_version,
        build_id=build_id,
        cannibalization_min_impressions=config.economics.cannibalization_min_impressions,
        cannibalization_min_distinct_urls=config.economics.cannibalization_min_distinct_urls,
    )
    candidates = detected["candidates"]
    # Drop incomplete actionable rows early.
    eligible = []
    rejected = []
    for row in candidates:
        if not row.get("problem") or not row.get("proposed_action") or not row.get("action_type"):
            rejected.append({"reason": "incomplete_action", "row": row.get("source_type")})
            continue
        if not row.get("metric_to_watch"):
            rejected.append({"reason": "missing_metric", "row": row.get("source_type")})
            continue
        # Target required unless reviewed new-page gap.
        if not row.get("target_page") and row.get("action_type") != "new_page":
            if row.get("target_page_status") not in {"approved_new_page", "manual_review", "no_sensible_target"}:
                rejected.append({"reason": "missing_target", "row": row.get("source_type")})
                continue
        # Technical/commerce diagnostics must not invent click-impact estimates.
        if row.get("action_type") in {"technical_fix", "product_mapping", "manual_investigation"}:
            row = dict(row)
            row["expected_incremental_clicks"] = None
            row["impact_score"] = 0.0
            row["impact_estimate"] = "diagnostic_no_click_impact_estimate"
        eligible.append(row)

    merged = merge_overlapping_actions(eligible)
    portfolio = select_top_ten(
        merged["merged"],
        limit=limit,
        minimum_incremental_clicks=config.economics.minimum_incremental_clicks,
        minimum_priority_score=config.economics.minimum_priority_score,
    )

    now = utc_now_iso()
    window = {
        "period_start": period_start.isoformat(),
        "period_end": end.isoformat(),
        "measurement_days": [14, 28, 56],
        "baseline_hint_days": 28,
    }
    selected_rows = []
    for row in portfolio["selected"]:
        item = dict(row)
        pri = priority_from_inputs(
            estimated_incremental_clicks=float(item.get("expected_incremental_clicks") or 0),
            confidence_value=float(item.get("confidence_value") or 0),
            estimated_cost=item.get("estimated_cost"),
            effort_value=item.get("effort_value"),
        )
        item.update(
            {
                "opportunity_id": str(uuid.uuid4()),
                "report_id": report,
                "opportunity_version": "v2",
                "owner": owner,
                "status": "open",
                "review_status": OpportunityReviewStatus.AWAITING_LLM_DIAGNOSIS.value
                if llm_assist
                else OpportunityReviewStatus.AWAITING_HUMAN_REVIEW.value,
                "measurement_window_json": window,
                "priority_score": pri["priority_score"],
                "priority_inputs_json": pri,
                "catalogue_version": catalogue_version,
                "created_at": now,
                "updated_at": now,
                "impact_score": float(item.get("impact_score") or item.get("expected_incremental_clicks") or 0),
                "impact_estimate": item.get("impact_estimate")
                or f"estimated_click_gain={item.get('expected_incremental_clicks')}",
            }
        )
        selected_rows.append(item)

    store.insert_opportunities_v2(selected_rows)

    diagnosis = None
    if llm_assist and selected_rows:
        diagnosis = assess_subjects(
            store,
            config,
            build_id=report,
            assessment_type=LlmAssessmentType.OPPORTUNITY_DIAGNOSIS.value,
            scope="reviewed_shortlist",
            limit=len(selected_rows),
            dry_run=False,
            client=llm_client,
        )
        # Attach valid assessment ids onto opportunities.
        for result in diagnosis.get("results") or []:
            oid = result.get("subject_id")
            aid = result.get("assessment_id")
            if not oid or not aid:
                continue
            if result.get("validation_status") == "valid" or result.get("status") == "assessment_validated":
                store.update_opportunity(
                    oid,
                    {
                        "llm_assessment_id": aid,
                        "review_status": OpportunityReviewStatus.AWAITING_HUMAN_REVIEW.value,
                        "updated_at": utc_now_iso(),
                    },
                )
            else:
                store.update_opportunity(
                    oid,
                    {
                        "llm_assessment_id": aid,
                        "review_status": OpportunityReviewStatus.AWAITING_LLM_DIAGNOSIS.value,
                        "blocked_reason": "llm_diagnosis_invalid_or_unavailable",
                        "updated_at": utc_now_iso(),
                    },
                )

    gates = evaluate_opportunity_gates(
        store,
        report_id=report,
        minimum_incremental_clicks=config.economics.minimum_incremental_clicks,
        minimum_priority_score=config.economics.minimum_priority_score,
    )
    selection_report = {
        "candidates": len(candidates),
        "eligible": len(eligible),
        "rejected_pre_merge": len(rejected),
        "merged": merged["merge_events"],
        "merge_input": merged["input_count"],
        "portfolio": portfolio["counts"],
        "portfolio_rejected": portfolio["rejected"],
        "coverage_warning": portfolio["coverage_warning"],
        "concentration_warning": portfolio.get("concentration_warning"),
        "blocked_detectors": detected["blocked_detectors"],
        "detector_counts": detected["detector_counts"],
        "llm_assist": bool(llm_assist),
        "diagnosis": {
            "created": (diagnosis or {}).get("created"),
            "invalid": (diagnosis or {}).get("invalid"),
            "blocked": (diagnosis or {}).get("blocked"),
        }
        if diagnosis
        else None,
        "gates": gates,
    }
    # Persist report summary onto each selected row (queryable).
    for row in selected_rows:
        store.update_opportunity(
            row["opportunity_id"],
            {"selection_report_json": selection_report, "updated_at": utc_now_iso()},
        )

    return {
        "report_id": report,
        "catalogue_version": catalogue_version,
        "build_id": build_id,
        "period_start": period_start.isoformat(),
        "period_end": end.isoformat(),
        "opportunity_count": len(selected_rows),
        "candidates": len(candidates),
        "eligible": len(eligible),
        "merged_groups": len(merged["merge_events"]),
        "selected": len(selected_rows),
        "blocked_detectors": detected["blocked_detectors"],
        "detector_counts": detected["detector_counts"],
        "coverage_warning": portfolio["coverage_warning"],
        "concentration_warning": portfolio.get("concentration_warning"),
        "gates": gates,
        "opportunities": [
            {
                "opportunity_id": r["opportunity_id"],
                "portfolio_rank": r.get("portfolio_rank"),
                "source_type": r.get("source_type"),
                "action_type": r.get("action_type"),
                "problem": r.get("problem"),
                "target_page": r.get("target_page"),
                "expected_incremental_clicks": r.get("expected_incremental_clicks"),
                "priority_score": r.get("priority_score"),
                "review_status": r.get("review_status"),
            }
            for r in selected_rows
        ],
        "note": "LLM diagnosis is non-authoritative; human approval required before implementation.",
    }
