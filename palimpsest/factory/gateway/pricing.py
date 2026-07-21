"""Model pricing resolved through ``genai-prices``.

New stable models may briefly precede the package catalog. The small fallback
table uses Google's published standard API rates until the catalog catches up.
Unknown models still return ``None``, never a misleading zero.
"""

from __future__ import annotations

from genai_prices import Usage, calc_price

_FALLBACK_RATES_PER_MILLION = {
    # https://ai.google.dev/gemini-api/docs/pricing
    "gemini-3.6-flash": (1.50, 7.50),
}


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    try:
        priced = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            model_ref=model,
        )
    except LookupError:
        rates = _FALLBACK_RATES_PER_MILLION.get(model)
        if rates is None:
            return None
        input_rate, output_rate = rates
        return (prompt_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return float(priced.total_price)
