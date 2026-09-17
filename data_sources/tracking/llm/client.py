"""Thin OpenAI chat client for catalogue LLM assessments (injectable for tests)."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

import requests

from ..config import TrackingConfig
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    InvalidResponseError,
    ProviderServerError,
    RateLimitError,
    TransientNetworkError,
)

CompleteFn = Callable[[str, str, Dict[str, Any]], Dict[str, Any]]

DEFAULT_ASSESSMENT_COST = Decimal("0.002")
DEFAULT_MODEL = "gpt-4o-mini"


def _map_status(status_code: int, provider: str) -> Exception:
    if status_code in {401, 403}:
        return AuthenticationError(f"{provider} authentication failed")
    if status_code == 429:
        return RateLimitError(f"{provider} rate limited")
    if status_code >= 500:
        return ProviderServerError(f"{provider} server error {status_code}")
    if status_code >= 400:
        return InvalidResponseError(f"{provider} rejected request ({status_code})")
    return TransientNetworkError(f"{provider} unexpected status {status_code}")


class LlmClient:
    """Provider boundary. Tests inject complete_fn to avoid network calls."""

    def __init__(
        self,
        config: TrackingConfig,
        *,
        complete_fn: Optional[CompleteFn] = None,
        provider: str = "openai",
        model: Optional[str] = None,
    ):
        self.config = config
        self.complete_fn = complete_fn
        self.provider = provider
        self.model = model or getattr(config, "openai_assessment_model", None) or config.openai_visibility_model or DEFAULT_MODEL

    def complete(self, *, system: str, user: str) -> Dict[str, Any]:
        if self.complete_fn is not None:
            return self.complete_fn(system, user, {"provider": self.provider, "model": self.model})
        if self.provider != "openai":
            raise ConfigurationError(f"Unsupported LLM provider: {self.provider}")
        if not self.config.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for LLM assessment")
        headers = {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        started = time.monotonic()
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise TransientNetworkError(f"OpenAI network failure: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if not response.ok:
            raise _map_status(response.status_code, "OpenAI")
        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidResponseError("OpenAI response missing content") from exc
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise InvalidResponseError("OpenAI response was not valid JSON") from exc
        usage = data.get("usage") or {}
        return {
            "output": parsed,
            "raw_text": text,
            "latency_ms": latency_ms,
            "cost_usd": DEFAULT_ASSESSMENT_COST,
            "model": data.get("model") or self.model,
            "provider": self.provider,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "raw": {"id": data.get("id"), "model": data.get("model")},
        }
