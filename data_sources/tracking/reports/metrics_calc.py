from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import ChannelClass, Engine
from ..storage import TrackingStore
from ..transforms.ai_parser import stable_rates
from ..transforms.normalize import canonical_json, sha256_hex


METRIC_QUERY_VERSION = "baseline_metrics_v1"


def weighted_ctr(clicks: int, impressions: int) -> Optional[float]:
    if impressions <= 0:
        return None
    return clicks / impressions


def impression_weighted_position(rows: List[Dict[str, Any]]) -> Optional[float]:
    total_impr = 0
    weighted = 0.0
    for row in rows:
        impr = int(row.get("impressions") or 0)
        pos = float(row.get("position") or 0)
        if impr <= 0:
            continue
        total_impr += impr
        weighted += pos * impr
    if total_impr <= 0:
        return None
    return weighted / total_impr


def engagement_rate(engaged: int, sessions: int) -> Optional[float]:
    if sessions <= 0:
        return None
    return engaged / sessions


def conversion_rate(purchases: int, sessions: int) -> Optional[float]:
    if sessions <= 0:
        return None
    return purchases / sessions


def visibility_buckets(positions: List[int]) -> Dict[str, Any]:
    total = len(positions)
    top3 = sum(1 for p in positions if 1 <= p <= 3)
    top10 = sum(1 for p in positions if 1 <= p <= 10)
    top20 = sum(1 for p in positions if 1 <= p <= 20)
    absent = sum(1 for p in positions if p == 0 or p > 20)
    return {
        "total": total,
        "top3_count": top3,
        "top10_count": top10,
        "top20_count": top20,
        "absent_count": absent,
        "top3_rate": (top3 / total) if total else None,
        "top10_rate": (top10 / total) if total else None,
        "top20_rate": (top20 / total) if total else None,
        "absent_rate": (absent / total) if total else None,
    }


