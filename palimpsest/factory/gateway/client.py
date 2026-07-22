"""Provider-agnostic model calls.

Stations call ``generate(ModelRequest)`` and get a ``ModelResponse`` with
text, token usage, and cost. Provider selection is by model id; retry with
exponential backoff on transient failures happens here, once, for every
provider. No station ever instantiates a provider client.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ModelRequest,
    ModelResponse,
)
from palimpsest.factory.usage import combine_cost


MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0


def generate(request: ModelRequest) -> ModelResponse:
    provider = _resolve_provider(request.model)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return provider(request)
        except GatewayError as error:
            if not error.transient or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))


def parse_json_response(text: str) -> Any:
    """Parse the first complete JSON value, tolerating fences and trailing text."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines.pop()
        stripped = "\n".join(lines).strip()
    value, _ = json.JSONDecoder().raw_decode(stripped)
    return value


def generate_json(
    request: ModelRequest, *, attempts: int = 3
) -> tuple[Any, ModelResponse]:
    """Return parsed JSON and a response with usage from every model call."""
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise GatewayError("JSON generation attempts must be a positive integer")

    prompt_tokens = output_tokens = thought_tokens = total_tokens = 0
    cost_usd: float | None = 0.0
    last_error: json.JSONDecodeError | None = None
    for _ in range(attempts):
        try:
            response = generate(request)
        except GatewayError as error:
            raise error.with_prior_usage(
                tokens_in=prompt_tokens,
                tokens_out=output_tokens + thought_tokens,
                cost_usd=cost_usd,
            ) from error
        prompt_tokens += response.prompt_tokens
        output_tokens += response.output_tokens
        thought_tokens += response.thought_tokens
        total_tokens += response.total_tokens
        cost_usd = combine_cost(cost_usd, response.cost_usd)
        try:
            value = parse_json_response(response.text)
        except json.JSONDecodeError as error:
            last_error = error
            continue
        return value, replace(
            response,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            thought_tokens=thought_tokens,
            cost_usd=cost_usd,
        )
    raise GatewayError(
        f"Model returned unparseable JSON after {attempts} attempts: {last_error}",
        tokens_in=prompt_tokens,
        tokens_out=output_tokens + thought_tokens,
        cost_usd=cost_usd,
    )


def _resolve_provider(model: str):
    if model.startswith("openai-codex/"):
        from palimpsest.factory.gateway.omp_codex import generate as omp_codex_generate

        return omp_codex_generate
    if model.startswith("gemini"):
        from palimpsest.factory.gateway.gemini import generate as gemini_generate

        return gemini_generate
    raise GatewayError(f"No provider registered for model: {model}")
