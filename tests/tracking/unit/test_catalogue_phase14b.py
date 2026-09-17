"""Phase 14b/13b: mid-funnel LLM semantic authority and portfolio preference."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.apply_semantics import (
    apply_llm_family_regroup,
    write_pool_semantic_fields,
)
from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogue.classify import classify_build
from data_sources.tracking.catalogue.families import derive_families_for_build
from data_sources.tracking.catalogue.selection import select_portfolio
from data_sources.tracking.catalogue.target_pages import evaluate_targets_for_build
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    LlmAssessmentType,
    SemanticAuthority,
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
        storage_url=f"sqlite:///{tmp_path / 'phase14b.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=5,
        catalogue_alternate_limit=2,
        daily_cost_cap_usd=Decimal("25"),
        openai_api_key="test-key",
    )


def _seed(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    sync_catalogues(store, config)
    store.insert_run(
        RunLog(
            run_id="run-14b",
            run_type=RunType.MANUAL,
            as_of_date=date(2026, 9, 10),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    page = "https://sunnystep.com/collections/walking-shoes"
    queries = [
        ("comfortable walking shoes", 5, 120),
        ("best walking shoes for work", 4, 100),
        ("walking shoes for standing", 3, 90),
        ("arch support sandals", 2, 70),
        ("office shoes singapore", 3, 80),
        ("cushioned everyday shoes", 2, 60),
        ("wide fit walking sandals", 1, 50),
        ("supportive mules singapore", 1, 40),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="run-14b",
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
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=8),
    )
    classify_build(store, config, build_id=built["build_id"])
    evaluate_targets_for_build(store, config, build_id=built["build_id"])
    derive_families_for_build(store, config, build_id=built["build_id"])
    return store, config, built["build_id"], page


def test_pool_semantic_schema_rejects_invented_page():
    ok, errors, _ = validate_assessment_output(
        "pool_semantic",
        {
            "customer_need": "all-day comfort",
            "search_intent": "problem_solution",
            "business_relevance": "relevant",
            "business_relevance_rationale": "fits footwear need",
            "family_key": "walking shoes comfort",
            "is_family_representative": True,
            "semantic_duplicates": [],
            "actionability": "optimize_existing",
            "actionability_rationale": "page exists",
            "recommended_target_page": "https://evil.example/x",
            "no_suitable_target": False,
            "confidence": "medium",
            "assumptions": [],
            "risk_flags": [],
            "gsc_clicks": 9,
        },
        allowed_target_pages=["https://sunnystep.com/collections/walking-shoes"],
        allowed_evidence_refs=["candidate:abc"],
    )
    assert not ok
    assert any("allowlist" in e for e in errors)
    assert any("gsc_clicks" in e for e in errors)


def test_pool_semantic_schema_coerces_common_model_slips():
    ok, errors, normalized = validate_assessment_output(
        "pool_semantic",
        {
            "customer_need": "all-day comfort",
            "search_intent": "transactional_category",
            "business_relevance": "relevant",
            "business_relevance_rationale": "fits footwear need",
            "family_key": "walking shoes",
            "is_family_representative": "true",
            "semantic_duplicates": "none",
            "actionability": "actionable",
            "actionability_rationale": "page exists",
            "recommended_target_page": "https://sunnystep.com/collections/walking-shoes",
            "no_suitable_target": "false",
            "confidence": 0.9,
            "assumptions": "collection covers walking intent",
            "risk_flags": [],
        },
        allowed_target_pages=["https://sunnystep.com/collections/walking-shoes"],
    )
    assert ok, errors
    assert normalized["actionability"] == "optimize_existing"
    assert normalized["confidence"] == "high"
    assert normalized["is_family_representative"] is True
    assert normalized["no_suitable_target"] is False
    assert normalized["assumptions"] == ["collection covers walking intent"]
    assert normalized["semantic_duplicates"] == []


def test_eligible_pool_dry_run_and_apply(tmp_path):
    store, config, build_id, page = _seed(tmp_path)
    dry = assess_subjects(
        store,
        config,
        build_id=build_id,
        assessment_type=LlmAssessmentType.POOL_SEMANTIC.value,
        scope="eligible_nonbrand",
        limit=10,
        dry_run=True,
    )
    assert dry["dry_run"] is True
    assert dry["subjects_eligible"] >= 1
    assert dry["prompt_version"] == "pool_semantic_v2"

    def complete_fn(system, user, meta):
        payload = json.loads(user)
        packet = payload["evidence_packet"]
        kw = packet["keyword"]
        return {
            "output": {
                "customer_need": "comfortable walking footwear",
                "search_intent": "transactional_category",
                "business_relevance": "relevant",
                "business_relevance_rationale": "core product category",
                "family_key": "walking shoes",
                "is_family_representative": "walking" in kw and "best" not in kw and "standing" not in kw,
                "semantic_duplicates": [],
                "actionability": "optimize_existing",
                "actionability_rationale": "owner collection exists",
                "recommended_target_page": page,
                "no_suitable_target": False,
                "confidence": "high",
                "assumptions": ["collection covers walking intent"],
                "risk_flags": [],
            },
            "latency_ms": 4,
            "cost_usd": Decimal("0.001"),
            "model": "fake",
            "provider": "fake",
        }

    out = assess_subjects(
        store,
        config,
        build_id=build_id,
        assessment_type=LlmAssessmentType.POOL_SEMANTIC.value,
        scope="eligible_nonbrand",
        limit=8,
        dry_run=False,
        client=LlmClient(config, complete_fn=complete_fn),
    )
    assert out["created"] >= 1
    assert out["family_regroup"]["llm_families"] >= 1
    llm_rows = store.fetchall(
        "SELECT * FROM keyword_candidates WHERE build_id = ? AND semantic_authority = ?",
        (build_id, SemanticAuthority.LLM.value),
    )
    assert llm_rows
    assert all(r["llm_intent"] for r in llm_rows)
    assert all(r["family_method"] == "llm_pool_semantic_v1" for r in llm_rows)
    assert store.fetchall(
        "SELECT COUNT(*) AS n FROM keyword_families WHERE build_id = ? AND family_method = ?",
        (build_id, "llm_pool_semantic_v1"),
    )[0]["n"] >= 1


def test_select_portfolio_require_llm_semantics(tmp_path):
    store, config, build_id, page = _seed(tmp_path)
    # Write LLM fields for a subset and regroup.
    cands = [
        dict(r)
        for r in store.fetchall(
            "SELECT * FROM keyword_candidates WHERE build_id = ? AND eligibility_status IN ('eligible','eligible_with_review')",
            (build_id,),
        )
    ][:4]
    for i, cand in enumerate(cands):
        write_pool_semantic_fields(
            store,
            candidate_id=cand["candidate_id"],
            normalized={
                "customer_need": "comfort",
                "search_intent": "transactional_category",
                "business_relevance": "relevant",
                "business_relevance_rationale": "product",
                "family_key": "walking shoes",
                "is_family_representative": i == 0,
                "semantic_duplicates": [],
                "actionability": "optimize_existing",
                "actionability_rationale": "ok",
                "recommended_target_page": page,
                "no_suitable_target": False,
                "confidence": "medium",
            },
            assessment_id=f"assess-{i}",
        )
    apply_llm_family_regroup(store, build_id=build_id)

    blocked = select_portfolio(
        store, config, build_id=build_id, require_llm_semantics=True, require_serper=False
    )
    # Only LLM primaries eligible — at least one selected if actionable.
    selected = store.fetchall(
        "SELECT candidate_id, semantic_authority FROM keyword_candidates WHERE build_id = ? AND decision = 'selected'",
        (build_id,),
    )
    assert selected
    assert all(r["semantic_authority"] == SemanticAuthority.LLM.value for r in selected)
    assert blocked.get("require_llm_semantics") is True
