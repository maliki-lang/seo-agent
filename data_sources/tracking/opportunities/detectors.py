"""Multi-signal opportunity detectors (Phase 15)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..enums import (
    OpportunityActionType,
    OpportunityCategory,
    OpportunitySourceType,
    TargetPageStatus,
)
from ..storage import TrackingStore
from .impact import CONFIDENCE, EFFORT, estimated_click_gain

HOMEPAGE_SUFFIXES = ("sunnystep.com", "sunnystep.com/", "gosunnystep.myshopify.com")


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _norm_host_path(url: str) -> str:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}"


def _is_homepage(url: str) -> bool:
    host_path = _norm_host_path(url)
    return host_path in {
        "sunnystep.com",
        "sunnystep.com/",
        "gosunnystep.myshopify.com",
        "gosunnystep.myshopify.com/",
    } or host_path.endswith("sunnystep.com/")


def _base_row(
    *,
    source_type: str,
    action_type: str,
    problem: str,
    proposed_action: str,
    category: str,
    evidence: Dict[str, Any],
    refs: List[Any],
    benchmarks: List[str],
    target_page: str,
    target_page_status: str,
    cluster_id: Optional[str],
    family_id: Optional[str],
    confidence_label: str,
    effort_label: str,
    metric: str,
    click_gain: Optional[Dict[str, Any]] = None,
    geo_gain: Optional[float] = None,
    estimated_cost: Optional[float] = None,
) -> Dict[str, Any]:
    conf_value = CONFIDENCE[confidence_label]
    gain = float((click_gain or {}).get("expected_incremental_clicks") or 0.0)
    assumptions = list((click_gain or {}).get("assumptions") or [])
    return {
        "opportunity_version": "v2",
        "source_type": source_type,
        "action_type": action_type,
        "category": category,
        "problem": problem,
        "proposed_action": proposed_action,
        "supporting_evidence_json": evidence,
        "source_row_references_json": refs,
        "benchmark_ids_json": benchmarks,
        "target_page": target_page or "",
        "target_asset": None,
        "target_page_status": target_page_status,
        "cluster_id": cluster_id,
        "family_id": family_id,
        "expected_incremental_clicks": gain if click_gain is not None else None,
        "expected_geo_gain": geo_gain,
        "estimated_cost": estimated_cost,
        "cost_currency": "USD" if estimated_cost is not None else None,
        "confidence_label": confidence_label,
        "confidence_value": conf_value,
        "effort_label": effort_label,
        "effort_value": EFFORT[effort_label],
        "metric_to_watch": metric,
        "assumptions_json": assumptions,
        "target_query_or_question": (benchmarks[0] if benchmarks else evidence.get("keyword") or ""),
        "impact_estimate": (
            f"estimated_click_gain={gain:.2f}"
            if click_gain is not None
            else f"expected_geo_gain={geo_gain or 0}"
        ),
        "impact_score": round(gain, 4) if click_gain is not None else round(float(geo_gain or 0), 4),
    }


def load_benchmark_rows(
    store: TrackingStore,
    *,
    catalogue_version: Optional[str] = None,
    build_id: Optional[str] = None,
    period_start: date,
    period_end: date,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Load actionable benchmarks from activated catalogue or selected build candidates."""
    blocked: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []

    if build_id:
        candidates = [
            dict(c)
            for c in store.fetchall(
                """
                SELECT * FROM keyword_candidates
                WHERE build_id = ? AND decision = 'selected'
                ORDER BY gsc_impressions DESC
                """,
                (build_id,),
            )
        ]
        for c in candidates:
            rows.append(
                {
                    "benchmark_id": c["candidate_id"],
                    "keyword": c["canonical_keyword"] or c["normalized_keyword"],
                    "cluster_id": c.get("cluster_id"),
                    "family_id": c.get("family_id"),
                    "target_page": c.get("llm_recommended_target_page")
                    or c.get("reviewed_target_page")
                    or c.get("proposed_target_page")
                    or c.get("primary_observed_page")
                    or "",
                    "observed_page": c.get("primary_observed_page") or "",
                    "target_page_status": c.get("target_page_status") or "",
                    "multi_page_class": c.get("multi_page_class") or "",
                    "semantic_authority": c.get("semantic_authority") or "",
                    "llm_actionability": c.get("llm_actionability") or "",
                    "impressions": int(c.get("gsc_impressions") or 0),
                    "clicks": int(c.get("gsc_clicks") or 0),
                    "ctr": float(c.get("gsc_weighted_ctr") or 0),
                    "position": float(c.get("gsc_weighted_position") or c.get("serper_position") or 0),
                    "serper_position": int(c.get("serper_position") or 0),
                    "serper_ranking_url": c.get("serper_ranking_url") or "",
                    "ga4_sessions": float(c.get("ga4_organic_sessions") or 0),
                    "ga4_purchases": float(c.get("ga4_purchases") or 0),
                    "ga4_revenue": float(c.get("ga4_revenue") or 0),
                    "page_type": c.get("page_type") or "",
                }
            )
    elif catalogue_version:
        catalog = [
            dict(item)
            for item in store.fetchall(
                """
                SELECT * FROM keyword_catalog
                WHERE catalogue_version = ? AND active = 1
                """,
                (catalogue_version,),
            )
        ]
        for item in catalog:
            keyword = item["keyword"]
            gsc = store.fetchall(
                """
                SELECT SUM(impressions) AS impressions, SUM(clicks) AS clicks,
                       CASE WHEN SUM(impressions) > 0
                            THEN 1.0 * SUM(clicks) / SUM(impressions) ELSE 0 END AS ctr,
                       CASE WHEN SUM(impressions) > 0
                            THEN SUM(position * impressions) / SUM(impressions) ELSE 0 END AS position
                FROM gsc_daily
                WHERE date >= ? AND date <= ? AND lower(query) = lower(?)
                """,
                (period_start.isoformat(), period_end.isoformat(), keyword),
            )[0]
            serp = store.fetchall(
                """
                SELECT sunnystep_position, target_page, natural_key
                FROM serp_daily
                WHERE lower(keyword) = lower(?)
                ORDER BY date DESC LIMIT 1
                """,
                (keyword,),
            )
            position = float(gsc["position"] or 0)
            serper_position = int(serp[0]["sunnystep_position"] or 0) if serp else 0
            rows.append(
                {
                    "benchmark_id": item["keyword_id"],
                    "keyword": keyword,
                    "cluster_id": item.get("cluster"),
                    "family_id": None,
                    "target_page": item.get("target_page") or (serp[0]["target_page"] if serp else ""),
                    "observed_page": serp[0]["target_page"] if serp else "",
                    "target_page_status": "",
                    "multi_page_class": "",
                    "impressions": int(gsc["impressions"] or 0),
                    "clicks": int(gsc["clicks"] or 0),
                    "ctr": float(gsc["ctr"] or 0),
                    "position": position or float(serper_position or 0),
                    "serper_position": serper_position,
                    "serper_ranking_url": serp[0]["target_page"] if serp else "",
                    "ga4_sessions": 0.0,
                    "ga4_purchases": 0.0,
                    "ga4_revenue": 0.0,
                    "page_type": "",
                    "serp_natural_key": serp[0]["natural_key"] if serp else None,
                }
            )
    else:
        blocked["benchmark_source"] = "catalogue_version or build_id required"

    return rows, blocked


