"""Stakeholder catalogue provenance report (Markdown + JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import TrackingConfig
from ..enums import CandidateDecision
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from . import PROVISIONAL_CATALOGUE_META, PROVISIONAL_CATALOGUE_VERSION


DEFERRED_SOURCES = [
    {
        "source": "Semrush",
        "status": "deferred",
        "reason": "Pending permission; no keyword-volume/difficulty/competitor-gap claims in v1.",
    },
    {
        "source": "Customer/support tickets, reviews, onsite search, sales conversations",
        "status": "deferred",
        "reason": "Unavailable unless access is later granted.",
    },
]


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def build_provenance_report(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    question_build_id: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
    if not builds:
        raise DataQualityError(f"Unknown build_id {build_id}")
    build = dict(builds[0])
    if not question_build_id:
        children = store.fetchall(
            """
            SELECT build_id FROM catalogue_builds
            WHERE parent_build_id = ? AND build_type = 'ai_question'
            ORDER BY created_at DESC LIMIT 1
            """,
            (build_id,),
        )
        question_build_id = children[0]["build_id"] if children else None

    funnel = _loads(build.get("funnel_json"), {})
    ga4_report = _loads(build.get("ga4_match_report_json"), {})
    serper_report = _loads(build.get("serper_validation_report_json"), {})

    decision_counts = {
        row["decision"]: int(row["n"])
        for row in store.fetchall(
            """
            SELECT decision, COUNT(*) AS n
            FROM keyword_candidates WHERE build_id = ?
            GROUP BY decision
            """,
            (build_id,),
        )
    }
    clusters = [dict(r) for r in store.fetchall(
        "SELECT cluster_id, cluster_name, primary_intent, primary_target_page, "
        "member_candidate_ids, gsc_clicks, gsc_impressions FROM catalogue_clusters WHERE build_id = ?",
        (build_id,),
    )]
    comparison = [
        dict(r)
        for r in store.fetchall(
            "SELECT decision, COUNT(*) AS n FROM catalogue_comparisons WHERE build_id = ? GROUP BY decision",
            (build_id,),
        )
    ]
    comparison_counts = {r["decision"]: int(r["n"]) for r in comparison}

    lineage = _lineage_walkthrough(store, build_id, question_build_id)

    q_selected = 0
    if question_build_id:
        q_selected = int(
            store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
                (question_build_id, CandidateDecision.SELECTED.value),
            )[0]["n"]
        )

    payload = {
        "generated_at": utc_now_iso(),
        "methodology_version": build.get("methodology_version"),
        "limitations": [
            "The v1 catalogue is biased toward queries where Sunnystep already received Google impressions. "
            "It does not provide complete discovery of zero-visibility market opportunities. "
            "Semrush and customer-language sources are planned enrichments for a later catalogue version.",
            "Serper validates current SERP visibility only and is not search-volume evidence.",
        ],
        "provisional_catalogue": PROVISIONAL_CATALOGUE_META,
        "build": {
            "build_id": build_id,
            "question_build_id": question_build_id,
            "status": build.get("status"),
            "source_window_start": build.get("source_window_start"),
            "source_window_end": build.get("source_window_end"),
            "ga4_window_start": build.get("ga4_window_start"),
            "ga4_window_end": build.get("ga4_window_end"),
            "gsc_source_run_ids": _loads(build.get("gsc_source_run_ids"), []),
            "ga4_source_run_ids": _loads(build.get("ga4_source_run_ids"), []),
            "serper_source_run_ids": _loads(build.get("serper_source_run_ids"), []),
            "source_fingerprint": build.get("source_fingerprint"),
            "approved_by": build.get("approved_by"),
            "approved_at": build.get("approved_at"),
            "activated_at": build.get("activated_at"),
        },
        "funnel": funnel,
        "decision_counts": decision_counts,
        "selected_keywords": int(decision_counts.get(CandidateDecision.SELECTED.value, 0)),
        "selected_questions": q_selected,
        "cluster_distribution": [
            {
                "cluster_id": c["cluster_id"],
                "name": c["cluster_name"],
                "intent": c["primary_intent"],
                "primary_target_page": c["primary_target_page"],
                "members": len(_loads(c.get("member_candidate_ids"), [])),
                "gsc_clicks": c["gsc_clicks"],
                "gsc_impressions": c["gsc_impressions"],
            }
            for c in clusters
        ],
        "ga4_match_report": ga4_report,
        "serper_validation_report": serper_report,
        "old_catalogue_comparison": {
            "provisional_version": PROVISIONAL_CATALOGUE_VERSION,
            "counts": comparison_counts,
        },
        "lineage_walkthrough": lineage,
        "deferred_source_register": DEFERRED_SOURCES,
        "counts_for_parity": {
            "selected_keywords": int(decision_counts.get(CandidateDecision.SELECTED.value, 0)),
            "selected_questions": q_selected,
            "clusters": len(clusters),
            "comparison_rows": sum(comparison_counts.values()),
        },
    }
    return payload


def _lineage_walkthrough(
    store: TrackingStore, build_id: str, question_build_id: Optional[str]
) -> Dict[str, Any]:
    candidates = store.fetchall(
        """
        SELECT * FROM keyword_candidates
        WHERE build_id = ? AND decision = ?
        ORDER BY gsc_impressions DESC LIMIT 1
        """,
        (build_id, CandidateDecision.SELECTED.value),
    )
    if not candidates:
        return {"status": "unavailable", "reason": "no selected keyword candidates"}
    cand = dict(candidates[0])
    sources = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM keyword_candidate_sources WHERE candidate_id = ? ORDER BY impressions DESC",
            (cand["candidate_id"],),
        )
    ]
    question = None
    if question_build_id:
        qrows = store.fetchall(
            """
            SELECT * FROM ai_question_candidates
            WHERE build_id = ? AND decision = ?
              AND source_candidate_ids LIKE ?
            LIMIT 1
            """,
            (question_build_id, CandidateDecision.SELECTED.value, f'%{cand["candidate_id"]}%'),
        )
        if not qrows:
            qrows = store.fetchall(
                "SELECT * FROM ai_question_candidates WHERE build_id = ? AND decision = ? LIMIT 1",
                (question_build_id, CandidateDecision.SELECTED.value),
            )
        if qrows:
            question = dict(qrows[0])
    return {
        "raw_gsc_rows": [
            {
                "gsc_natural_key": s["gsc_natural_key"],
                "raw_query": s["raw_query"],
                "raw_page": s["raw_page"],
                "clicks": s["clicks"],
                "impressions": s["impressions"],
                "transformation_method": s["transformation_method"],
            }
            for s in sources
        ],
        "normalized_candidate": {
            "candidate_id": cand["candidate_id"],
            "canonical_keyword": cand["canonical_keyword"],
            "normalized_keyword": cand["normalized_keyword"],
            "gsc_clicks": cand["gsc_clicks"],
            "gsc_impressions": cand["gsc_impressions"],
            "gsc_weighted_ctr": cand["gsc_weighted_ctr"],
            "gsc_weighted_position": cand["gsc_weighted_position"],
            "primary_observed_page": cand["primary_observed_page"],
            "ga4_organic_sessions": cand.get("ga4_organic_sessions"),
            "ga4_match_status": cand.get("ga4_match_status"),
            "serper_position": cand.get("serper_position"),
            "decision": cand["decision"],
            "cluster_id": cand.get("cluster_id"),
        },
        "ai_question": question,
    }


def render_provenance_markdown(payload: Dict[str, Any]) -> str:
    build = payload["build"]
    lines: List[str] = [
        "# Catalogue provenance report",
        "",
        f"Generated at: `{payload['generated_at']}`",
        f"Methodology: `{payload.get('methodology_version')}`",
        "",
        "## 1. Methodology and source limitations",
        "",
    ]
    for item in payload["limitations"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## 2. GSC and GA4 windows",
            "",
            f"- GSC: `{build.get('source_window_start')}` → `{build.get('source_window_end')}`",
            f"- GA4: `{build.get('ga4_window_start')}` → `{build.get('ga4_window_end')}`",
            "",
            "## 3. Source run IDs and fingerprints",
            "",
            f"- Fingerprint: `{build.get('source_fingerprint')}`",
            f"- GSC runs: `{build.get('gsc_source_run_ids')}`",
            f"- GA4 runs: `{build.get('ga4_source_run_ids')}`",
            f"- Serper runs: `{build.get('serper_source_run_ids')}`",
            "",
            "## 4. Selection funnel",
            "",
            "```json",
            json.dumps(payload.get("funnel") or {}, indent=2, sort_keys=True),
            "```",
            "",
            "## 5. Cluster distribution",
            "",
        ]
    )
    for cluster in payload.get("cluster_distribution") or []:
        lines.append(
            f"- **{cluster['name']}** ({cluster['intent']}): "
            f"{cluster['members']} members → `{cluster['primary_target_page']}`"
        )
    lines.extend(
        [
            "",
            "## 6. Selected / rejected / deferred",
            "",
            f"- Keyword decisions: `{payload.get('decision_counts')}`",
            f"- Selected keywords: **{payload.get('selected_keywords')}**",
            f"- Selected AI questions: **{payload.get('selected_questions')}**",
            "",
            "## 7. GA4 page-match rate",
            "",
            "```json",
            json.dumps(payload.get("ga4_match_report") or {}, indent=2, sort_keys=True),
            "```",
            "",
            "## 8. Serper validation coverage",
            "",
            "```json",
            json.dumps(payload.get("serper_validation_report") or {}, indent=2, sort_keys=True),
            "```",
            "",
            "## 9. Old-catalogue comparison (candidate-v0.1)",
            "",
            f"- Counts: `{payload.get('old_catalogue_comparison', {}).get('counts')}`",
            "",
            "## 10. Lineage walkthrough",
            "",
            "```json",
            json.dumps(payload.get("lineage_walkthrough") or {}, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## 11. Deferred source register",
            "",
        ]
    )
    for item in payload.get("deferred_source_register") or []:
        lines.append(f"- **{item['source']}** — {item['status']}: {item['reason']}")
    lines.extend(
        [
            "",
            "## 12. Approval identity and catalogue version",
            "",
            f"- Status: `{build.get('status')}`",
            f"- Approved by: `{build.get('approved_by')}` at `{build.get('approved_at')}`",
            f"- Activated at: `{build.get('activated_at')}`",
            f"- Provisional reference version: `{PROVISIONAL_CATALOGUE_VERSION}`",
            "",
        ]
    )
    return "\n".join(lines)


def write_provenance_report(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    build_id: str,
    output: str,
    question_build_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_provenance_report(
        store, config, build_id=build_id, question_build_id=question_build_id
    )
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_provenance_markdown(payload), encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    payload["output_markdown"] = str(path)
    payload["output_json"] = str(json_path)
    return payload
