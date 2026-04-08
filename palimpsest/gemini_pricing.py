"""Single source of truth for Gemini model pricing.

All prices are per 1,000,000 tokens, in USD. ``estimate_cost`` returns
``None`` for models not listed in ``PRICING`` so callers can distinguish
"unknown cost" from "zero cost".
"""

from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-lite-preview": {"input": 0.02, "output": 0.10},
}


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    """Estimate USD cost for a Gemini call, or None if model is unknown."""
    prices = PRICING.get(model)
    if prices is None:
        return None
    return (prompt_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
