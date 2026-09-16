from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import OpportunityCategory
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .metrics_calc import resolve_baseline_window

# Approved CTR curve for impact estimates (not guarantees).
EXPECTED_CTR = {
    1: 0.316,
    2: 0.157,
    3: 0.105,
    4: 0.075,
    5: 0.059,
    6: 0.048,
    7: 0.041,
    8: 0.035,
    9: 0.031,
    10: 0.027,
    11: 0.018,
    12: 0.015,
    13: 0.013,
    14: 0.012,
    15: 0.011,
    16: 0.010,
    17: 0.009,
    18: 0.008,
    19: 0.008,
    20: 0.007,
}

CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
EFFORT = {"S": 1.0, "M": 2.0, "L": 3.0}
IMPACT_SCALE_MAX = 100.0


def expected_ctr(position: int) -> float:
    if position <= 0:
        return 0.0
    if position in EXPECTED_CTR:
        return EXPECTED_CTR[position]
    if position > 20:
        return 0.003
    return EXPECTED_CTR.get(min(EXPECTED_CTR.keys(), key=lambda k: abs(k - position)), 0.01)


def normalize_seo_impact(click_gain: float, max_gain: float) -> float:
    if max_gain <= 0:
        return 0.0
    return max(0.0, min(IMPACT_SCALE_MAX, (click_gain / max_gain) * IMPACT_SCALE_MAX))


def normalize_geo_impact(mention_gain: float, catalogue_size: int) -> float:
    if catalogue_size <= 0:
        return 0.0
    return max(0.0, min(IMPACT_SCALE_MAX, (mention_gain / catalogue_size) * IMPACT_SCALE_MAX))


def priority_score(impact: float, confidence: float, effort: float) -> float:
    if effort <= 0:
        return 0.0
    return round(impact * confidence / effort, 6)