def detect_all(
    store: TrackingStore,
    *,
    period_start: date,
    period_end: date,
    catalogue_version: Optional[str] = None,
    build_id: Optional[str] = None,
) -> Dict[str, Any]:
    benchmarks, blocked = load_benchmark_rows(
        store,
        catalogue_version=catalogue_version,
        build_id=build_id,
        period_start=period_start,
        period_end=period_end,
    )
    candidates: List[Dict[str, Any]] = []
    detector_counts: Dict[str, int] = {}

    def _add(rows: Sequence[Dict[str, Any]], name: str) -> None:
        detector_counts[name] = len(rows)
        candidates.extend(rows)

    _add(_ctr_underperformance(benchmarks, period_start, period_end), OpportunitySourceType.CTR_UNDERPERFORMANCE.value)
    _add(_ranking_improvement(benchmarks, period_start, period_end), OpportunitySourceType.RANKING_IMPROVEMENT.value)
    _add(_page_one_underperformance(benchmarks, period_start, period_end), OpportunitySourceType.PAGE_ONE_UNDERPERFORMANCE.value)
    _add(_cannibalization(benchmarks, period_start, period_end), OpportunitySourceType.CANNIBALIZATION.value)
    _add(_wrong_target(benchmarks, period_start, period_end), OpportunitySourceType.WRONG_TARGET.value)
    _add(_traffic_commerce_gap(benchmarks, period_start, period_end), OpportunitySourceType.TRAFFIC_TO_COMMERCE_GAP.value)
    _add(_existing_page_expansion(benchmarks, period_start, period_end), OpportunitySourceType.EXISTING_PAGE_EXPANSION.value)
    _add(_new_page_gap(benchmarks, period_start, period_end), OpportunitySourceType.NEW_PAGE_GAP.value)
    geo_rows, geo_blocked = _geo_evidence_gap(store, period_start, period_end)
    if geo_blocked:
        blocked["geo_evidence_gap"] = geo_blocked
    else:
        _add(geo_rows, OpportunitySourceType.GEO_EVIDENCE_GAP.value)
    tech_rows, tech_blocked = _technical_manual(store, period_start, period_end)
    if tech_blocked:
        blocked["technical_manual"] = tech_blocked
    else:
        _add(tech_rows, OpportunitySourceType.TECHNICAL_MANUAL.value)

    return {
        "candidates": candidates,
        "detector_counts": detector_counts,
        "blocked_detectors": blocked,
        "benchmark_count": len(benchmarks),
    }


