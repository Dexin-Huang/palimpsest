"""Model pricing, resolved through the genai-prices database.

``estimate_cost`` returns ``None`` for models the database doesn't know,
so callers can distinguish "unknown cost" from "zero cost".
"""

from __future__ import annotations

from genai_prices import Usage, calc_price


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    try:
        priced = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            model_ref=model,
        )
    except Exception:  # unknown model / unparseable ref
        return None
    return float(priced.total_price)