class OpportunityBuilder:
    def __init__(self, config: TrackingConfig, store: TrackingStore):
        self.config = config
        self.store = store

    def build(
        self,
        *,
        report_id: str,
        end_date: Optional[date] = None,
        limit: int = 10,
        owner: str = "seo-agent",
    ) -> List[Dict[str, Any]]:
        period_start, period_end = resolve_baseline_window(self.store, self.config, end_date)
        seo = self._seo_opportunities(period_start, period_end)
        geo = self._geo_opportunities(period_start, period_end)
        combined = seo + geo
        combined.sort(
            key=lambda row: (
                -float(row["priority_score"]),
                row["category"],
                row["target_query_or_question"],
                row["target_page"],
            )
        )
        # Consolidate overlapping target pages: keep highest score per page+category.
        seen = set()
        selected: List[Dict[str, Any]] = []
        for row in combined:
            key = (row["category"], row["target_page"])
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) >= limit:
                break
        created_at = utc_now_iso()
        for row in selected:
            row["opportunity_id"] = str(uuid.uuid4())
            row["report_id"] = report_id
            row["owner"] = owner
            row["status"] = "open"
            row["created_at"] = created_at
        self.store.insert_opportunities(selected)
        return selected

    def _seo_opportunities(self, start: date, end: date) -> List[Dict[str, Any]]:
        rows = self.store.fetchall(
            """
            SELECT s.natural_key, s.keyword, s.keyword_id, s.target_page, s.cluster,
                   s.sunnystep_position, s.ai_overview_status,
                   COALESCE(g.impressions, 0) AS impressions,
                   COALESCE(g.clicks, 0) AS clicks
            FROM serp_daily s
            LEFT JOIN (
                SELECT query, SUM(impressions) AS impressions, SUM(clicks) AS clicks
                FROM gsc_daily
                WHERE date >= ? AND date <= ?
                GROUP BY query
            ) g ON lower(g.query) = lower(s.keyword)
            WHERE s.date = (
                SELECT MAX(date) FROM serp_daily WHERE date >= ? AND date <= ?
            )
            """,
            (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()),
        )
        candidates = []
        for row in rows:
            position = int(row["sunnystep_position"] or 0)
            impressions = int(row["impressions"] or 0)
            clicks = int(row["clicks"] or 0)
            if position == 0:
                target_rank = 10
                effort = "L"
                confidence = "low"
                problem = "Tracked keyword has no Sunnystep result in inspected SERP."
                action = "Improve or create the existing target page and earn a first-page ranking."
            elif 4 <= position <= 20:
                target_rank = 3 if position <= 10 else 10
                effort = "M" if position <= 10 else "L"
                confidence = "medium"
                problem = f"Tracked keyword ranks at position {position}, below top-3 visibility."
                action = "Refresh on-page SEO and internal links on the existing target page."
            elif 1 <= position <= 3:
                continue
            else:
                continue
            expected_clicks = impressions * expected_ctr(target_rank)
            gain = max(0.0, expected_clicks - clicks)
            conf_value = CONFIDENCE[confidence]
            if row["ai_overview_status"] == "unverified":
                conf_value *= 0.9
                confidence = "medium" if confidence == "high" else confidence
            if impressions < 20:
                conf_value = min(conf_value, CONFIDENCE["low"])
                confidence = "low"
            candidates.append(
                {
                    "category": OpportunityCategory.SEO.value,
                    "problem": problem,
                    "supporting_evidence_json": {
                        "position": position,
                        "impressions": impressions,
                        "clicks": clicks,
                        "target_rank": target_rank,
                        "expected_ctr": expected_ctr(target_rank),
                        "estimated_click_gain": round(gain, 3),
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                    },
                    "source_row_references_json": [
                        {"table": "serp_daily", "natural_key": row["natural_key"], "keyword_id": row["keyword_id"]}
                    ],
                    "target_query_or_question": row["keyword"],
                    "target_page": row["target_page"],
                    "proposed_action": action,
                    "impact_estimate": f"estimated_click_gain={gain:.2f}",
                    "raw_impact": gain,
                    "confidence_label": confidence,
                    "confidence_value": conf_value,
                    "effort_label": effort,
                    "effort_value": EFFORT[effort],
                    "metric_to_watch": "gsc_clicks + serp_position",
                }
            )
        max_gain = max((c["raw_impact"] for c in candidates), default=0.0) or 1.0
        out = []
        for item in candidates:
            impact = normalize_seo_impact(item.pop("raw_impact"), max_gain)
            item["impact_score"] = round(impact, 4)
            item["priority_score"] = priority_score(
                impact, item["confidence_value"], item["effort_value"]
            )
            item["supporting_evidence_json"] = json.dumps(item["supporting_evidence_json"], sort_keys=True)
            item["source_row_references_json"] = json.dumps(item["source_row_references_json"], sort_keys=True)
            out.append(item)
        return out

    def _geo_opportunities(self, start: date, end: date) -> List[Dict[str, Any]]:
        rows = self.store.fetchall(
            """
            SELECT natural_key, question_id, question, target_page, engine,
                   mentioned_sunnystep, cited_urls, repetition_number
            FROM ai_answer_runs
            WHERE as_of_date = (
                SELECT MAX(as_of_date) FROM ai_answer_runs
                WHERE as_of_date >= ? AND as_of_date <= ?
            )
            """,
            (start.isoformat(), end.isoformat()),
        )
        grouped: Dict[Tuple[str, str], List[Any]] = {}
        for row in rows:
            key = (row["question_id"], row["engine"])
            grouped.setdefault(key, []).append(row)
        catalogue_size = max(1, len({row["question_id"] for row in rows}) or 1)
        candidates = []
        for (question_id, engine), items in grouped.items():
            if len(items) < 3:
                continue
            mentions = sum(1 for item in items if item["mentioned_sunnystep"])
            citations = 0
            for item in items:
                try:
                    urls = json.loads(item["cited_urls"] or "[]")
                except json.JSONDecodeError:
                    urls = []
                if any("sunnystep.com" in (url or "") for url in urls):
                    citations += 1
            if mentions >= 2 and citations >= 2:
                continue
            confidence = "medium" if len(items) >= 3 else "low"
            conf_value = CONFIDENCE[confidence]
            if mentions == 0:
                conf_value = CONFIDENCE["low"]
                confidence = "low"
            mention_gain = 1.0 if mentions < 2 else 0.0
            citation_gain = 1.0 if citations < 2 else 0.0
            raw = mention_gain + citation_gain
            sample = items[0]
            candidates.append(
                {
                    "category": OpportunityCategory.GEO.value,
                    "problem": (
                        f"{engine} answers for this question lack stable Sunnystep "
                        f"mention/citation (mentions={mentions}/3, citations={citations}/3)."
                    ),
                    "supporting_evidence_json": {
                        "engine": engine,
                        "question_id": question_id,
                        "mentions": mentions,
                        "citations": citations,
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                    },
                    "source_row_references_json": [
                        {"table": "ai_answer_runs", "natural_key": item["natural_key"]}
                        for item in items
                    ],
                    "target_query_or_question": sample["question"],
                    "target_page": sample["target_page"],
                    "proposed_action": (
                        "Strengthen the existing target page with clear brand entity signals "
                        "and cite-worthy answers; pursue AI citation destinations."
                    ),
                    "impact_estimate": f"expected_stable_mention_gain={mention_gain};citation_gain={citation_gain}",
                    "raw_impact": raw,
                    "confidence_label": confidence,
                    "confidence_value": conf_value,
                    "effort_label": "M",
                    "effort_value": EFFORT["M"],
                    "metric_to_watch": f"{engine}_mention_rate + citation_rate",
                }
            )
        out = []
        for item in candidates:
            impact = normalize_geo_impact(item.pop("raw_impact"), catalogue_size)
            item["impact_score"] = round(impact, 4)
            item["priority_score"] = priority_score(
                impact, item["confidence_value"], item["effort_value"]
            )
            item["supporting_evidence_json"] = json.dumps(item["supporting_evidence_json"], sort_keys=True)
            item["source_row_references_json"] = json.dumps(item["source_row_references_json"], sort_keys=True)
            out.append(item)
        return out
