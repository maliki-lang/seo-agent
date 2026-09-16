"""Explicit Serper validation for shortlisted catalogue keyword candidates."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..collectors.serper import (
    DEFAULT_QUERY_COST,
    detect_ai_overview,
    redact_serper_payload,
    sunnystep_position,
)
from ..config import TrackingConfig
from ..costs import CostLedger
from ..enums import CandidateDecision, RunStatus, RunType, Source
from ..exceptions import ConfigurationError, CostLimitExceeded, DataQualityError
from ..models import RunLog
from ..storage import TrackingStore
from ..transforms.normalize import (
    canonical_page_key,
    canonicalize_url,
    normalize_host,
    utc_now_iso,
)
from .builder import thresholds_from_config
from .policy import policy_from_config
from .scoring import (
    refresh_decision_reason,
    score_candidate,
    score_candidate_v2,
    score_v2_update_fields,
    serp_component_scores,
)

SearchFn = Callable[[str], Dict[str, Any]]
SUNNYSTEP_HOSTS = {"sunnystep.com", "gosunnystep.myshopify.com"}


def _sunnystep_ranking_url(organic: Sequence[Dict[str, Any]]) -> str:
    for item in organic:
        url = item.get("link") or item.get("url") or ""
        host = normalize_host(url)
        if any(host == h or host.endswith("." + h) for h in SUNNYSTEP_HOSTS):
            return canonicalize_url(url) or url
    return ""


def _result_types(organic: Sequence[Dict[str, Any]]) -> List[str]:
    types: List[str] = []
    for item in organic[:10]:
        # Serper may expose type-like fields; only record deterministic present values.
        for key in ("type", "resultType", "kind"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                types.append(value.strip().lower())
                break
        else:
            types.append("organic")
    return types


def _proposed_target_ranks(organic: Sequence[Dict[str, Any]], proposed: str) -> bool:
    target = canonical_page_key(proposed)
    if not target:
        return False
    for item in organic:
        url = item.get("link") or item.get("url") or ""
        if canonical_page_key(url) == target:
            return True
    return False


def validate_serp_for_build(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    decision: str = CandidateDecision.PENDING.value,
    limit: int = 0,
    pool: str = "decision",
    search_fn: Optional[SearchFn] = None,
    cost_ledger: Optional[CostLedger] = None,
    as_of: Optional[date] = None,
    require_serper_for_v2: bool = True,
) -> Dict[str, Any]:
    """Paid Serper validation for shortlisted catalogue candidates. Explicit command only.

    pool:
      - decision: filter by decision value (legacy Phase 7 behavior)
      - preselected: validate serp_preselected=1 family primaries (Phase 12)
    """
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")

    pool_mode = (pool or "decision").strip().lower()
    if pool_mode not in {"decision", "preselected"}:
        raise ConfigurationError("pool must be 'decision' or 'preselected'")

    if pool_mode == "preselected":
        sql = """
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND serp_preselected = 1
            ORDER BY serp_preselect_rank ASC, final_selection_score DESC, gsc_impressions DESC, normalized_keyword
        """
        params: List[Any] = [build_id]
    else:
        if decision not in {d.value for d in CandidateDecision}:
            raise ConfigurationError(f"Invalid decision filter: {decision}")
        sql = """
            SELECT * FROM keyword_candidates
            WHERE build_id = ? AND decision = ?
            ORDER BY final_selection_score DESC, gsc_impressions DESC, normalized_keyword
        """
        params = [build_id, decision]

    candidates = [dict(r) for r in store.fetchall(sql, params)]
    if limit and limit > 0:
        candidates = candidates[:limit]
    if not candidates:
        return {
            "build_id": build_id,
            "decision": decision,
            "pool": pool_mode,
            "validated": 0,
            "failed": 0,
            "skipped": 0,
            "note": "No candidates matched the pool/decision filter.",
        }

    ledger = cost_ledger or CostLedger(config.daily_cost_cap_usd)
    if search_fn is None:
        if not config.serper_api_key:
            raise ConfigurationError("SERPER_API_KEY is required for catalogue validate-serp")
        from data_sources.modules.serper import SerperClient

        client = SerperClient(api_key=config.serper_api_key, timeout_seconds=30)

        def _live(keyword: str) -> Dict[str, Any]:
            return client._search(
                keyword,
                gl=config.serper_gl,
                hl=config.serper_hl,
                num=config.serper_results_limit,
                location=config.serper_location or None,
            )

        search = _live
    else:
        search = search_fn

    as_of_date = as_of or date.today()
    run_id = str(uuid.uuid4())
    now = utc_now_iso()
    store.insert_run(
        RunLog(
            run_id=run_id,
            run_type=RunType.MANUAL,
            as_of_date=as_of_date,
            started_at=now,
            status=RunStatus.RUNNING,
            requested_collectors=[Source.SERPER.value],
            code_version="catalogue_validate_serp",
            config_fingerprint=config.fingerprint(),
        )
    )

    thresholds = thresholds_from_config(config)
    selection_policy = policy_from_config(config)
    validated = 0
    failed = 0
    blocked = 0
    details: List[Dict[str, Any]] = []
    raw_count = 0
    cost = Decimal("0")

    try:
        for cand in candidates:
            try:
                ledger.add(DEFAULT_QUERY_COST, source="catalogue_validate_serp")
            except CostLimitExceeded as exc:
                blocked += 1
                details.append(
                    {
                        "candidate_id": cand["candidate_id"],
                        "status": "blocked",
                        "reason": str(exc),
                    }
                )
                # Record explicit blocked state for missing Serper on this and remaining rows.
                store.update_keyword_candidate(
                    cand["candidate_id"],
                    {
                        "selection_reasons_json": {
                            "reasons": ["serper_blocked_by_cost_cap"],
                            "hard_excluded": False,
                            "exclusion_reasons": [],
                        },
                        "updated_at": utc_now_iso(),
                    },
                )
                break

            try:
                payload = search(cand["canonical_keyword"])
            except Exception as exc:  # noqa: BLE001 - record per-candidate failure
                failed += 1
                details.append(
                    {
                        "candidate_id": cand["candidate_id"],
                        "status": "failed",
                        "reason": exc.__class__.__name__,
                    }
                )
                store.update_keyword_candidate(
                    cand["candidate_id"],
                    {
                        "selection_reasons_json": {
                            "reasons": [f"serper_failed:{exc.__class__.__name__}"],
                            "hard_excluded": False,
                            "exclusion_reasons": [],
                        },
                        "updated_at": utc_now_iso(),
                    },
                )
                continue

            cost += DEFAULT_QUERY_COST
            organic = payload.get("organic") or []
            position = sunnystep_position(organic)
            ranking_url = _sunnystep_ranking_url(organic)
            domains = []
            for item in organic[:10]:
                host = normalize_host(item.get("link") or item.get("url") or "")
                if host:
                    domains.append(host)
            aio_status, _citations = detect_ai_overview(payload)
            proposed = cand["proposed_target_page"] or cand["primary_observed_page"] or ""
            target_ranks = _proposed_target_ranks(organic, proposed)
            result_types = _result_types(organic)

            raw_id = str(uuid.uuid4())
            store.execute(
                """
                INSERT INTO raw_records(
                    raw_record_id, run_id, source, endpoint_or_operation,
                    request_fingerprint, payload_json, content_type, received_at,
                    retention_class, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_id,
                    run_id,
                    Source.SERPER.value,
                    "catalogue.validate_serp",
                    cand["candidate_id"],
                    json.dumps(redact_serper_payload(payload), sort_keys=True),
                    "application/json",
                    now,
                    "standard",
                    raw_id,
                ),
            )
            raw_count += 1

            scores = score_candidate(
                clicks=int(cand["gsc_clicks"]),
                impressions=int(cand["gsc_impressions"]),
                weighted_position=cand["gsc_weighted_position"],
                weighted_ctr=cand["gsc_weighted_ctr"],
                multi_page=bool(cand["multi_page_competition"]),
                normalized_keyword=cand["normalized_keyword"],
                source_row_count=int(cand["source_row_count"]),
                source_date_count=int(cand["source_date_count"]),
                relevance_terms=thresholds.relevance_terms,
                min_impressions=thresholds.min_impressions,
                ga4_sessions=cand["ga4_organic_sessions"],
                ga4_engaged_sessions=cand["ga4_engaged_sessions"],
                ga4_purchases=cand["ga4_purchases"],
                ga4_revenue=Decimal(str(cand["ga4_revenue"])) if cand["ga4_revenue"] is not None else None,
                page_type=cand["page_type"] or "other",
                serper_position=position,
                proposed_target_ranks=target_ranks,
                serper_validated=True,
            )
            vis, serp_opp, align, serp_conf, feasibility, _serp_reasons = serp_component_scores(
                position=position,
                proposed_target_ranks=target_ranks,
                validated=True,
                target_actionability=float(cand.get("target_actionability_score") or 0.0),
            )
            enriched = dict(cand)
            enriched.update(
                {
                    "serper_position": position,
                    "serper_run_id": run_id,
                    "serper_collected_at": now,
                    "proposed_target_ranks": int(target_ranks),
                    "serper_validation_score": scores.serper_validation_score,
                }
            )
            v2 = score_candidate_v2(
                enriched,
                score_weights=selection_policy.score_weights,
                penalties=selection_policy.penalties,
                min_impressions=selection_policy.min_impressions,
                require_serper=require_serper_for_v2 and pool_mode == "preselected",
            )
            v2_fields = score_v2_update_fields(v2)
            # Ensure separated Serper components from this validation win.
            v2_fields.update(
                {
                    "serp_visibility_score": vis,
                    "serp_opportunity_score": serp_opp,
                    "serp_target_alignment_score": align,
                    "serp_validation_confidence": serp_conf,
                    "serp_feasibility_score": feasibility,
                }
            )
            store.update_keyword_candidate(
                cand["candidate_id"],
                {
                    "serper_position": position,
                    "serper_ranking_url": ranking_url or None,
                    "serper_top_10_domains": domains,
                    "serper_intent": None,
                    "serper_validation_score": scores.serper_validation_score,
                    "serper_run_id": run_id,
                    "serper_collected_at": now,
                    "proposed_target_ranks": int(target_ranks),
                    "serper_ai_overview_status": aio_status.value,
                    "serper_result_types_json": result_types,
                    "final_selection_score": scores.final_selection_score,
                    "decision_reason": refresh_decision_reason(
                        cand["decision_reason"], scores.reasons
                    ),
                    "updated_at": now,
                    **v2_fields,
                },
            )
            validated += 1
            details.append(
                {
                    "candidate_id": cand["candidate_id"],
                    "status": "validated",
                    "sunnystep_position": position,
                    "proposed_target_ranks": target_ranks,
                    "ai_overview_status": aio_status.value,
                    "selection_score_v2": v2.selection_score_v2,
                    "serp_visibility_score": vis,
                    "serp_opportunity_score": serp_opp,
                }
            )

        status = RunStatus.SUCCEEDED
        if failed and validated:
            status = RunStatus.PARTIAL
        elif failed and not validated:
            status = RunStatus.FAILED
        if blocked and not validated:
            status = RunStatus.FAILED
        store.update_run(
            run_id,
            status=status,
            finished_at=utc_now_iso(),
            completed_collectors=[Source.SERPER.value] if validated else [],
            failed_collectors={"serper": "candidate_failures"} if failed else {},
            row_counts={"serper_validations": validated, "raw_records": raw_count},
            cost_usd=cost,
        )
    except Exception:
        store.update_run(
            run_id,
            status=RunStatus.FAILED,
            finished_at=utc_now_iso(),
            error_code="SerperValidationError",
            error_message="catalogue validate-serp failed",
        )
        raise

    required = len(candidates)
    report = {
        "pool": pool_mode,
        "required_candidates": required,
        "validated": validated,
        "failed": failed,
        "blocked_by_cost_cap": blocked,
        "coverage_ok": validated + failed + blocked >= required and blocked == 0 and failed == 0,
        "details": details,
    }
    existing_runs = []
    try:
        existing = json.loads(builds[0]["serper_source_run_ids"] or "[]")
        if isinstance(existing, list):
            existing_runs = existing
    except (TypeError, json.JSONDecodeError):
        existing_runs = []
    if run_id not in existing_runs:
        existing_runs.append(run_id)
    store.update_catalogue_build(
        build_id,
        {
            "serper_source_run_ids": existing_runs,
            "serper_validation_report_json": report,
        },
    )
    return {
        "build_id": build_id,
        "decision": decision if pool_mode == "decision" else None,
        "pool": pool_mode,
        "serper_run_id": run_id,
        "cost_usd": str(cost),
        "validated": validated,
        "failed": failed,
        "blocked": blocked,
        "report": report,
        "note": "Serper validates current SERP visibility only; it is not search-volume evidence.",
    }
