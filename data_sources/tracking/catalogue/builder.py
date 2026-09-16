"""GSC-derived keyword catalogue candidate builder (Phase 6)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..config import TrackingConfig
from ..enums import (
    BrandStatus,
    CandidateDecision,
    CatalogueBuildStatus,
    CatalogueBuildType,
)
from ..exceptions import ConfigurationError, DataQualityError
from ..reports.metrics_calc import impression_weighted_position, weighted_ctr
from ..storage import TrackingStore
from ..transforms.brand_label import BrandClassifier
from ..transforms.normalize import (
    catalogue_transformation_method,
    natural_key,
    normalize_catalogue_query,
    normalize_query,
    sha256_hex,
    utc_now_iso,
)
from . import (
    DEFAULT_MIN_CLICKS_PROTECT,
    DEFAULT_MIN_IMPRESSIONS,
    DEFAULT_RELEVANCE_TERMS,
    DEFAULT_SELECTED_LIMIT,
    METHODOLOGY_VERSION,
    PROVISIONAL_CATALOGUE_META,
)
from .scoring import score_candidate

# Source values / run types that must never contribute to production catalogue builds.
_EXCLUDED_GSC_SOURCES = frozenset({"demo", "fixture", "test"})
_EXCLUDED_RUN_TYPES = frozenset({"demo"})


@dataclass(frozen=True)
class CatalogueThresholds:
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS
    min_clicks_protect: int = DEFAULT_MIN_CLICKS_PROTECT
    selected_limit: int = DEFAULT_SELECTED_LIMIT
    relevance_terms: Tuple[str, ...] = DEFAULT_RELEVANCE_TERMS


def thresholds_from_config(config: TrackingConfig) -> CatalogueThresholds:
    return CatalogueThresholds(
        min_impressions=int(getattr(config, "catalogue_min_impressions", DEFAULT_MIN_IMPRESSIONS)),
        min_clicks_protect=int(
            getattr(config, "catalogue_min_clicks_protect", DEFAULT_MIN_CLICKS_PROTECT)
        ),
        selected_limit=int(getattr(config, "catalogue_selected_limit", DEFAULT_SELECTED_LIMIT)),
        relevance_terms=tuple(
            getattr(config, "catalogue_relevance_terms", DEFAULT_RELEVANCE_TERMS) or DEFAULT_RELEVANCE_TERMS
        ),
    )


def default_gsc_window(store: TrackingStore, *, days: int = 90) -> Tuple[date, date]:
    rows = store.fetchall("SELECT MAX(date) AS max_d FROM gsc_daily WHERE source = 'gsc'")
    max_d = rows[0]["max_d"] if rows else None
    if not max_d:
        raise DataQualityError(
            "No production gsc_daily rows available for catalogue build. "
            "Backfill GSC first; do not invent candidates."
        )
    end = date.fromisoformat(max_d)
    start = end - timedelta(days=days - 1)
    return start, end


def _fetch_eligible_gsc_rows(
    store: TrackingStore,
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    rows = store.fetchall(
        """
        SELECT
            g.natural_key,
            g.run_id,
            g.date,
            g.query,
            g.page,
            g.clicks,
            g.impressions,
            g.ctr,
            g.position,
            g.source,
            COALESCE(r.run_type, '') AS run_type
        FROM gsc_daily g
        LEFT JOIN run_log r ON r.run_id = g.run_id
        WHERE g.date >= ? AND g.date <= ?
        ORDER BY g.date, g.query, g.page
        """,
        (start.isoformat(), end.isoformat()),
    )
    eligible: List[Dict[str, Any]] = []
    for row in rows:
        source = (row["source"] or "").strip().lower()
        run_type = (row["run_type"] or "").strip().lower()
        if source in _EXCLUDED_GSC_SOURCES or run_type in _EXCLUDED_RUN_TYPES:
            continue
        if source != "gsc":
            continue
        eligible.append(dict(row))
    return eligible


def _page_evidence(pages: Dict[str, Dict[str, float]]) -> Tuple[str, List[Dict[str, Any]], bool]:
    ranked = sorted(
        pages.items(),
        key=lambda item: (item[1]["impressions"], item[1]["clicks"], item[0]),
        reverse=True,
    )
    observed = [
        {
            "page": page,
            "clicks": int(stats["clicks"]),
            "impressions": int(stats["impressions"]),
        }
        for page, stats in ranked
    ]
    primary = ranked[0][0] if ranked else ""
    multi = len(ranked) > 1
    return primary, observed, multi


def _decide(
    *,
    brand: bool,
    relevance: float,
    impressions: int,
    clicks: int,
    opportunity: float,
    thresholds: CatalogueThresholds,
) -> Tuple[str, str]:
    if brand:
        return CandidateDecision.REJECTED.value, "branded_query_excluded_from_v1_nonbrand_catalogue"
    if relevance <= 0:
        return CandidateDecision.DEFERRED.value, "outside_configured_business_relevance_terms"
    if impressions < thresholds.min_impressions and clicks < thresholds.min_clicks_protect:
        return CandidateDecision.DEFERRED.value, "insufficient_gsc_evidence"
    if opportunity <= 0:
        return CandidateDecision.DEFERRED.value, "gsc_opportunity_score_zero"
    return CandidateDecision.PENDING.value, "meets_phase6_evidence_threshold_awaiting_review"


def build_keyword_catalogue(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    gsc_start: Optional[date] = None,
    gsc_end: Optional[date] = None,
    ga4_start: Optional[date] = None,
    ga4_end: Optional[date] = None,
    status: str = CatalogueBuildStatus.DRAFT.value,
    created_by: str = "catalogue-builder",
    thresholds: Optional[CatalogueThresholds] = None,
) -> Dict[str, Any]:
    """Build a draft keyword catalogue from stored production GSC rows only."""
    store.migrate()
    if status not in {s.value for s in CatalogueBuildStatus}:
        raise ConfigurationError(f"Invalid catalogue status: {status}")

    limits = thresholds or thresholds_from_config(config)
    if gsc_start is None or gsc_end is None:
        default_start, default_end = default_gsc_window(store, days=config.backfill_days or 90)
        gsc_start = gsc_start or default_start
        gsc_end = gsc_end or default_end
    if gsc_start > gsc_end:
        raise ConfigurationError("gsc-start-date must be on or before gsc-end-date")

    classifier = BrandClassifier.from_config(config)
    rows = _fetch_eligible_gsc_rows(store, gsc_start, gsc_end)
    excluded_fixture_count = store.fetchall(
        """
        SELECT COUNT(*) AS n
        FROM gsc_daily g
        LEFT JOIN run_log r ON r.run_id = g.run_id
        WHERE g.date >= ? AND g.date <= ?
          AND (
            LOWER(COALESCE(g.source, '')) IN ('demo', 'fixture', 'test')
            OR LOWER(COALESCE(r.run_type, '')) = 'demo'
            OR LOWER(COALESCE(g.source, '')) NOT IN ('gsc')
          )
        """,
        (gsc_start.isoformat(), gsc_end.isoformat()),
    )[0]["n"]

    funnel = {
        "raw_gsc_rows": len(rows),
        "excluded_fixture_or_non_production_rows": int(excluded_fixture_count),
        "unique_raw_queries": 0,
        "normalized_candidates": 0,
        "non_branded_candidates": 0,
        "business_relevant_candidates": 0,
        "candidates_meeting_evidence_threshold": 0,
        "serper_validated_candidates": 0,  # Phase 7
        "selected_keywords": 0,
        "pending_keywords": 0,
        "rejected_keywords": 0,
        "deferred_keywords": 0,
    }

    raw_queries = {normalize_query(r["query"]) for r in rows if r["query"]}
    funnel["unique_raw_queries"] = len(raw_queries)

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = normalize_catalogue_query(row["query"])
        if not key:
            continue
        grouped[key].append(row)

    build_id = str(uuid.uuid4())
    now = utc_now_iso()
    run_ids = sorted({r["run_id"] for r in rows})
    fingerprint_payload = "|".join(
        [
            gsc_start.isoformat(),
            gsc_end.isoformat(),
            ",".join(run_ids),
            str(len(rows)),
            METHODOLOGY_VERSION,
            classifier.version,
        ]
    )
    source_fingerprint = sha256_hex(fingerprint_payload)

    candidates_out: List[Dict[str, Any]] = []
    sources_out: List[Dict[str, Any]] = []

    for normalized, members in grouped.items():
        clicks = sum(int(m["clicks"]) for m in members)
        impressions = sum(int(m["impressions"]) for m in members)
        ctr = weighted_ctr(clicks, impressions)
        pos = impression_weighted_position(
            [{"impressions": m["impressions"], "position": m["position"]} for m in members]
        )
        pages: Dict[str, Dict[str, float]] = defaultdict(lambda: {"clicks": 0.0, "impressions": 0.0})
        for m in members:
            pages[m["page"]]["clicks"] += int(m["clicks"])
            pages[m["page"]]["impressions"] += int(m["impressions"])
        primary_page, observed_pages, multi_page = _page_evidence(pages)

        distinct_raw = sorted({normalize_query(m["query"]) for m in members})
        # Prefer highest-impression raw spelling as canonical display form.
        raw_impr: Dict[str, int] = defaultdict(int)
        for m in members:
            raw_impr[normalize_query(m["query"])] += int(m["impressions"])
        canonical = sorted(raw_impr.items(), key=lambda item: (-item[1], item[0]))[0][0]
        merged = len(distinct_raw) > 1
        brand = classifier.is_brand(canonical) or classifier.is_brand(normalized)
        brand_status = BrandStatus.BRANDED.value if brand else BrandStatus.NON_BRANDED.value

        scores = score_candidate(
            clicks=clicks,
            impressions=impressions,
            weighted_position=pos,
            weighted_ctr=ctr,
            multi_page=multi_page,
            normalized_keyword=normalized,
            source_row_count=len(members),
            source_date_count=len({m["date"] for m in members}),
            relevance_terms=limits.relevance_terms,
            min_impressions=limits.min_impressions,
        )
        decision, reason = _decide(
            brand=brand,
            relevance=scores.business_relevance_score,
            impressions=impressions,
            clicks=clicks,
            opportunity=scores.gsc_opportunity_score,
            thresholds=limits,
        )
        # Promote a shortlist of pending→selected for funnel visibility (still draft build).
        candidate_id = natural_key([build_id, normalized])
        candidates_out.append(
            {
                "candidate_id": candidate_id,
                "build_id": build_id,
                "canonical_keyword": canonical,
                "normalized_keyword": normalized,
                "brand_status": brand_status,
                "brand_rule_version": classifier.version,
                "primary_observed_page": primary_page,
                "observed_pages_json": observed_pages,
                "multi_page_competition": int(multi_page),
                "source_query_count": len(distinct_raw),
                "source_row_count": len(members),
                "source_date_count": len({m["date"] for m in members}),
                "gsc_clicks": clicks,
                "gsc_impressions": impressions,
                "gsc_weighted_ctr": ctr,
                "gsc_weighted_position": pos,
                "business_relevance_score": scores.business_relevance_score,
                "gsc_opportunity_score": scores.gsc_opportunity_score,
                "evidence_confidence_score": scores.evidence_confidence_score,
                "final_selection_score": scores.final_selection_score,
                "decision": decision,
                "decision_reason": reason + ";" + ",".join(scores.reasons),
                "proposed_target_page": primary_page,
                "created_at": now,
                "updated_at": now,
                "_sort": (
                    scores.final_selection_score,
                    impressions,
                    clicks,
                    normalized,
                ),
            }
        )
        for m in members:
            raw_norm = normalize_query(m["query"])
            method = catalogue_transformation_method(
                m["query"],
                normalized,
                merged=merged and raw_norm != canonical,
            )
            sources_out.append(
                {
                    "candidate_source_id": natural_key([candidate_id, m["natural_key"]]),
                    "candidate_id": candidate_id,
                    "gsc_natural_key": m["natural_key"],
                    "gsc_run_id": m["run_id"],
                    "raw_query": m["query"],
                    "raw_page": m["page"],
                    "clicks": int(m["clicks"]),
                    "impressions": int(m["impressions"]),
                    "ctr": float(m["ctr"]),
                    "position": float(m["position"]),
                    "row_date": m["date"],
                    "transformation_method": method,
                    "created_at": now,
                }
            )

    funnel["normalized_candidates"] = len(candidates_out)
    nonbrand = [c for c in candidates_out if c["brand_status"] == BrandStatus.NON_BRANDED.value]
    funnel["non_branded_candidates"] = len(nonbrand)
    relevant = [c for c in nonbrand if (c["business_relevance_score"] or 0) > 0]
    funnel["business_relevant_candidates"] = len(relevant)
    evidence_ok = [
        c
        for c in relevant
        if c["gsc_impressions"] >= limits.min_impressions
        or c["gsc_clicks"] >= limits.min_clicks_protect
    ]
    funnel["candidates_meeting_evidence_threshold"] = len(evidence_ok)

    # Select top N from evidence_ok by score for draft shortlist.
    ranked = sorted(evidence_ok, key=lambda c: c["_sort"], reverse=True)
    selected_ids = {c["candidate_id"] for c in ranked[: limits.selected_limit]}
    for c in candidates_out:
        if c["candidate_id"] in selected_ids:
            c["decision"] = CandidateDecision.SELECTED.value
            if not c["decision_reason"].startswith("selected_"):
                c["decision_reason"] = "selected_phase6_shortlist;" + c["decision_reason"]
        # strip private sort key before persist
        c.pop("_sort", None)

    funnel["selected_keywords"] = sum(
        1 for c in candidates_out if c["decision"] == CandidateDecision.SELECTED.value
    )
    funnel["pending_keywords"] = sum(
        1 for c in candidates_out if c["decision"] == CandidateDecision.PENDING.value
    )
    funnel["rejected_keywords"] = sum(
        1 for c in candidates_out if c["decision"] == CandidateDecision.REJECTED.value
    )
    funnel["deferred_keywords"] = sum(
        1 for c in candidates_out if c["decision"] == CandidateDecision.DEFERRED.value
    )

    notes = (
        f"Provisional catalogue reference: {PROVISIONAL_CATALOGUE_META}. "
        "Semrush and customer/support sources deferred. "
        "Serper validation and GA4 enrichment not applied in Phase 6."
    )
    store.insert_catalogue_build(
        {
            "build_id": build_id,
            "build_type": CatalogueBuildType.KEYWORD.value,
            "status": status,
            "source_window_start": gsc_start.isoformat(),
            "source_window_end": gsc_end.isoformat(),
            "gsc_source_run_ids": run_ids,
            "ga4_source_run_ids": [],
            "serper_source_run_ids": [],
            "source_fingerprint": source_fingerprint,
            "methodology_version": METHODOLOGY_VERSION,
            "created_by": created_by,
            "created_at": now,
            "notes": notes,
            "funnel_json": funnel,
            "ga4_window_start": ga4_start.isoformat() if ga4_start else None,
            "ga4_window_end": ga4_end.isoformat() if ga4_end else None,
        }
    )
    store.insert_keyword_candidates(candidates_out)
    store.insert_keyword_candidate_sources(sources_out)

    # Spot-check aggregation reconciliation for selected/pending candidates.
    reconciliation_errors = _reconcile_sample(store, build_id, limit=25)
    if reconciliation_errors:
        raise DataQualityError(
            "GSC aggregation reconciliation failed: " + "; ".join(reconciliation_errors[:5])
        )

    result = {
        "build_id": build_id,
        "build_type": CatalogueBuildType.KEYWORD.value,
        "status": status,
        "methodology_version": METHODOLOGY_VERSION,
        "source_window_start": gsc_start.isoformat(),
        "source_window_end": gsc_end.isoformat(),
        "ga4_window_start": ga4_start.isoformat() if ga4_start else None,
        "ga4_window_end": ga4_end.isoformat() if ga4_end else None,
        "gsc_source_run_ids": run_ids,
        "source_fingerprint": source_fingerprint,
        "brand_rule_version": classifier.version,
        "funnel": funnel,
        "inserted": {
            "candidates": len(candidates_out),
            "sources": len(sources_out),
        },
        "provisional_catalogue": PROVISIONAL_CATALOGUE_META,
        "limitations": [
            "v1 catalogue is biased toward queries where Sunnystep already received Google impressions",
            "Semrush deferred pending permission",
            "customer/support language sources unavailable",
            "Serper validation requires explicit catalogue validate-serp",
        ],
    }
    if ga4_start is not None and ga4_end is not None:
        from .enrich import enrich_build_with_ga4

        result["ga4_enrichment"] = enrich_build_with_ga4(
            store,
            config,
            build_id=build_id,
            ga4_start=ga4_start,
            ga4_end=ga4_end,
        )
    return result


def _reconcile_sample(store: TrackingStore, build_id: str, *, limit: int) -> List[str]:
    errors: List[str] = []
    candidates = store.fetchall(
        """
        SELECT candidate_id, gsc_clicks, gsc_impressions, gsc_weighted_ctr, gsc_weighted_position
        FROM keyword_candidates
        WHERE build_id = ?
        ORDER BY gsc_impressions DESC
        LIMIT ?
        """,
        (build_id, limit),
    )
    for cand in candidates:
        sources = store.fetchall(
            """
            SELECT clicks, impressions, position
            FROM keyword_candidate_sources
            WHERE candidate_id = ?
            """,
            (cand["candidate_id"],),
        )
        clicks = sum(int(s["clicks"]) for s in sources)
        impressions = sum(int(s["impressions"]) for s in sources)
        if clicks != int(cand["gsc_clicks"]) or impressions != int(cand["gsc_impressions"]):
            errors.append(f"{cand['candidate_id']}: clicks/impressions mismatch")
            continue
        expected_ctr = weighted_ctr(clicks, impressions)
        stored_ctr = cand["gsc_weighted_ctr"]
        if expected_ctr is None and stored_ctr is not None:
            errors.append(f"{cand['candidate_id']}: ctr should be null")
        elif expected_ctr is not None and stored_ctr is not None:
            if abs(float(stored_ctr) - float(expected_ctr)) > 1e-9:
                errors.append(f"{cand['candidate_id']}: ctr mismatch")
        expected_pos = impression_weighted_position(
            [{"impressions": s["impressions"], "position": s["position"]} for s in sources]
        )
        stored_pos = cand["gsc_weighted_position"]
        if expected_pos is None and stored_pos is not None:
            errors.append(f"{cand['candidate_id']}: position should be null")
        elif expected_pos is not None and stored_pos is not None:
            if abs(float(stored_pos) - float(expected_pos)) > 1e-9:
                errors.append(f"{cand['candidate_id']}: position mismatch")
        if not sources:
            errors.append(f"{cand['candidate_id']}: missing source lineage")
    return errors


def get_candidate_lineage(store: TrackingStore, candidate_id: str) -> Dict[str, Any]:
    rows = store.fetchall(
        "SELECT * FROM keyword_candidates WHERE candidate_id = ?",
        (candidate_id,),
    )
    if not rows:
        raise DataQualityError(f"Unknown candidate_id {candidate_id}")
    sources = store.fetchall(
        "SELECT * FROM keyword_candidate_sources WHERE candidate_id = ? ORDER BY impressions DESC",
        (candidate_id,),
    )
    build = store.fetchall(
        "SELECT * FROM catalogue_builds WHERE build_id = ?",
        (rows[0]["build_id"],),
    )
    return {
        "candidate": dict(rows[0]),
        "sources": [dict(s) for s in sources],
        "build": dict(build[0]) if build else None,
    }