def _ctr_underperformance(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        position = int(round(row["position"] or 0))
        if not (1 <= position <= 10):
            continue
        if row["impressions"] < 40:
            continue
        expected = estimated_click_gain(
            impressions=row["impressions"], current_clicks=row["clicks"], target_position=position
        )
        # Underperformance vs expected CTR at current position.
        expected_at_current = row["impressions"] * expected["expected_ctr_at_target"]
        if row["clicks"] >= expected_at_current * 0.7:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"],
            current_clicks=row["clicks"],
            target_position=max(1, position - 2),
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.CTR_UNDERPERFORMANCE.value,
                action_type=OpportunityActionType.TITLE_META_REWRITE.value,
                problem=(
                    f"CTR underperforms position-relative expectation at rank ~{position} "
                    f"({row['clicks']} clicks vs ~{expected_at_current:.1f} expected)."
                ),
                proposed_action="Rewrite title/meta to better match intent and improve CTR.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "position": position,
                    "impressions": row["impressions"],
                    "clicks": row["clicks"],
                    "ctr": row["ctr"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=row["target_page"],
                target_page_status=row.get("target_page_status") or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium" if row["impressions"] >= 80 else "low",
                effort_label="S",
                metric="gsc_ctr + gsc_clicks",
                click_gain=gain,
            )
        )
    return out


def _ranking_improvement(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        position = int(round(row.get("serper_position") or row["position"] or 0))
        if not (11 <= position <= 20):
            continue
        if row["impressions"] < 20:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"], current_clicks=row["clicks"], target_position=10
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.RANKING_IMPROVEMENT.value,
                action_type=OpportunityActionType.CONTENT_EXPANSION.value,
                problem=f"Meaningful demand at position {position} (page-two band) with upside to page one.",
                proposed_action="Expand existing page coverage and internal links to reach page one.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "position": position,
                    "impressions": row["impressions"],
                    "clicks": row["clicks"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=row["target_page"],
                target_page_status=row.get("target_page_status") or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium",
                effort_label="M",
                metric="serp_position + gsc_clicks",
                click_gain=gain,
            )
        )
    return out


def _page_one_underperformance(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        position = int(round(row.get("serper_position") or row["position"] or 0))
        if not (4 <= position <= 10):
            continue
        if row["impressions"] < 25:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"], current_clicks=row["clicks"], target_position=3
        )
        if gain["expected_incremental_clicks"] < 1:
            continue
        out.append(
            _base_row(
                source_type=OpportunitySourceType.PAGE_ONE_UNDERPERFORMANCE.value,
                action_type=OpportunityActionType.CONTENT_EXPANSION.value,
                problem=f"Page-one rank {position} with measurable upside to top-3.",
                proposed_action="Strengthen on-page relevance and internal linking on the owner page.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "position": position,
                    "impressions": row["impressions"],
                    "clicks": row["clicks"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=row["target_page"],
                target_page_status=row.get("target_page_status") or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium",
                effort_label="M",
                metric="serp_position + gsc_clicks",
                click_gain=gain,
            )
        )
    return out


def _cannibalization(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if row.get("multi_page_class") != "cannibalization_candidate":
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"],
            current_clicks=row["clicks"],
            target_position=max(3, int(round(row["position"] or 10))),
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.CANNIBALIZATION.value,
                action_type=OpportunityActionType.CONTENT_CONSOLIDATION.value,
                problem="Multiple Sunnystep pages appear to compete for the same family/intent.",
                proposed_action="Consolidate competing pages or clarify ownership with redirects/internal links.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "multi_page_class": row.get("multi_page_class"),
                    "impressions": row["impressions"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=row["target_page"],
                target_page_status=TargetPageStatus.MULTIPLE_PAGES_COMPETING.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium",
                effort_label="L",
                metric="gsc_clicks_by_page",
                click_gain=gain,
            )
        )
    return out


def _wrong_target(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        approved = row.get("target_page") or ""
        observed = row.get("serper_ranking_url") or row.get("observed_page") or ""
        if not approved or not observed:
            continue
        if _norm_host_path(approved) == _norm_host_path(observed):
            continue
        if row["impressions"] < 15:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"],
            current_clicks=row["clicks"],
            target_position=max(3, int(round(row["position"] or 8))),
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.WRONG_TARGET.value,
                action_type=OpportunityActionType.INTERNAL_LINKING.value,
                problem="Observed ranking page differs from the approved owner page.",
                proposed_action="Align ranking URL to approved owner via internal links and on-page targeting.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "approved_target": approved,
                    "observed_ranking_url": observed,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=approved,
                target_page_status=row.get("target_page_status") or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium",
                effort_label="M",
                metric="serp_ranking_url",
                click_gain=gain,
            )
        )
    return out


