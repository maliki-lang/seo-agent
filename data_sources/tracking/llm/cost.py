"""Deterministic dry-run cost estimation for LLM assessments."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

# Conservative defaults used when provider token estimates are unavailable.
CHARS_PER_TOKEN = 4
DEFAULT_INPUT_TOKENS = 900
DEFAULT_OUTPUT_TOKENS = 350
DEFAULT_USD_PER_1K_INPUT = Decimal("0.00015")
DEFAULT_USD_PER_1K_OUTPUT = Decimal("0.0006")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_call_cost(
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    usd_per_1k_input: Decimal = DEFAULT_USD_PER_1K_INPUT,
    usd_per_1k_output: Decimal = DEFAULT_USD_PER_1K_OUTPUT,
) -> Decimal:
    in_tok = Decimal(input_tokens if input_tokens is not None else DEFAULT_INPUT_TOKENS)
    out_tok = Decimal(output_tokens if output_tokens is not None else DEFAULT_OUTPUT_TOKENS)
    return (in_tok / Decimal(1000) * usd_per_1k_input) + (out_tok / Decimal(1000) * usd_per_1k_output)


def estimate_batch_cost(call_count: int, *, per_call: Optional[Decimal] = None) -> Dict[str, Any]:
    count = max(0, int(call_count))
    unit = per_call if per_call is not None else estimate_call_cost()
    return {
        "estimated_calls": count,
        "estimated_tokens_per_call": DEFAULT_INPUT_TOKENS + DEFAULT_OUTPUT_TOKENS,
        "estimated_maximum_cost_usd": float(unit * count),
        "cost_basis": {
            "input_tokens": DEFAULT_INPUT_TOKENS,
            "output_tokens": DEFAULT_OUTPUT_TOKENS,
            "usd_per_1k_input": float(DEFAULT_USD_PER_1K_INPUT),
            "usd_per_1k_output": float(DEFAULT_USD_PER_1K_OUTPUT),
        },
    }
