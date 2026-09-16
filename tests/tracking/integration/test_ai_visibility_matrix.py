from datetime import date

import json

from data_sources.tracking.catalogs import load_question_catalog
from data_sources.tracking.collectors.ai_visibility import AiVisibilityCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.enums import Engine
from data_sources.tracking.runner import TrackingRunner
from data_sources.tracking.storage import TrackingStore
from data_sources.tracking.transforms.ai_parser import stable_rates


def _complete(engine, question, repetition):
    mention = "Sunnystep makes comfortable walking shoes." if repetition != 3 else "No brand named here."
    cite = "https://sunnystep.com/collections/walking-shoes" if repetition != 3 else "https://example.com"
    return {
        "text": f"{mention} See {cite}",
        "citations": [cite],
        "latency_ms": 12,
        "cost_usd": "0.01",
        "model": "fixture-model",
        "search_enabled": engine == Engine.PERPLEXITY.value,
        "raw": {"engine": engine, "repetition": repetition},
    }


def test_ai_visibility_writes_120_rows(tmp_path):
    config = TrackingConfig(storage_url=f"sqlite:///{tmp_path / 'ai.db'}")
    questions = load_question_catalog(config)
    assert len(questions) == 20
    store = TrackingStore(config)
    store.migrate()
    collector = AiVisibilityCollector(config, complete_fn=_complete, questions=questions)
    runner = TrackingRunner(config, store=store, ai_collector=collector)
    result = runner.collect_source("ai_visibility", date(2026, 9, 15))
    assert result["status"] == "succeeded"
    assert store.count("ai_answer_runs") == 120
    assert store.count("raw_records") == 120
    rows = store.fetchall("SELECT question_id, engine, repetition_number, raw_answer FROM ai_answer_runs")
    assert all(row["raw_answer"] for row in rows)
    parsed_rows = []
    for row in store.fetchall(
        "SELECT question_id, engine, mentioned_sunnystep, cited_urls, repetition_number FROM ai_answer_runs"
    ):
        parsed_rows.append(
            type("R", (), {
                "question_id": row["question_id"],
                "engine": row["engine"],
                "mentioned_sunnystep": bool(row["mentioned_sunnystep"]),
                "cited_urls": json.loads(row["cited_urls"]),
                "repetition_number": row["repetition_number"],
            })()
        )
    rates = stable_rates(parsed_rows)
    assert rates["eligible_questions"] == 20
    assert rates["mention_rate"] == 1.0
    assert rates["citation_rate"] == 1.0
    second = runner.collect_source("ai_visibility", date(2026, 9, 15))
    assert store.count("ai_answer_runs") == 120
    assert second["result"]["stats"]["unchanged"] == 120
