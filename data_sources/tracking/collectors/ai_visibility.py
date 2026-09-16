from __future__ import annotations

import time
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from ..catalogs import QuestionRecord, active_questions
from ..collectors.base import Collector, CollectorResult
from ..config import TrackingConfig
from ..costs import CostLedger
from ..enums import Engine, Source
from ..exceptions import AuthenticationError, ConfigurationError
from ..models import AiAnswerRow
from ..retries import classify_http_error
from ..transforms.ai_parser import (
    PARSER_VERSION,
    competitor_catalogue_from_config,
    parse_answer,
)
from ..transforms.normalize import utc_now_iso

CompleteFn = Callable[[str, str, int], Dict[str, Any]]
DEFAULT_COMPLETION_COST = Decimal("0.01")


def _map_status(status: int, name: str) -> Exception:
    mapped = classify_http_error(status, f"{name} request failed")
    if status in {401, 403}:
        return AuthenticationError(f"{name} authentication failed.")
    return mapped


class AiVisibilityCollector(Collector):
    source = Source.AI_VISIBILITY.value

    def __init__(
        self,
        config: TrackingConfig,
        *,
        complete_fn: Optional[CompleteFn] = None,
        questions: Optional[Sequence[QuestionRecord]] = None,
        cost_ledger: Optional[CostLedger] = None,
        question_limit: int = 0,
        engines: Optional[Sequence[Engine]] = None,
        repetitions: int = 3,
    ):
        self.config = config
        self._complete_fn = complete_fn
        self._questions = list(questions) if questions is not None else None
        self.cost_ledger = cost_ledger
        self.question_limit = question_limit
        self.engines = list(engines) if engines is not None else [Engine.CHATGPT, Engine.PERPLEXITY]
        self.repetitions = repetitions
        self.competitors = competitor_catalogue_from_config("config/competitors.yaml")

    def _complete(self, engine: str, question: str, repetition: int) -> Dict[str, Any]:
        if self._complete_fn is not None:
            return self._complete_fn(engine, question, repetition)
        if engine == Engine.CHATGPT.value:
            return self._complete_openai(question)
        if engine == Engine.PERPLEXITY.value:
            return self._complete_perplexity(question)
        raise ConfigurationError(f"Unknown AI engine {engine}")

    def _complete_openai(self, question: str) -> Dict[str, Any]:
        if not self.config.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for ChatGPT visibility")
        model = self.config.openai_visibility_model or "gpt-4o-mini"
        headers = {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "temperature": 0.2,
        }
        search_enabled = False
        started = time.monotonic()
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={**body, "tools": [{"type": "web_search"}]},
            timeout=60,
        )
        if response.status_code == 400:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=60,
            )
            search_enabled = False
        else:
            search_enabled = response.ok
        latency_ms = int((time.monotonic() - started) * 1000)
        if not response.ok:
            raise _map_status(response.status_code, "OpenAI")
        data = response.json()
        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        return {
            "text": text,
            "citations": [],
            "latency_ms": latency_ms,
            "cost_usd": DEFAULT_COMPLETION_COST,
            "model": data.get("model") or model,
            "search_enabled": search_enabled,
            "raw": {"id": data.get("id"), "model": data.get("model")},
        }

    def _complete_perplexity(self, question: str) -> Dict[str, Any]:
        if not self.config.perplexity_api_key:
            raise ConfigurationError("PERPLEXITY_API_KEY is required for Perplexity visibility")
        model = self.config.perplexity_visibility_model or "sonar"
        started = time.monotonic()
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.perplexity_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": question}],
                "temperature": 0.2,
            },
            timeout=60,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if not response.ok:
            raise _map_status(response.status_code, "Perplexity")
        data = response.json()
        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        citations = data.get("citations") or []
        if not isinstance(citations, list):
            citations = []
        return {
            "text": text,
            "citations": [str(item) for item in citations],
            "latency_ms": latency_ms,
            "cost_usd": DEFAULT_COMPLETION_COST,
            "model": data.get("model") or model,
            "search_enabled": True,
            "raw": {"model": data.get("model"), "citation_count": len(citations)},
        }

    def collect(
        self,
        *,
        run_id: str,
        as_of_date: date,
        start_date: date,
        end_date: date,
        **kwargs: Any,
    ) -> CollectorResult:
        questions = list(self._questions if self._questions is not None else active_questions(self.config))
        questions = [row for row in questions if row.active]
        if self.question_limit:
            questions = questions[: self.question_limit]
        rows: List[AiAnswerRow] = []
        raw_payloads: List[Dict[str, Any]] = []
        cost = Decimal("0")
        collected_at = utc_now_iso()
        for record in questions:
            for engine in self.engines:
                for repetition in range(1, self.repetitions + 1):
                    if self.cost_ledger is not None:
                        self.cost_ledger.add(DEFAULT_COMPLETION_COST, source=self.source)
                    payload = self._complete(engine.value, record.question, repetition)
                    text = str(payload.get("text") or "")
                    parsed = parse_answer(
                        text,
                        extra_urls=payload.get("citations") or [],
                        competitor_catalogue=self.competitors,
                    )
                    api_cost = Decimal(str(payload.get("cost_usd") or DEFAULT_COMPLETION_COST))
                    cost += api_cost
                    raw_id = str(uuid.uuid4())
                    raw_payloads.append(
                        {
                            "raw_record_id": raw_id,
                            "endpoint_or_operation": f"ai.{engine.value}",
                            "payload": payload.get("raw") or {"text_length": len(text)},
                        }
                    )
                    rows.append(
                        AiAnswerRow(
                            run_id=run_id,
                            as_of_date=as_of_date,
                            engine=engine,
                            question_id=record.question_id,
                            question=record.question,
                            repetition_number=repetition,
                            raw_answer=text,
                            mentioned_sunnystep=parsed.mentioned_sunnystep,
                            cited_urls=parsed.cited_urls,
                            named_competitors=parsed.named_competitors,
                            target_cluster=record.cluster,
                            target_page=record.target_page,
                            api_cost_usd=api_cost,
                            latency_ms=int(payload.get("latency_ms") or 0),
                            model=str(payload.get("model") or ""),
                            search_enabled=bool(payload.get("search_enabled")),
                            parser_version=PARSER_VERSION,
                            collected_at=collected_at,
                            raw_record_id=raw_id,
                        )
                    )
        return CollectorResult(
            source=self.source,
            rows=rows,
            cost_usd=cost,
            raw_payloads=raw_payloads,
            warnings=[
                "Incomplete question/engine sets are excluded from published mention/citation rates until all three repetitions exist."
            ],
        )