def _traffic_commerce_gap(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        sessions = float(row.get("ga4_sessions") or 0)
        purchases = float(row.get("ga4_purchases") or 0)
        if sessions < 50:
            continue
        if purchases > 0:
            continue
        # Page-level attribution is limited — keep confidence low and note assumption.
        out.append(
            _base_row(
                source_type=OpportunitySourceType.TRAFFIC_TO_COMMERCE_GAP.value,
                action_type=OpportunityActionType.PRODUCT_MAPPING.value,
                problem=(
                    f"Strong organic sessions ({sessions:.0f}) with weak recorded purchases "
                    "(page-level GA4 attribution limitations apply)."
                ),
                proposed_action="Improve product mapping/CTAs from the landing page; verify measurement.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "ga4_sessions": sessions,
                    "ga4_purchases": purchases,
                    "attribution_note": "page_level_not_keyword_causal",
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=row["target_page"],
                target_page_status=row.get("target_page_status") or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="low",
                effort_label="M",
                metric="ga4_organic_sessions + purchases",
                click_gain=None,
                estimated_cost=None,
            )
        )
        out[-1]["assumptions_json"] = [
            "GA4 page metrics are not keyword-causal",
            "commerce gap requires measurement review before scaling",
        ]
        out[-1]["expected_incremental_clicks"] = 0.0
        out[-1]["impact_score"] = 0.0
    return out


def _existing_page_expansion(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        page = row.get("target_page") or ""
        status = row.get("target_page_status") or ""
        if not page or _is_homepage(page):
            continue
        if status in {
            TargetPageStatus.NO_SENSIBLE_TARGET.value,
            TargetPageStatus.APPROVED_NEW_PAGE.value,
            TargetPageStatus.HOMEPAGE_UNRESOLVED.value,
        }:
            continue
        if row["impressions"] < 30:
            continue
        position = int(round(row["position"] or 0))
        if position and position <= 3:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"],
            current_clicks=row["clicks"],
            target_position=3 if position and position <= 10 else 10,
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.EXISTING_PAGE_EXPANSION.value,
                action_type=OpportunityActionType.ANSWER_SECTION.value,
                problem="Validated demand exists and a relevant page can be expanded.",
                proposed_action="Add answer sections covering missing intent facets on the existing page.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "impressions": row["impressions"],
                    "position": position,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=page,
                target_page_status=status or TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="medium",
                effort_label="M",
                metric="gsc_clicks",
                click_gain=gain,
            )
        )
    return out


def _new_page_gap(rows, start, end) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        status = row.get("target_page_status") or ""
        page = row.get("target_page") or ""
        is_gap = status in {
            TargetPageStatus.APPROVED_NEW_PAGE.value,
            TargetPageStatus.NO_SENSIBLE_TARGET.value,
            TargetPageStatus.HOMEPAGE_UNRESOLVED.value,
        } or (_is_homepage(page) and row["impressions"] >= 40)
        if not is_gap:
            continue
        if row["impressions"] < 25:
            continue
        gain = estimated_click_gain(
            impressions=row["impressions"], current_clicks=row["clicks"], target_position=10
        )
        out.append(
            _base_row(
                source_type=OpportunitySourceType.NEW_PAGE_GAP.value,
                action_type=OpportunityActionType.NEW_PAGE.value,
                problem="Validated demand with no credible existing owner page (requires review).",
                proposed_action="Create a new owner page after human review of the content gap.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "keyword": row["keyword"],
                    "target_page_status": status,
                    "current_page": page,
                    "impressions": row["impressions"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "benchmark", "benchmark_id": row["benchmark_id"]}],
                benchmarks=[row["benchmark_id"]],
                target_page=page if not _is_homepage(page) else "",
                target_page_status=status or TargetPageStatus.APPROVED_NEW_PAGE.value,
                cluster_id=row.get("cluster_id"),
                family_id=row.get("family_id"),
                confidence_label="low",
                effort_label="L",
                metric="gsc_impressions + new_page_ranking",
                click_gain=gain,
            )
        )
    return out


