"""Phase 14: LLM assessment contracts, question review, and pilots."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue.clusters import derive_clusters
from data_sources.tracking.catalogue.question_pilot import override_pilot, pilot_ai_questions
from data_sources.tracking.catalogue.question_review import (
    evaluate_question_gates,
    export_question_review,
    mark_questions_review_ready,
)
from data_sources.tracking.catalogue.question_sources import classify_source_availability
from data_sources.tracking.catalogue.questions import build_ai_questions
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    LlmAssessmentType,
    LlmValidationStatus,
    PilotStatus,
    QuestionSourceType,
    RunStatus,
    RunType,
)
from data_sources.tracking.llm.assess import assess_subjects
from data_sources.tracking.llm.client import LlmClient
from data_sources.tracking.llm.schemas import validate_assessment_output
from data_sources.tracking.models import GscDailyRow, RunLog
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _config(tmp_path) -> TrackingConfig:
    return TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase14.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=10,
        daily_cost_cap_usd=Decimal("25"),
        openai_api_key="test-key",
    )


def _seed_keyword_and_questions(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    sync_catalogues(store, config)
    store.insert_run(
        RunLog(
            run_id="run-p14",
            run_type=RunType.MANUAL,
            as_of_date=date(2026, 9, 10),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    page = "https://sunnystep.com/collections/walking-shoes"
    queries = [
        ("comfortable walking shoes", 5, 120),
        ("best work shoes for standing", 4, 90),
        ("arch support sandals", 3, 70),
        ("shoes for plantar fasciitis", 2, 55),
        ("office shoes singapore", 3, 80),
        ("wide fit walking sandals", 2, 45),
        ("cushioned everyday shoes", 2, 50),
        ("comfortable flats for work", 2, 48),
        ("best shoes for long walking", 1, 40),
        ("supportive mules singapore", 1, 35),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="run-p14",
                as_of_date=date(2026, 9, 10),
                date=date(2026, 9, 10),
                query=q,
                page=page,
                country="sgp",
                clicks=c,
                impressions=i,
                ctr=c / i,
                position=8.0,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for q, c, i in queries
        ]
    )
    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 10),
        gsc_end=date(2026, 9, 10),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=10),
    )
    derive_clusters(store, build_id=built["build_id"])
    questions = build_ai_questions(store, config, keyword_build_id=built["build_id"], count=20)
    return store, config, built["build_id"], questions["build_id"]


def test_migration_creates_llm_assessments(tmp_path):
    store, config, _, _ = _seed_keyword_and_questions(tmp_path)
    versions = {r["version"] for r in store.fetchall("SELECT version FROM schema_migrations")}
    assert "010_llm_ai_questions" in versions
    cols = {r["name"] for r in store.fetchall("PRAGMA table_info(ai_question_candidates)")}
    assert "source_type" in cols
    assert "pilot_status" in cols


def test_synthetic_draft_source_label(tmp_path):
    store, _, _, q_build = _seed_keyword_and_questions(tmp_path)
    rows = store.fetchall(
        "SELECT source_type, decision_reason FROM ai_question_candidates WHERE build_id = ?",
        (q_build,),
    )
    assert rows
    assert all(r["source_type"] == QuestionSourceType.SYNTHETIC_DRAFT.value for r in rows)


def test_blocked_customer_sources_are_deferred():
    out = classify_source_availability("customer_support")
    assert out["status"] == "blocked"
    assert out["source_type"] == QuestionSourceType.SOURCE_BLOCKED.value


def test_schema_rejects_invented_facts_and_pages():
    ok, errors, _ = validate_assessment_output(
        "semantic_review",
        {
            "customer_need": "comfort",
            "intent_summary": "commercial",
            "business_relevance_rationale": "fits footwear",
            "possible_semantic_duplicates": [],
            "recommended_target_page": "https://evil.example/page",
            "confidence": "high",
            "assumptions": [],
            "risk_flags": [],
            "gsc_clicks": 99,
        },
        allowed_target_pages=["https://sunnystep.com/collections/walking-shoes"],
    )
    assert not ok
    assert any("allowlist" in e for e in errors)
    assert any("gsc_clicks" in e for e in errors)


def test_assess_llm_dry_run_no_provider_call(tmp_path):
    store, config, build_id, _ = _seed_keyword_and_questions(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("provider must not be called in dry-run")

    client = LlmClient(config, complete_fn=boom)
    # dry_run path never uses client, but pass anyway
    out = assess_subjects(
        store,
        config,
        build_id=build_id,
        assessment_type=LlmAssessmentType.SEMANTIC_REVIEW.value,
        scope="reviewed_shortlist",
        limit=5,
        dry_run=True,
        client=client,
    )
    assert out["dry_run"] is True
    assert out["subjects_eligible"] >= 1
    assert "estimated_maximum_cost_usd" in out
    assert store.count("llm_assessments") == 0


def test_assess_llm_valid_and_invalid_outputs(tmp_path):
    store, config, build_id, _ = _seed_keyword_and_questions(tmp_path)
    page = "https://sunnystep.com/collections/walking-shoes"
    calls = {"n": 0}

    def complete_fn(system, user, meta):
        calls["n"] += 1
        payload = json.loads(user)
        packet = payload["evidence_packet"]
        if calls["n"] == 1:
            return {
                "output": {
                    "customer_need": "all-day comfort",
                    "intent_summary": "commercial investigation",
                    "business_relevance_rationale": "matches footwear need",
                    "possible_semantic_duplicates": [],
                    "recommended_target_page": packet["allowlisted_target_pages"][0],
                    "confidence": "medium",
                    "assumptions": ["SERP stable"],
                    "risk_flags": [],
                },
                "latency_ms": 12,
                "cost_usd": Decimal("0.001"),
                "model": "fake-model",
                "provider": "fake",
            }
        return {
            "output": {
                "customer_need": "x",
                "intent_summary": "y",
                "business_relevance_rationale": "z",
                "possible_semantic_duplicates": [],
                "recommended_target_page": "https://not-allowed.example/",
                "confidence": "high",
                "assumptions": [],
                "risk_flags": [],
                "search_volume": 1000,
            },
            "latency_ms": 9,
            "cost_usd": Decimal("0.001"),
            "model": "fake-model",
            "provider": "fake",
        }

    client = LlmClient(config, complete_fn=complete_fn)
    out = assess_subjects(
        store,
        config,
        build_id=build_id,
        assessment_type=LlmAssessmentType.SEMANTIC_REVIEW.value,
        scope="reviewed_shortlist",
        limit=2,
        dry_run=False,
        client=client,
    )
    assert out["created"] == 2
    assert out["invalid"] == 1
    rows = store.fetchall("SELECT validation_status FROM llm_assessments ORDER BY created_at")
    assert rows[0]["validation_status"] == LlmValidationStatus.VALID.value
    assert rows[1]["validation_status"] == LlmValidationStatus.INVALID.value

    # Idempotent reuse of valid assessment
    again = assess_subjects(
        store,
        config,
        build_id=build_id,
        assessment_type=LlmAssessmentType.SEMANTIC_REVIEW.value,
        scope="reviewed_shortlist",
        limit=1,
        dry_run=False,
        client=client,
    )
    assert again["reused"] == 1
    assert store.fetchall("SELECT COUNT(*) AS n FROM llm_assessments")[0]["n"] == 2


def test_question_rewrite_and_pilot(tmp_path):
    store, config, _, q_build = _seed_keyword_and_questions(tmp_path)

    def rewrite_fn(system, user, meta):
        payload = json.loads(user)
        q = payload["evidence_packet"]["question"]
        return {
            "output": {
                "rewritten_question": q.replace("What", "Which").rstrip("?") + " this year?",
                "naturalness": "passed",
                "rationale": "more natural",
                "risk_flags": [],
            },
            "latency_ms": 5,
            "cost_usd": Decimal("0.001"),
            "model": "fake",
            "provider": "fake",
        }

    assess_subjects(
        store,
        config,
        build_id=q_build,
        assessment_type=LlmAssessmentType.QUESTION_REWRITE.value,
        scope="reviewed_shortlist",
        limit=3,
        client=LlmClient(config, complete_fn=rewrite_fn),
    )

    def fake_complete(engine, question, repetition):
        return {
            "text": (
                "For everyday comfort and fit, choose cushioned walking shoes with arch support. "
                "Match the use case and situation (office or long walks), and use practical shopping "
                "criteria like humidity, width, and return policy in Singapore."
            ),
            "citations": ["https://sunnystep.com/collections/walking-shoes", "https://example.com/a"],
            "latency_ms": 3,
            "cost_usd": Decimal("0.001"),
            "model": "fake",
        }

    pilot = pilot_ai_questions(
        store,
        config,
        question_build_id=q_build,
        engines=["chatgpt"],
        repetitions=1,
        limit=5,
        complete_fn=fake_complete,
    )
    assert pilot["piloted"] == 5
    assert pilot["passed"] >= 1
    assert all(
        r["pilot_status"] != PilotStatus.NOT_RUN.value
        for r in store.fetchall(
            "SELECT pilot_status FROM ai_question_candidates WHERE build_id = ? AND decision = 'selected' LIMIT 5",
            (q_build,),
        )
    )


def test_question_gates_and_export(tmp_path):
    store, config, _, q_build = _seed_keyword_and_questions(tmp_path)
    before = evaluate_question_gates(store, config, question_build_id=q_build, production_count=20)
    assert before["gate_status"] == "failed"
    mark_questions_review_ready(store, question_build_id=q_build, reviewed_by="Ting")
    after = evaluate_question_gates(store, config, question_build_id=q_build, production_count=20)
    assert after["gate_status"] == "pass"
    path = tmp_path / "q-review.csv"
    exported = export_question_review(store, question_build_id=q_build, output=str(path))
    assert exported["rows"] == 20
    assert path.exists()


def test_pilot_override_requires_reason(tmp_path):
    store, _, _, q_build = _seed_keyword_and_questions(tmp_path)
    qid = store.fetchall(
        "SELECT question_candidate_id FROM ai_question_candidates WHERE build_id = ? LIMIT 1",
        (q_build,),
    )[0]["question_candidate_id"]
    out = override_pilot(
        store,
        question_candidate_id=qid,
        approved_by="Ting",
        reason="pilot flake; wording validated manually",
    )
    assert out["pilot_override_by"] == "Ting"
