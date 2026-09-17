"""Experiment measurement at 14/28/56-day checkpoints (Phase 16)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Sequence

from ..config import TrackingConfig
from ..enums import ChannelClass, ExperimentStatus
from ..exceptions import DataQualityError
from ..opportunities.targets import canonicalize_target_url
from ..storage import TrackingStore
from ..transforms.normalize import canonical_page_key, utc_now_iso
from .counterfactual import estimate_expected_clicks
from .gates import assert_transition
from .learning import update_action_type_prior
from .outcomes import classify_outcome

Checkpoint = Literal[14, 28, 56]
VALID_CHECKPOINTS = {14, 28, 56}


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _page_matches(stored: str, target: str) -> bool:
    if not stored or not target:
        return False
    return canonical_page_key(stored) == canonical_page_key(target)


def _gsc_nonbrand_agg(
    store: TrackingStore,
    *,
    page: str,
    queries: Sequence[str],
    start: date,
    end: date,
) -> Dict[str, Any]:
    rows = store.fetchall(
        """
        SELECT query, page, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               CASE WHEN SUM(impressions) > 0
                    THEN 1.0 * SUM(clicks) / SUM(impressions) ELSE 0 END AS ctr,
               CASE WHEN SUM(impressions) > 0
                    THEN SUM(position * impressions) / SUM(impressions) ELSE 0 END AS position
        FROM gsc_daily
        WHERE date >= ? AND date <= ? AND is_brand = 0
        GROUP BY query, page
        """,
        (start.isoformat(), end.isoformat()),
    )
    page_clicks = 0.0
    page_impr = 0.0
    query_clicks = 0.0
    query_impr = 0.0
    weighted_pos_num = 0.0
    refs = []
    query_set = {q.lower() for q in queries if q}
    for row in rows:
        if not _page_matches(row["page"], page):
            continue
        clicks = float(row["clicks"] or 0)
        impr = float(row["impressions"] or 0)
        page_clicks += clicks
        page_impr += impr
        weighted_pos_num += float(row["position"] or 0) * impr
        refs.append(
            {
                "grain": "page_query",
                "query": row["query"],
                "page": row["page"],
                "clicks": clicks,
                "impressions": impr,
            }
        )
        if row["query"].lower() in query_set:
            query_clicks += clicks
            query_impr += impr
    # Prefer query-family clicks when queries are specified; else page-level.
    if query_set:
        clicks = query_clicks
        impressions = query_impr
    else:
        clicks = page_clicks
        impressions = page_impr
    ctr = (clicks / impressions) if impressions > 0 else None
    avg_pos = (weighted_pos_num / page_impr) if page_impr > 0 else None
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "average_position": avg_pos,
        "page_clicks": page_clicks,
        "query_family_clicks": query_clicks if query_set else None,
        "source_references": refs[:50],
    }


def _sitewide_nonbrand_clicks(
    store: TrackingStore,
    *,
    exclude_page: str,
    start: date,
    end: date,
) -> float:
    rows = store.fetchall(
        """
        SELECT page, SUM(clicks) AS clicks
        FROM gsc_daily
        WHERE date >= ? AND date <= ? AND is_brand = 0
        GROUP BY page
        """,
        (start.isoformat(), end.isoformat()),
    )
    total = 0.0
    for row in rows:
        if _page_matches(row["page"], exclude_page):
            continue
        total += float(row["clicks"] or 0)
    return total


def _control_clicks(
    store: TrackingStore,
    *,
    page: str,
    start: date,
    end: date,
) -> float:
    agg = _gsc_nonbrand_agg(store, page=page, queries=[], start=start, end=end)
    return float(agg["clicks"])


def _ga4_organic_for_page(
    store: TrackingStore,
    *,
    page: str,
    start: date,
    end: date,
) -> Dict[str, float]:
    rows = store.fetchall(
        """
        SELECT landing_page, SUM(sessions) AS sessions,
               SUM(engaged_sessions) AS engaged_sessions,
               SUM(purchases) AS purchases,
               SUM(CAST(total_revenue AS REAL)) AS revenue
        FROM ga4_daily
        WHERE date >= ? AND date <= ? AND channel_class = ?
        GROUP BY landing_page
        """,
        (start.isoformat(), end.isoformat(), ChannelClass.ORGANIC_SEARCH.value),
    )
    sessions = engaged = purchases = revenue = 0.0
    target_key = canonical_page_key(page)
    for row in rows:
        if canonical_page_key(row["landing_page"]) != target_key:
            continue
        sessions += float(row["sessions"] or 0)
        engaged += float(row["engaged_sessions"] or 0)
        purchases += float(row["purchases"] or 0)
        revenue += float(row["revenue"] or 0)
    return {
        "organic_sessions": sessions,
        "engaged_sessions": engaged,
        "purchases": purchases,
        "revenue": revenue,
    }


def _ai_referral_sessions(
    store: TrackingStore,
    *,
    page: str,
    start: date,
    end: date,
) -> float:
    rows = store.fetchall(
        """
        SELECT landing_page, SUM(sessions) AS sessions
        FROM ga4_daily
        WHERE date >= ? AND date <= ? AND channel_class = ?
        GROUP BY landing_page
        """,
        (start.isoformat(), end.isoformat(), ChannelClass.AI_REFERRAL.value),
    )
    target_key = canonical_page_key(page)
    total = 0.0
    for row in rows:
        if canonical_page_key(row["landing_page"]) == target_key:
            total += float(row["sessions"] or 0)
    return total


def _complete_date_count(store: TrackingStore, table: str, start: date, end: date) -> int:
    row = store.fetchall(
        f"SELECT COUNT(DISTINCT date) AS n FROM {table} WHERE date >= ? AND date <= ?",
        (start.isoformat(), end.isoformat()),
    )[0]
    return int(row["n"] or 0)


def measure_experiment(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    experiment_id: str,
    checkpoint_days: Checkpoint,
    as_of_date: date,
    force: bool = False,
    audit_reason: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    if int(checkpoint_days) not in VALID_CHECKPOINTS:
        raise DataQualityError("checkpoint_days must be 14, 28, or 56")
    experiment = store.get_experiment(experiment_id)
    if not experiment:
        raise DataQualityError(f"Unknown experiment_id: {experiment_id}")
    if not experiment.get("published_at"):
        raise DataQualityError("Experiment must be published before measurement")

    existing = store.fetchall(
        """
        SELECT * FROM experiment_measurements
        WHERE experiment_id = ? AND checkpoint_days = ?
        """,
        (experiment_id, int(checkpoint_days)),
    )
    if existing and not force:
        return {
            "measurement": dict(existing[0]),
            "idempotent": True,
            "note": "existing measurement returned; pass force=True with audit_reason to overwrite",
        }
    if existing and force and not (audit_reason or "").strip():
        raise DataQualityError("force overwrite requires audit_reason")

    published = _parse_date(experiment["published_at"])
    lag = int(config.economics.gsc_complete_lag_days)
    # Exclude incomplete recent GSC days.
    complete_as_of = as_of_date - timedelta(days=lag)
    window_start = published
    window_end = published + timedelta(days=int(checkpoint_days) - 1)
    if window_end > complete_as_of:
        raise DataQualityError(
            f"Insufficient complete GSC days for day-{checkpoint_days} "
            f"(need through {window_end.isoformat()}, complete as of {complete_as_of.isoformat()})"
        )

    baseline_start = _parse_date(experiment["baseline_start"])
    baseline_end = _parse_date(experiment["baseline_end"])
    expected_days = (window_end - window_start).days + 1
    gsc_days = _complete_date_count(store, "gsc_daily", window_start, window_end)
    ga4_days = _complete_date_count(store, "ga4_daily", window_start, window_end)
    baseline_days = _complete_date_count(store, "gsc_daily", baseline_start, baseline_end)

    quality_status = "pass"
    quality_notes = []
    if gsc_days < max(1, int(expected_days * 0.7)):
        quality_status = "fail"
        quality_notes.append(f"sparse_gsc_post={gsc_days}/{expected_days}")
    if baseline_days < 14:
        quality_status = "fail"
        quality_notes.append(f"sparse_gsc_baseline={baseline_days}")
    if ga4_days < max(1, int(expected_days * 0.5)):
        quality_notes.append(f"sparse_ga4_post={ga4_days}/{expected_days}")

    page = canonicalize_target_url(experiment["target_page"]) or experiment["target_page"]
    queries = _load_json(experiment.get("target_queries_json"), [])
    controls = _load_json(experiment.get("control_pages_json"), [])

    baseline = _gsc_nonbrand_agg(
        store, page=page, queries=queries, start=baseline_start, end=baseline_end
    )
    observed = _gsc_nonbrand_agg(
        store, page=page, queries=queries, start=window_start, end=window_end
    )
    control_base = [_control_clicks(store, page=p, start=baseline_start, end=baseline_end) for p in controls]
    control_post = [_control_clicks(store, page=p, start=window_start, end=window_end) for p in controls]
    site_base = _sitewide_nonbrand_clicks(
        store, exclude_page=page, start=baseline_start, end=baseline_end
    )
    site_post = _sitewide_nonbrand_clicks(
        store, exclude_page=page, start=window_start, end=window_end
    )

    counterfactual = estimate_expected_clicks(
        target_baseline_clicks=float(baseline["clicks"]),
        target_observed_clicks=float(observed["clicks"]),
        control_baseline_clicks=control_base,
        control_post_clicks=control_post,
        sitewide_baseline_clicks=site_base,
        sitewide_post_clicks=site_post,
    )

    if float(baseline["clicks"]) <= 0:
        confidence = "low"
        quality_notes.append("zero_baseline_clicks")
    elif counterfactual.get("confidence_cap") == "low":
        confidence = "low"
    elif quality_status != "pass":
        confidence = "low"
    elif gsc_days >= expected_days - 1:
        confidence = "high"
    else:
        confidence = "medium"

    ga4 = _ga4_organic_for_page(store, page=page, start=window_start, end=window_end)
    ai_sessions = _ai_referral_sessions(store, page=page, start=window_start, end=window_end)
    metadata = _load_json(experiment.get("metadata_json"), {})
    isolation = metadata.get("isolation_quality") or "high"

    classification = classify_outcome(
        checkpoint_days=int(checkpoint_days),
        adjusted_incremental_clicks=counterfactual["adjusted_incremental_clicks"],
        baseline_clicks=float(baseline["clicks"]),
        observed_clicks=float(observed["clicks"]),
        confidence_label=confidence,
        quality_status=quality_status,
        isolation_quality=isolation,
        actual_cost=float(experiment.get("actual_cost") or 0),
        economics=config.economics,
    )

    now = utc_now_iso()
    assumptions = list(counterfactual.get("assumptions") or [])
    assumptions.extend(quality_notes)
    if audit_reason:
        assumptions.append(f"force_overwrite_reason={audit_reason}")

    measurement_id = (
        existing[0]["measurement_id"] if existing else str(uuid.uuid4())
    )
    row = {
        "measurement_id": measurement_id,
        "experiment_id": experiment_id,
        "checkpoint_days": int(checkpoint_days),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "baseline_clicks": float(baseline["clicks"]),
        "observed_clicks": float(observed["clicks"]),
        "expected_clicks_without_change": counterfactual["expected_clicks_without_change"],
        "adjusted_incremental_clicks": counterfactual["adjusted_incremental_clicks"],
        "sitewide_trend_factor": counterfactual.get("sitewide_trend_factor"),
        "control_trend_factor": counterfactual.get("control_trend_factor"),
        "nonbranded_impressions": float(observed["impressions"]),
        "ctr": observed.get("ctr"),
        "average_position": observed.get("average_position"),
        "organic_sessions": ga4["organic_sessions"],
        "engaged_sessions": ga4["engaged_sessions"],
        "purchases": ga4["purchases"],
        "revenue": ga4["revenue"],
        "ai_referral_sessions": ai_sessions,
        "confidence_label": confidence,
        "outcome": classification["outcome"],
        "adjustment_method": counterfactual["method"],
        "source_references_json": {
            "page_level": {"clicks": baseline.get("page_clicks"), "post_clicks": observed.get("page_clicks")},
            "query_family": {
                "baseline": baseline.get("query_family_clicks"),
                "observed": observed.get("query_family_clicks"),
            },
            "rows": (baseline.get("source_references") or [])[:20]
            + (observed.get("source_references") or [])[:20],
            "classification_reasons": classification.get("reasons"),
            "actual_cost_per_incremental_click": classification.get(
                "actual_cost_per_incremental_click"
            ),
        },
        "assumptions_json": assumptions,
        "quality_status": quality_status,
        "calculated_at": now,
    }
    store.upsert_experiment_measurement(row)

    # Move experiment status.
    current = experiment["status"]
    if current == ExperimentStatus.PUBLISHED.value:
        assert_transition(current, ExperimentStatus.MEASURING.value)
        new_status = ExperimentStatus.MEASURING.value
    else:
        new_status = current
    if classification.get("finalizable") and int(checkpoint_days) >= 28:
        outcome_status = classification["outcome"]
        if outcome_status in {
            ExperimentStatus.WINNER.value,
            ExperimentStatus.LIKELY_WINNER.value,
            ExperimentStatus.INCONCLUSIVE.value,
            ExperimentStatus.LIKELY_LOSS.value,
            ExperimentStatus.TRACKING_FAILURE.value,
        }:
            assert_transition(ExperimentStatus.MEASURING.value, outcome_status)
            new_status = outcome_status

    store.update_experiment(
        experiment_id,
        {
            "status": new_status,
            "counterfactual_method": counterfactual["method"],
            "updated_at": now,
        },
    )

    prior = None
    if classification.get("finalizable"):
        prior = update_action_type_prior(
            store, config, action_type=experiment["action_type"]
        )

    return {
        "measurement": store.get_experiment_measurement(experiment_id, int(checkpoint_days)),
        "classification": classification,
        "experiment_status": new_status,
        "prior": prior,
        "idempotent": False,
        "estimated_cost_per_incremental_click": (
            None
            if not experiment.get("estimated_cost")
            or not experiment.get("estimated_incremental_clicks")
            or float(experiment.get("estimated_incremental_clicks") or 0) <= 0
            else round(
                float(experiment["estimated_cost"])
                / float(experiment["estimated_incremental_clicks"]),
                6,
            )
        ),
        "actual_cost_per_incremental_click": classification.get(
            "actual_cost_per_incremental_click"
        ),
    }