def compute_period_metrics(
    store: TrackingStore,
    period_start: date,
    period_end: date,
) -> Tuple[List[Dict[str, Any]], str]:
    """Return metric rows and an input fingerprint for the window."""
    start_s = period_start.isoformat()
    end_s = period_end.isoformat()
    metrics: List[Dict[str, Any]] = []

    gsc_rows = store.fetchall(
        """
        SELECT clicks, impressions, position, is_brand
        FROM gsc_daily WHERE date >= ? AND date <= ?
        """,
        (start_s, end_s),
    )
    clicks = sum(int(r["clicks"]) for r in gsc_rows)
    impressions = sum(int(r["impressions"]) for r in gsc_rows)
    brand_clicks = sum(int(r["clicks"]) for r in gsc_rows if r["is_brand"])
    brand_impr = sum(int(r["impressions"]) for r in gsc_rows if r["is_brand"])
    nonbrand_clicks = clicks - brand_clicks
    nonbrand_impr = impressions - brand_impr
    gsc_payload = [dict(r) for r in gsc_rows]
    metrics.extend(
        [
            _metric("gsc_clicks", clicks, segment={}),
            _metric("gsc_impressions", impressions, segment={}),
            _metric(
                "gsc_weighted_ctr",
                weighted_ctr(clicks, impressions),
                numerator=clicks,
                denominator=impressions,
                segment={},
            ),
            _metric(
                "gsc_weighted_avg_position",
                impression_weighted_position(gsc_payload),
                segment={},
            ),
            _metric("gsc_branded_clicks", brand_clicks, segment={"brand": True}),
            _metric("gsc_branded_impressions", brand_impr, segment={"brand": True}),
            _metric("gsc_nonbranded_clicks", nonbrand_clicks, segment={"brand": False}),
            _metric("gsc_nonbranded_impressions", nonbrand_impr, segment={"brand": False}),
        ]
    )

    ga4_rows = store.fetchall(
        """
        SELECT sessions, engaged_sessions, purchases, total_revenue, channel_class
        FROM ga4_daily WHERE date >= ? AND date <= ?
        """,
        (start_s, end_s),
    )
    organic = [r for r in ga4_rows if r["channel_class"] == ChannelClass.ORGANIC_SEARCH.value]
    org_sessions = sum(int(r["sessions"]) for r in organic)
    org_engaged = sum(int(r["engaged_sessions"]) for r in organic)
    org_purchases = sum(int(r["purchases"]) for r in organic)
    org_revenue = sum(Decimal(str(r["total_revenue"] or "0")) for r in organic)
    metrics.extend(
        [
            _metric("ga4_organic_sessions", org_sessions, segment={"channel": "organic_search"}),
            _metric("ga4_organic_engaged_sessions", org_engaged, segment={"channel": "organic_search"}),
            _metric("ga4_organic_purchases", org_purchases, segment={"channel": "organic_search"}),
            _metric(
                "ga4_organic_revenue",
                format(org_revenue, "f"),
                segment={"channel": "organic_search"},
            ),
            _metric(
                "ga4_organic_engagement_rate",
                engagement_rate(org_engaged, org_sessions),
                numerator=org_engaged,
                denominator=org_sessions,
                segment={"channel": "organic_search"},
            ),
            _metric(
                "ga4_organic_conversion_rate",
                conversion_rate(org_purchases, org_sessions),
                numerator=org_purchases,
                denominator=org_sessions,
                segment={"channel": "organic_search"},
            ),
        ]
    )

    # Latest SERP snapshot on or before period_end
    serp_rows = store.fetchall(
        """
        SELECT sunnystep_position FROM serp_daily
        WHERE date = (
            SELECT MAX(date) FROM serp_daily WHERE date >= ? AND date <= ?
        )
        """,
        (start_s, end_s),
    )
    buckets = visibility_buckets([int(r["sunnystep_position"]) for r in serp_rows])
    for key, value in buckets.items():
        metrics.append(_metric(f"serp_visibility_{key}", value, segment={}))

    ai_rows = store.fetchall(
        """
        SELECT question_id, engine, mentioned_sunnystep, cited_urls, repetition_number
        FROM ai_answer_runs
        WHERE as_of_date >= ? AND as_of_date <= ?
        """,
        (start_s, end_s),
    )
    parsed = []
    for row in ai_rows:
        parsed.append(
            type(
                "R",
                (),
                {
                    "question_id": row["question_id"],
                    "engine": row["engine"],
                    "mentioned_sunnystep": bool(row["mentioned_sunnystep"]),
                    "cited_urls": json.loads(row["cited_urls"] or "[]"),
                    "repetition_number": row["repetition_number"],
                },
            )()
        )
    rates = stable_rates(parsed)
    metrics.append(_metric("geo_combined_mention_rate", rates["mention_rate"], segment={"engine": "combined"}))
    metrics.append(_metric("geo_combined_citation_rate", rates["citation_rate"], segment={"engine": "combined"}))
    for engine in (Engine.CHATGPT.value, Engine.PERPLEXITY.value):
        engine_rates = (rates.get("engines") or {}).get(engine) or {}
        metrics.append(
            _metric(
                "geo_mention_rate",
                engine_rates.get("mention_rate"),
                segment={"engine": engine},
                notes="stable majority (>=2/3) of complete repetition sets",
            )
        )
        metrics.append(
            _metric(
                "geo_citation_rate",
                engine_rates.get("citation_rate"),
                segment={"engine": engine},
                notes="stable majority (>=2/3) of complete repetition sets",
            )
        )

    fingerprint_payload = {
        "period_start": start_s,
        "period_end": end_s,
        "gsc_rows": len(gsc_rows),
        "ga4_rows": len(ga4_rows),
        "serp_rows": len(serp_rows),
        "ai_rows": len(ai_rows),
        "metric_count": len(metrics),
        "version": METRIC_QUERY_VERSION,
    }
    fingerprint = sha256_hex(canonical_json(fingerprint_payload))
    for item in metrics:
        item["source_query_version"] = METRIC_QUERY_VERSION
        item["input_fingerprint"] = fingerprint
    return metrics, fingerprint


def _metric(
    name: str,
    value: Any,
    *,
    segment: Dict[str, Any],
    numerator: Any = None,
    denominator: Any = None,
    notes: str = "",
) -> Dict[str, Any]:
    if value is None:
        metric_value = None
    elif isinstance(value, float):
        metric_value = f"{value:.6f}"
    else:
        metric_value = str(value)
    return {
        "metric_name": name,
        "segment_json": json.dumps(segment, sort_keys=True),
        "metric_value": metric_value,
        "numerator": None if numerator is None else str(numerator),
        "denominator": None if denominator is None else str(denominator),
        "notes": notes,
    }


def resolve_baseline_window(
    store: TrackingStore,
    config: TrackingConfig,
    end_date: Optional[date] = None,
) -> Tuple[date, date]:
    """Latest complete 28-day window ending on freshest available date."""
    days = config.baseline_days
    candidates = []
    for table, column in (("gsc_daily", "date"), ("ga4_daily", "date"), ("serp_daily", "date")):
        if store.count(table) == 0:
            continue
        row = store.fetchall(f"SELECT MAX({column}) AS d FROM {table}")[0]
        if row["d"]:
            candidates.append(date.fromisoformat(row["d"]))
    if end_date is not None:
        period_end = end_date
    elif candidates:
        period_end = min(candidates)
    else:
        period_end = date.today()
    period_start = period_end - timedelta(days=days - 1)
    return period_start, period_end