def _geo_evidence_gap(store, start, end) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    rows = store.fetchall(
        """
        SELECT natural_key, question_id, question, target_page, engine,
               mentioned_sunnystep, cited_urls, repetition_number
        FROM ai_answer_runs
        WHERE as_of_date >= ? AND as_of_date <= ?
        """,
        (start.isoformat(), end.isoformat()),
    )
    if not rows:
        return [], "ai_answer_runs unavailable for period"
    grouped: Dict[Tuple[str, str], List[Any]] = {}
    for row in rows:
        grouped.setdefault((row["question_id"], row["engine"]), []).append(row)
    out = []
    for (question_id, engine), items in grouped.items():
        if len(items) < 3:
            continue
        mentions = sum(1 for i in items if i["mentioned_sunnystep"])
        citations = 0
        for item in items:
            urls = _load_json(item["cited_urls"], [])
            if any("sunnystep.com" in (u or "") for u in urls):
                citations += 1
        if mentions >= 2 and citations >= 2:
            continue
        sample = items[0]
        out.append(
            _base_row(
                source_type=OpportunitySourceType.GEO_EVIDENCE_GAP.value,
                action_type=OpportunityActionType.GEO_EVIDENCE_UPGRADE.value,
                problem=(
                    f"{engine} answers lack stable Sunnystep mention/citation "
                    f"(mentions={mentions}/3, citations={citations}/3)."
                ),
                proposed_action="Upgrade cite-worthy answer evidence on the target page for AI visibility.",
                category=OpportunityCategory.GEO.value,
                evidence={
                    "engine": engine,
                    "question_id": question_id,
                    "mentions": mentions,
                    "citations": citations,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "ai_answer_runs", "natural_key": i["natural_key"]} for i in items],
                benchmarks=[question_id],
                target_page=sample["target_page"] or "",
                target_page_status=TargetPageStatus.OBSERVED_PAGE_NEEDS_OPTIMIZATION.value,
                cluster_id=None,
                family_id=None,
                confidence_label="medium" if len(items) >= 3 else "low",
                effort_label="M",
                metric=f"{engine}_mention_rate + citation_rate",
                click_gain=None,
                geo_gain=float((1 if mentions < 2 else 0) + (1 if citations < 2 else 0)),
            )
        )
        out[-1]["expected_incremental_clicks"] = None  # GEO must not invent click conversion
        out[-1]["assumptions_json"] = [
            "GEO impact is not translated into clicks without an evidence-backed conversion model"
        ]
    return out, None


def _technical_manual(store, start, end) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    # Prefer quality-check failures in the period; otherwise mark blocked.
    checks = store.fetchall(
        """
        SELECT check_id, run_id, check_name, status, severity, details_json, observed_value
        FROM quality_check_log
        WHERE status = 'fail' AND severity IN ('critical', 'error')
        ORDER BY check_id DESC
        LIMIT 20
        """
    )
    if not checks:
        return [], "no failed quality checks available for technical detector"
    out = []
    for row in checks[:5]:
        out.append(
            _base_row(
                source_type=OpportunitySourceType.TECHNICAL_MANUAL.value,
                action_type=OpportunityActionType.MANUAL_INVESTIGATION.value,
                problem=f"Quality check failed: {row['check_name']} ({row['severity']}).",
                proposed_action="Investigate indexability/freshness/measurement failure and remediate.",
                category=OpportunityCategory.SEO.value,
                evidence={
                    "check_name": row["check_name"],
                    "severity": row["severity"],
                    "observed_value": row["observed_value"],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
                refs=[{"table": "quality_check_log", "check_id": row["check_id"], "run_id": row["run_id"]}],
                benchmarks=[],
                target_page="",
                target_page_status=TargetPageStatus.MANUAL_REVIEW.value,
                cluster_id=None,
                family_id=None,
                confidence_label="low",
                effort_label="M",
                metric="quality_check_status",
                click_gain=None,
            )
        )
        out[-1]["expected_incremental_clicks"] = 0.0
        out[-1]["assumptions_json"] = ["technical findings require manual confirmation before content changes"]
    return out, None
