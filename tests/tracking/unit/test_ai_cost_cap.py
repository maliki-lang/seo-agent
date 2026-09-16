from datetime import date
from decimal import Decimal

import pytest

from data_sources.tracking.catalogs import QuestionRecord
from data_sources.tracking.collectors.ai_visibility import AiVisibilityCollector
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.costs import CostLedger
from data_sources.tracking.exceptions import CostLimitExceeded


def test_ai_visibility_respects_cost_cap():
    config = TrackingConfig()
    questions = [
        QuestionRecord(
            question_id="q001",
            question="What are comfortable shoes?",
            cluster="comfortable shoes (discovery)",
            target_page="https://sunnystep.com/",
            locale="en-SG",
            active=True,
            valid_from="2026-01-01",
            valid_to="",
        )
    ]
    ledger = CostLedger(Decimal("0.05"))

    def complete(engine, question, repetition):
        return {
            "text": "Sunnystep https://sunnystep.com/",
            "citations": ["https://sunnystep.com/"],
            "latency_ms": 1,
            "cost_usd": "0.01",
            "model": "fixture",
            "search_enabled": False,
            "raw": {},
        }

    collector = AiVisibilityCollector(
        config,
        complete_fn=complete,
        questions=questions,
        cost_ledger=ledger,
        repetitions=3,
    )
    with pytest.raises(CostLimitExceeded):
        collector.collect(
            run_id="r1",
            as_of_date=date(2026, 9, 15),
            start_date=date(2026, 9, 15),
            end_date=date(2026, 9, 15),
        )
