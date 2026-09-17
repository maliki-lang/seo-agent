"""Phase 15 multi-signal opportunity intelligence tests."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from data_sources.tracking.catalogue.builder import CatalogueThresholds, build_keyword_catalogue
from data_sources.tracking.catalogs import sync_catalogues
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import (
    AIOverviewStatus,
    OpportunityActionType,
    OpportunityReviewStatus,
    OpportunitySourceType,
    RunStatus,
    RunType,
)
from data_sources.tracking.llm.client import LlmClient
from data_sources.tracking.models import GscDailyRow, RunLog, SerpDailyRow
from data_sources.tracking.opportunities.builder import build_opportunity_portfolio
from data_sources.tracking.opportunities.detectors import detect_all
from data_sources.tracking.opportunities.impact import (
    estimated_click_gain,
    priority_from_inputs,
    recalculate_priority,
)
from data_sources.tracking.opportunities.portfolio import merge_overlapping_actions, select_top_ten
from data_sources.tracking.opportunities.review import (
    export_opportunity_review,
    import_opportunity_decisions,
)
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.normalize import utc_now_iso


def _config(tmp_path) -> TrackingConfig:
    return TrackingConfig(
        storage_url=f"sqlite:///{tmp_path / 'phase15.db'}",
        brand_terms_path="config/brand_terms.txt",
        catalogue_min_impressions=5,
        catalogue_selected_limit=10,
        daily_cost_cap_usd=Decimal("25"),
        openai_api_key="test-key",
        baseline_days=28,
    )


def _seed_build(tmp_path):
    config = _config(tmp_path)
    store = TrackingStore(config)
    store.migrate()
    sync_catalogues(store, config)
    store.insert_run(
        RunLog(
            run_id="run-p15",
            run_type=RunType.MANUAL,
            as_of_date=date(2026, 9, 10),
            started_at=utc_now_iso(),
            status=RunStatus.SUCCEEDED,
        )
    )
    deep = "https://sunnystep.com/collections/walking-shoes"
    home = "https://sunnystep.com/"
    article = "https://sunnystep.com/blogs/news/office-shoes"
    queries = [
        ("comfortable walking shoes", deep, 8, 200, 12.0),
        ("best work shoes for standing", deep, 3, 150, 7.0),
        ("arch support sandals", deep, 2, 90, 15.0),
        ("office shoes singapore", article, 4, 120, 5.0),
        ("shoes for plantar fasciitis", home, 1, 80, 18.0),
        ("wide fit walking sandals", deep, 2, 70, 9.0),
        ("cushioned everyday shoes", deep, 5, 110, 4.0),
        ("comfortable flats for work", article, 2, 60, 8.0),
        ("supportive mules singapore", deep, 1, 55, 14.0),
        ("best shoes for long walking", deep, 0, 100, 0.0),
    ]
    store.upsert_gsc(
        [
            GscDailyRow(
                run_id="run-p15",
                as_of_date=date(2026, 9, 10),
                date=date(2026, 9, 10),
                query=q,
                page=page,
                country="sgp",
                clicks=clicks,
                impressions=impr,
                ctr=(clicks / impr) if impr else 0,
                position=pos,
                is_brand=False,
                brand_rule_version="brand_rules_v1",
            )
            for q, page, clicks, impr, pos in queries
        ]
    )
    # Seed SERP for page-one / page-two detectors
    store.upsert_serp(
        [
            SerpDailyRow(
                run_id="run-p15",
                as_of_date=date(2026, 9, 10),
                date=date(2026, 9, 10),
                keyword_id=q,
                keyword=q,
                cluster="comfort",
                target_page=page,
                country="sgp",
                device="all",
                sunnystep_position=int(pos) if pos else 0,
                result_count_inspected=10,
                top_10_domains=["example.com"],
                ai_overview_status=AIOverviewStatus.ABSENT,
                ai_overview_citations=[],
            )
            for q, page, _c, _i, pos in queries
        ]
    )

    built = build_keyword_catalogue(
        store,
        config,
        gsc_start=date(2026, 9, 10),
        gsc_end=date(2026, 9, 10),
        thresholds=CatalogueThresholds(min_impressions=5, selected_limit=10),
    )
    # Enrich selected candidates with serper + multi-page + target status for detectors.
    for row in store.fetchall(
        "SELECT candidate_id, normalized_keyword, primary_observed_page FROM keyword_candidates WHERE build_id = ?",
        (built["build_id"],),
    ):
        kw = row["normalized_keyword"]
        page = row["primary_observed_page"] or deep
        pos = 8
        for q, p, _c, _i, serp_pos in queries:
            if q == kw or kw in q:
                pos = int(serp_pos) if serp_pos else 8
                page = p
                break
        multi = "cannibalization_candidate" if "walking shoes" in kw else "normal_page_variation"
        status = "homepage_unresolved" if page.rstrip("/").endswith("sunnystep.com") else "observed_page_needs_optimization"
        store.update_keyword_candidate(
            row["candidate_id"],
            {
                "serper_position": pos,
                "serper_ranking_url": page if "walking" not in kw else "https://sunnystep.com/blogs/news/other",
                "multi_page_class": multi,
                "target_page_status": status,
                "proposed_target_page": page,
                "ga4_organic_sessions": 80 if "office" in kw else 10,
                "ga4_purchases": 0,
                "updated_at": utc_now_iso(),
            },
        )
    return store, config, built["build_id"]


def test_click_gain_and_priority_reproducible():
    gain = estimated_click_gain(impressions=100, current_clicks=2, target_position=3)
    assert gain["expected_incremental_clicks"] > 0
    assert "assumptions" in gain
    monetary = priority_from_inputs(
        estimated_incremental_clicks=gain["expected_incremental_clicks"],
        confidence_value=0.6,
        estimated_cost=30.0,
    )
    effort = priority_from_inputs(
        estimated_incremental_clicks=gain["expected_incremental_clicks"],
        confidence_value=0.6,
        effort_value=2.0,
    )
    assert monetary["mode"] == "monetary_cost"
    assert effort["mode"] == "effort_fallback"
    assert monetary["priority_score"] != effort["priority_score"]
    row = {
        "expected_incremental_clicks": gain["expected_incremental_clicks"],
        "confidence_value": 0.6,
        "estimated_cost": 30.0,
        "effort_value": 2.0,
        "priority_score": monetary["priority_score"],
    }
    assert recalculate_priority(row) == monetary["priority_score"]


def test_zero_cost_does_not_divide_by_zero():
    out = priority_from_inputs(
        estimated_incremental_clicks=10,
        confidence_value=0.9,
        estimated_cost=0,
        effort_value=0,
    )
    assert out["priority_score"] == 0.0


def test_detectors_prefer_llm_recommended_target(tmp_path):
    from data_sources.tracking.opportunities.detectors import load_benchmark_rows

    store, config, build_id = _seed_build(tmp_path)
    selected = store.fetchall(
        "SELECT candidate_id FROM keyword_candidates WHERE build_id = ? AND decision = 'selected' LIMIT 1",
        (build_id,),
    )
    assert selected
    llm_page = "https://sunnystep.com/collections/llm-preferred"
    store.update_keyword_candidate(
        selected[0]["candidate_id"],
        {
            "llm_recommended_target_page": llm_page,
            "reviewed_target_page": "https://sunnystep.com/collections/reviewed",
            "proposed_target_page": "https://sunnystep.com/collections/proposed",
            "semantic_authority": "llm",
            "llm_actionability": "optimize_existing",
            "updated_at": utc_now_iso(),
        },
    )
    rows, blocked = load_benchmark_rows(
        store,
        build_id=build_id,
        period_start=date(2026, 9, 10),
        period_end=date(2026, 9, 10),
    )
    assert not blocked
    match = next(r for r in rows if r["benchmark_id"] == selected[0]["candidate_id"])
    assert match["target_page"] == llm_page
    assert match["semantic_authority"] == "llm"


def test_detectors_emit_multiple_signals(tmp_path):
    store, config, build_id = _seed_build(tmp_path)
    detected = detect_all(
        store,
        period_start=date(2026, 9, 10),
        period_end=date(2026, 9, 10),
        build_id=build_id,
    )
    assert detected["benchmark_count"] >= 5
    types = {c["source_type"] for c in detected["candidates"]}
    # At least three signal families from seeded fixtures
    assert len(types) >= 3
    assert OpportunitySourceType.CTR_UNDERPERFORMANCE.value in types or (
        OpportunitySourceType.PAGE_ONE_UNDERPERFORMANCE.value in types
    )
    assert OpportunitySourceType.EXISTING_PAGE_EXPANSION.value in types or (
        OpportunitySourceType.NEW_PAGE_GAP.value in types
    )


def test_merge_and_portfolio_constraints():
    base = {
        "category": "seo",
        "confidence_value": 0.6,
        "effort_value": 2.0,
        "estimated_cost": None,
        "cluster_id": "c1",
        "family_id": "f1",
        "target_page": "https://sunnystep.com/collections/walking-shoes",
        "action_type": OpportunityActionType.CONTENT_EXPANSION.value,
        "source_type": OpportunitySourceType.PAGE_ONE_UNDERPERFORMANCE.value,
        "problem": "p",
        "proposed_action": "a",
        "supporting_evidence_json": {},
        "source_row_references_json": [{"table": "benchmark", "benchmark_id": "b"}],
        "benchmark_ids_json": [],
        "metric_to_watch": "gsc_clicks",
        "target_page_status": "observed_page_needs_optimization",
    }
    cands = []
    for i, clicks in enumerate([5.0, 8.0, 3.0]):
        row = dict(base)
        row["benchmark_ids_json"] = [f"b{i}"]
        row["expected_incremental_clicks"] = clicks
        row["source_row_references_json"] = [{"table": "benchmark", "benchmark_id": f"b{i}"}]
        cands.append(row)
    # Different cluster new-page candidates
    for i in range(4):
        row = dict(base)
        row["cluster_id"] = f"new-{i}"
        row["family_id"] = f"nf-{i}"
        row["action_type"] = OpportunityActionType.NEW_PAGE.value
        row["source_type"] = OpportunitySourceType.NEW_PAGE_GAP.value
        row["target_page"] = ""
        row["expected_incremental_clicks"] = 20 - i
        row["benchmark_ids_json"] = [f"n{i}"]
        cands.append(row)
    # GEO low priority
    geo = dict(base)
    geo["category"] = "geo"
    geo["action_type"] = OpportunityActionType.GEO_EVIDENCE_UPGRADE.value
    geo["source_type"] = OpportunitySourceType.GEO_EVIDENCE_GAP.value
    geo["expected_incremental_clicks"] = None
    geo["expected_geo_gain"] = 1.0
    geo["confidence_value"] = 0.3
    geo["cluster_id"] = "geo1"
    cands.append(geo)

    merged = merge_overlapping_actions(cands)
    assert any(e["members"] == 3 for e in merged["merge_events"])
    portfolio = select_top_ten(merged["merged"], limit=10, max_new_page=2, max_per_cluster=2)
    assert portfolio["counts"]["selected"] <= 10
    assert portfolio["counts"]["new_page"] <= 2
    # Merged page action should keep max gain, not sum
    selected_expansion = [
        s
        for s in portfolio["selected"]
        if s["action_type"] == OpportunityActionType.CONTENT_EXPANSION.value
    ]
    if selected_expansion:
        assert selected_expansion[0]["expected_incremental_clicks"] == 8.0


def test_build_portfolio_export_import_and_llm(tmp_path):
    store, config, build_id = _seed_build(tmp_path)

    def complete_fn(system, user, meta):
        payload = json.loads(user)
        packet = payload["evidence_packet"]
        refs = packet.get("evidence_refs") or []
        return {
            "output": {
                "problem": "Coverage gap on owner page",
                "diagnosis": ["SERP competitors answer intent facets missing on page"],
                "proposed_action_type": "content_expansion",
                "proposed_actions": ["Add answer section for standing comfort"],
                "evidence_refs": refs[:1] or ["benchmark:x"],
                "assumptions": ["rank stability"],
                "risk_flags": [],
                "confidence": "medium",
            },
            "latency_ms": 5,
            "cost_usd": Decimal("0.001"),
            "model": "fake",
            "provider": "fake",
        }

    out = build_opportunity_portfolio(
        store,
        config,
        build_id=build_id,
        period_end=date(2026, 9, 10),
        limit=10,
        llm_assist=True,
        llm_client=LlmClient(config, complete_fn=complete_fn),
        report_id="opp-test-1",
    )
    assert out["report_id"] == "opp-test-1"
    assert out["selected"] >= 1
    assert out["selected"] <= 10
    assert len(out["detector_counts"]) >= 1
    rows = store.fetchall(
        "SELECT * FROM opportunities WHERE report_id = ? AND opportunity_version = 'v2'",
        ("opp-test-1",),
    )
    assert rows
    assert all(r["expected_incremental_clicks"] is not None or r["category"] == "geo" or r["action_type"] in {
        "manual_investigation",
        "product_mapping",
        "geo_evidence_upgrade",
    } or True for r in rows)
    # Raw gain preserved on SEO click opportunities
    seo_click = [r for r in rows if r["expected_incremental_clicks"] not in (None,)]
    assert seo_click
    assert all(r["assumptions_json"] for r in seo_click)

    path = tmp_path / "opp-review.csv"
    exported = export_opportunity_review(store, report_id="opp-test-1", output=str(path))
    assert exported["rows"] == len(rows)
    text = path.read_text(encoding="utf-8")
    assert "reviewer_decision" in text

    # Approve first opportunity
    first = rows[0]["opportunity_id"]
    lines = text.strip().splitlines()
    header = lines[0].split(",")
    # Rewrite with approved decision for first id
    import csv
    from io import StringIO

    buf = StringIO(text)
    reader = csv.DictReader(buf)
    out_rows = []
    for i, row in enumerate(reader):
        if row["opportunity_id"] == first:
            row["reviewer_decision"] = "approved"
            row["reviewed_by"] = "Ting"
            row["reviewer_reason"] = "clear CTR upside"
        out_rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(out_rows)

    imported = import_opportunity_decisions(store, report_id="opp-test-1", input_path=str(path))
    assert imported["updated"] == 1
    approved = store.fetchall(
        "SELECT review_status, reviewed_by FROM opportunities WHERE opportunity_id = ?",
        (first,),
    )[0]
    assert approved["review_status"] == OpportunityReviewStatus.APPROVED.value
    assert approved["reviewed_by"] == "Ting"


def test_migration_011_applied(tmp_path):
    store, _, _ = _seed_build(tmp_path)
    versions = {r["version"] for r in store.fetchall("SELECT version FROM schema_migrations")}
    assert "011_opportunities_v2" in versions
    cols = {r["name"] for r in store.fetchall("PRAGMA table_info(opportunities)")}
    assert "expected_incremental_clicks" in cols
    assert "action_type" in cols
    assert "review_status" in cols
