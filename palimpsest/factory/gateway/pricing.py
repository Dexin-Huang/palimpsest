"""Model pricing: overrides first, then the maintained genai-prices database.

``OVERRIDES`` pins prices we want to control exactly (per 1M tokens, USD);
everything else resolves through the ``genai-prices`` package's bundled
database, which tracks current provider pricing. ``estimate_cost`` returns
``None`` for models neither source knows, so callers can distinguish
"unknown cost" from "zero cost".
"""

from __future__ import annotations

from genai_prices import Usage, calc_price

OVERRIDES: dict[str, dict[str, float]] = {
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-lite-preview": {"input": 0.02, "output": 0.10},
}


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    prices = OVERRIDES.get(model)
    if prices is not None:
        return (prompt_tokens * prices["input"]
                + output_tokens * prices["output"]) / 1_000_000
    try:
        priced = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            model_ref=model,
        )
    except Exception:  # unknown model / unparseable ref
        return None
    return float(priced.total_price)
