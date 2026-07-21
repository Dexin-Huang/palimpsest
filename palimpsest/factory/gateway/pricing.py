"""Model pricing resolved through ``genai-prices``.

The ``latest`` aliases are priced as the concrete releases they currently
target. New stable models may briefly precede the package catalog, so Google's
published standard rates cover that narrow gap. Unknown models still return
``None``, never a misleading zero.
"""

from __future__ import annotations

from genai_prices import Usage, calc_price

_LATEST_PRICING_TARGETS = {
    "gemini-flash-latest": "gemini-3.6-flash",
    "gemini-flash-lite-latest": "gemini-3.5-flash-lite",
}


_FALLBACK_RATES_PER_MILLION = {
    # https://ai.google.dev/gemini-api/docs/pricing
    "gemini-3.6-flash": (1.50, 7.50),
}


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    pricing_model = _LATEST_PRICING_TARGETS.get(model, model)
    try:
        priced = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            model_ref=pricing_model,
        )
    except LookupError:
        rates = _FALLBACK_RATES_PER_MILLION.get(pricing_model)
        if rates is None:
            return None
        input_rate, output_rate = rates
        return (prompt_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return float(priced.total_price)
