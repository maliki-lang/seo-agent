"""GA4 landing-page enrichment for catalogue keyword candidates."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import ChannelClass
from ..exceptions import ConfigurationError, DataQualityError
from ..reports.metrics_calc import conversion_rate, engagement_rate
from ..storage import TrackingStore
from ..transforms.normalize import (
    canonical_page_key,
    infer_page_type,
    is_homepage_page_key,
    utc_now_iso,
)
from .scoring import refresh_decision_reason, score_candidate
from .builder import thresholds_from_config

# Homepage organic totals are site-level, not keyword-level. Stamping them onto
# every GSC query whose primary page is "/" inflates selection scores.
GA4_STATUS_MATCHED = "matched"
GA4_STATUS_UNMATCHED = "unmatched"
GA4_STATUS_UNAVAILABLE = "unavailable"
GA4_STATUS_SUPPRESSED_HOMEPAGE = "suppressed_homepage"


def default_ga4_window(store: TrackingStore, *, days: int = 90) -> Tuple[date, date]:
    rows = store.fetchall(
        """
        SELECT MAX(date) AS max_d
        FROM ga4_daily
        WHERE source = 'ga4' AND channel_class = ?
        """,
        (ChannelClass.ORGANIC_SEARCH.value,),
    )
    max_d = rows[0]["max_d"] if rows else None
    if not max_d:
        raise DataQualityError(
            "No production organic ga4_daily rows available for catalogue enrichment."
        )
    end = date.fromisoformat(max_d)
    start = end - timedelta(days=days - 1)
    return start, end


def _aggregate_ga4_by_page(
    store: TrackingStore,
    start: date,
    end: date,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    rows = store.fetchall(
        """
        SELECT
            landing_page,
            run_id,
            sessions,
            engaged_sessions,
            purchases,
            total_revenue,
            source
        FROM ga4_daily
        WHERE date >= ? AND date <= ?
          AND channel_class = ?
          AND LOWER(COALESCE(source, '')) = 'ga4'
        """,
        (start.isoformat(), end.isoformat(), ChannelClass.ORGANIC_SEARCH.value),
    )
    by_page: Dict[str, Dict[str, Any]] = {}
    run_ids = set()
    for row in rows:
        key = canonical_page_key(row["landing_page"])
        if not key:
            continue
        run_ids.add(row["run_id"])
        bucket = by_page.setdefault(
            key,
            {
                "sessions": 0,
                "engaged_sessions": 0,
                "purchases": 0,
                "revenue": Decimal("0"),
                "raw_landings": set(),
            },
        )
        bucket["sessions"] += int(row["sessions"])
        bucket["engaged_sessions"] += int(row["engaged_sessions"])
        bucket["purchases"] += int(row["purchases"])
        bucket["revenue"] += Decimal(str(row["total_revenue"] or "0"))
        bucket["raw_landings"].add(row["landing_page"])
    return by_page, sorted(run_ids)


def _candidate_page_key(cand: Any) -> str:
    page = cand["primary_observed_page"] or cand["proposed_target_page"] or ""
    return canonical_page_key(page)


def _score_reasons_for_status(status: str, reasons: Tuple[str, ...]) -> Tuple[str, ...]:
    if status != GA4_STATUS_SUPPRESSED_HOMEPAGE:
        return reasons
    out: List[str] = []
    replaced = False
    for reason in reasons:
        if reason == "ga4_unavailable":
            out.append("ga4_homepage_fanout_suppressed")
            replaced = True
        else:
            out.append(reason)
    if not replaced:
        out.append("ga4_homepage_fanout_suppressed")
    return tuple(out)


def enrich_build_with_ga4(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    ga4_start: Optional[date] = None,
    ga4_end: Optional[date] = None,
) -> Dict[str, Any]:
    """Attach GA4 organic page metrics to candidates. Unmatched pages stay NULL, not zero.

    Homepage joins are recorded for provenance but suppressed from scoring: site-root
    organic totals must not be treated as keyword-level commercial evidence.
    """
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")
    build = dict(builds[0])

    if ga4_start is None or ga4_end is None:
        default_start, default_end = default_ga4_window(store, days=config.backfill_days or 90)
        ga4_start = ga4_start or default_start
        ga4_end = ga4_end or default_end
    if ga4_start > ga4_end:
        raise ConfigurationError("ga4-start-date must be on or before ga4-end-date")

    by_page, run_ids = _aggregate_ga4_by_page(store, ga4_start, ga4_end)
    candidates = store.fetchall(
        "SELECT * FROM keyword_candidates WHERE build_id = ?",
        (build_id,),
    )
    if not candidates:
        raise DataQualityError(f"No keyword candidates for build_id {build_id}")

    thresholds = thresholds_from_config(config)
    page_keys = [_candidate_page_key(c) for c in candidates]
    page_key_counts = Counter(key for key in page_keys if key)
    eligible_pages = sorted(page_key_counts)
    matched_pages = [page for page in eligible_pages if page in by_page]
    unmatched_pages = [page for page in eligible_pages if page not in by_page]
    if len(matched_pages) + len(unmatched_pages) != len(eligible_pages):
        raise DataQualityError("GA4 join accounting failed: matched + unmatched != eligible pages")

    now = utc_now_iso()
    updated = 0
    matched_candidates = 0
    unmatched_candidates = 0
    suppressed_homepage_candidates = 0
    shared_page_candidates = 0
    for cand, key in zip(candidates, page_keys):
        page = cand["primary_observed_page"] or cand["proposed_target_page"] or ""
        page_type = infer_page_type(page)
        shared = bool(key and page_key_counts[key] > 1)
        if shared:
            shared_page_candidates += 1
        metrics = by_page.get(key) if key else None
        suppress_homepage = bool(key and is_homepage_page_key(key) and metrics is not None)

        if metrics is None:
            unmatched_candidates += 1
            status = GA4_STATUS_UNMATCHED if key else GA4_STATUS_UNAVAILABLE
            ga4_payload = {
                "ga4_organic_sessions": None,
                "ga4_engaged_sessions": None,
                "ga4_purchases": None,
                "ga4_revenue": None,
                "ga4_conversion_rate": None,
                "ga4_engagement_rate": None,
                "ga4_match_status": status,
                "ga4_match_page": key or None,
                "ga4_shared_page": int(shared) if key else None,
                "ga4_value_score": None,
                "page_type": page_type,
            }
            sessions = engaged = purchases = None
            revenue = None
        elif suppress_homepage:
            suppressed_homepage_candidates += 1
            # Provenance only: do not copy homepage rollup into metric columns or scores.
            ga4_payload = {
                "ga4_organic_sessions": None,
                "ga4_engaged_sessions": None,
                "ga4_purchases": None,
                "ga4_revenue": None,
                "ga4_conversion_rate": None,
                "ga4_engagement_rate": None,
                "ga4_match_status": GA4_STATUS_SUPPRESSED_HOMEPAGE,
                "ga4_match_page": key,
                "ga4_shared_page": int(shared),
                "ga4_value_score": None,
                "page_type": page_type,
            }
            sessions = engaged = purchases = None
            revenue = None
        else:
            matched_candidates += 1
            sessions = int(metrics["sessions"])
            engaged = int(metrics["engaged_sessions"])
            purchases = int(metrics["purchases"])
            revenue = Decimal(metrics["revenue"])
            ga4_payload = {
                "ga4_organic_sessions": sessions,
                "ga4_engaged_sessions": engaged,
                "ga4_purchases": purchases,
                "ga4_revenue": format(revenue, "f"),
                "ga4_conversion_rate": conversion_rate(purchases, sessions),
                "ga4_engagement_rate": engagement_rate(engaged, sessions),
                "ga4_match_status": GA4_STATUS_MATCHED,
                "ga4_match_page": key,
                "ga4_shared_page": int(shared),
                "page_type": page_type,
            }

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
            ga4_sessions=sessions,
            ga4_engaged_sessions=engaged,
            ga4_purchases=purchases,
            ga4_revenue=revenue,
            page_type=page_type,
            serper_position=cand["serper_position"],
            proposed_target_ranks=(
                bool(cand["proposed_target_ranks"])
                if "proposed_target_ranks" in cand.keys() and cand["proposed_target_ranks"] is not None
                else None
            ),
            serper_validated=bool(cand["serper_run_id"])
            if "serper_run_id" in cand.keys() and cand["serper_run_id"]
            else False,
        )
        status = ga4_payload["ga4_match_status"]
        score_reasons = _score_reasons_for_status(status, scores.reasons)
        ga4_payload["ga4_value_score"] = scores.ga4_value_score
        ga4_payload["final_selection_score"] = scores.final_selection_score
        ga4_payload["business_relevance_score"] = scores.business_relevance_score
        ga4_payload["gsc_opportunity_score"] = scores.gsc_opportunity_score
        ga4_payload["evidence_confidence_score"] = scores.evidence_confidence_score
        ga4_payload["serper_validation_score"] = scores.serper_validation_score
        ga4_payload["decision_reason"] = refresh_decision_reason(
            cand["decision_reason"], score_reasons
        )
        ga4_payload["updated_at"] = now
        store.update_keyword_candidate(cand["candidate_id"], ga4_payload)
        updated += 1

    report = {
        "eligible_pages": len(eligible_pages),
        "matched_pages": len(matched_pages),
        "unmatched_pages": len(unmatched_pages),
        "matched_page_keys": matched_pages,
        "unmatched_page_keys": unmatched_pages,
        "matched_candidates": matched_candidates,
        "unmatched_candidates": unmatched_candidates,
        "suppressed_homepage_candidates": suppressed_homepage_candidates,
        "shared_page_candidates": shared_page_candidates,
        "accounting_ok": len(matched_pages) + len(unmatched_pages) == len(eligible_pages),
    }
    store.update_catalogue_build(
        build_id,
        {
            "ga4_source_run_ids": run_ids,
            "ga4_window_start": ga4_start.isoformat(),
            "ga4_window_end": ga4_end.isoformat(),
            "ga4_match_report_json": report,
        },
    )
    return {
        "build_id": build_id,
        "ga4_window_start": ga4_start.isoformat(),
        "ga4_window_end": ga4_end.isoformat(),
        "ga4_source_run_ids": run_ids,
        "updated_candidates": updated,
        "match_report": report,
        "note": (
            "Unmatched GA4 joins leave metrics NULL; homepage joins are suppressed "
            "from scoring (site-level rollup is not keyword evidence)."
        ),
    }
