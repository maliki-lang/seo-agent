"""Low-cost one-repetition AI-question pilot (Phase 14)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from ..config import TrackingConfig
from ..costs import CostLedger
from ..enums import CandidateDecision, Engine, PilotStatus
from ..exceptions import ConfigurationError, DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso

CompleteFn = Callable[[str, str, int], Dict[str, Any]]

FOOTWEAR_TOKENS = (
    "shoe",
    "shoes",
    "sandal",
    "sandals",
    "footwear",
    "walking",
    "comfort",
    "arch",
    "insole",
    "slipper",
    "mule",
    "loafer",
)

_STOPWORDS = {"and", "the", "for", "with", "from", "that", "this", "your", "into", "when"}

COMPETITOR_HINTS = (
    "allbirds",
    "vionic",
    "birkenstock",
    "hoka",
    "skechers",
    "clarks",
    "crocs",
    "orthofeet",
)


def _load_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _host(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _evaluate_answer(
    *,
    question: str,
    answer_text: str,
    citations: Sequence[str],
    expected_elements: Sequence[str],
    target_page: str,
) -> Dict[str, Any]:
    text = (answer_text or "").lower()
    relevant = any(tok in text for tok in FOOTWEAR_TOKENS) or any(
        tok in question.lower() for tok in FOOTWEAR_TOKENS
    )
    citation_hosts = sorted({_host(u) for u in citations if u})
    usable_citations = bool(citation_hosts)
    competitors = sorted({c for c in COMPETITOR_HINTS if c in text or any(c in h for h in citation_hosts)})
    sunnystep_qualify = "sunnystep" in text or any("sunnystep" in h for h in citation_hosts)
    covered = []
    missing = []
    for element in expected_elements:
        tokens = [
            t
            for t in (element or "").lower().replace("/", " ").replace("-", " ").split()
            if len(t) > 2 and t not in _STOPWORDS
        ]
        # Match if a majority of meaningful tokens appear in the answer.
        hits = sum(1 for t in tokens if t in text)
        if tokens and hits >= max(1, (len(tokens) + 1) // 2):
            covered.append(element)
        else:
            missing.append(element)
    coverage_ratio = (len(covered) / len(expected_elements)) if expected_elements else 0.0
    safety_flags = []
    for bad in ("cure", "cures", "fda-approved", "diagnose"):
        if bad in text or bad in question.lower():
            safety_flags.append(bad)

    if safety_flags:
        status = PilotStatus.FAILED.value
        reason = "safety_or_ambiguity"
    elif not relevant:
        status = PilotStatus.FAILED.value
        reason = "not_footwear_relevant"
    elif not usable_citations and coverage_ratio < 0.34:
        status = PilotStatus.INCONCLUSIVE.value
        reason = "weak_citations_and_coverage"
    elif coverage_ratio >= 0.33 and relevant:
        status = PilotStatus.PASSED.value
        reason = "relevant_with_usable_signal"
    else:
        status = PilotStatus.INCONCLUSIVE.value
        reason = "mixed_signal"

    return {
        "status": status,
        "reason": reason,
        "relevant_footwear": relevant,
        "usable_citations": usable_citations,
        "citation_hosts": citation_hosts,
        "competitors": competitors,
        "sunnystep_could_qualify": sunnystep_qualify,
        "expected_element_coverage": round(coverage_ratio, 3),
        "covered_elements": covered,
        "missing_elements": missing,
        "target_page": target_page,
        "safety_flags": safety_flags,
    }


def pilot_ai_questions(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    question_build_id: str,
    engines: Optional[Sequence[str]] = None,
    repetitions: int = 1,
    limit: int = 30,
    complete_fn: Optional[CompleteFn] = None,
    cost_ledger: Optional[CostLedger] = None,
) -> Dict[str, Any]:
    """Run a one-repetition pilot before production three-rep measurement."""
    store.migrate()
    if repetitions != 1:
        raise ConfigurationError("Phase 14 pilot requires --repetitions 1")
    engine_list = [e.strip().lower() for e in (engines or [Engine.CHATGPT.value, Engine.PERPLEXITY.value])]
    for engine in engine_list:
        if engine not in {Engine.CHATGPT.value, Engine.PERPLEXITY.value}:
            raise ConfigurationError(f"Unsupported engine: {engine}")

    builds = store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (question_build_id,))
    if not builds:
        raise DataQualityError(f"Unknown question_build_id {question_build_id}")

    rows = [
        dict(r)
        for r in store.fetchall(
            """
            SELECT * FROM ai_question_candidates
            WHERE build_id = ? AND decision = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (question_build_id, CandidateDecision.SELECTED.value, limit),
        )
    ]
    if not rows:
        raise DataQualityError("No selected AI questions to pilot")

    # Reuse AiVisibilityCollector completion path when no fake injector is supplied.
    if complete_fn is None:
        from ..collectors.ai_visibility import AiVisibilityCollector

        collector = AiVisibilityCollector(config, cost_ledger=cost_ledger or CostLedger(config.daily_cost_cap_usd))

        def complete_fn(engine: str, question: str, repetition: int) -> Dict[str, Any]:
            return collector._complete(engine, question, repetition)  # noqa: SLF001 — intentional reuse

    ledger = cost_ledger or CostLedger(config.daily_cost_cap_usd)
    now = utc_now_iso()
    outcomes: List[Dict[str, Any]] = []
    passed = failed = inconclusive = 0

    for row in rows:
        expected = _load_json(row.get("expected_answer_elements_json"), [])
        per_engine = []
        statuses = []
        for engine in engine_list:
            completion = complete_fn(engine, row["question"], 1)
            cost = Decimal(str(completion.get("cost_usd") or "0"))
            ledger.add(cost, source=f"pilot:{engine}")
            evaluation = _evaluate_answer(
                question=row["question"],
                answer_text=completion.get("text") or "",
                citations=completion.get("citations") or [],
                expected_elements=expected,
                target_page=row.get("proposed_target_page") or "",
            )
            evaluation["engine"] = engine
            evaluation["model"] = completion.get("model")
            evaluation["latency_ms"] = completion.get("latency_ms")
            evaluation["cost_usd"] = float(cost)
            per_engine.append(evaluation)
            statuses.append(evaluation["status"])

        if PilotStatus.FAILED.value in statuses:
            final = PilotStatus.FAILED.value
            failed += 1
        elif all(s == PilotStatus.PASSED.value for s in statuses):
            final = PilotStatus.PASSED.value
            passed += 1
        else:
            final = PilotStatus.INCONCLUSIVE.value
            inconclusive += 1

        pilot_results = {
            "repetitions": 1,
            "engines": engine_list,
            "per_engine": per_engine,
            "final_status": final,
            "piloted_at": now,
        }
        store.update_ai_question_candidate(
            row["question_candidate_id"],
            {
                "pilot_status": final,
                "pilot_results_json": pilot_results,
                "updated_at": now,
            },
        )
        outcomes.append(
            {
                "question_candidate_id": row["question_candidate_id"],
                "pilot_status": final,
                "engines": engine_list,
            }
        )

    return {
        "question_build_id": question_build_id,
        "engines": engine_list,
        "repetitions": 1,
        "piloted": len(outcomes),
        "passed": passed,
        "failed": failed,
        "inconclusive": inconclusive,
        "spent_usd": float(ledger.spent),
        "outcomes": outcomes,
        "note": "Only pilot-passed questions may activate unless an approved override exists.",
    }


def override_pilot(
    store: TrackingStore,
    *,
    question_candidate_id: str,
    approved_by: str,
    reason: str,
) -> Dict[str, Any]:
    if not (approved_by or "").strip() or not (reason or "").strip():
        raise ConfigurationError("pilot override requires approved_by and reason")
    rows = store.fetchall(
        "SELECT * FROM ai_question_candidates WHERE question_candidate_id = ?",
        (question_candidate_id,),
    )
    if not rows:
        raise DataQualityError(f"Unknown question_candidate_id {question_candidate_id}")
    now = utc_now_iso()
    store.update_ai_question_candidate(
        question_candidate_id,
        {
            "pilot_override_by": approved_by.strip(),
            "pilot_override_at": now,
            "pilot_override_reason": reason.strip(),
            "updated_at": now,
        },
    )
    return {
        "question_candidate_id": question_candidate_id,
        "pilot_override_by": approved_by.strip(),
        "pilot_override_at": now,
        "pilot_override_reason": reason.strip(),
    }
