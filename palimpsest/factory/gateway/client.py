"""Provider-agnostic model calls.

Stations call ``generate(ModelRequest)`` and get a ``ModelResponse`` with
text, token usage, and cost. Provider selection is by model id; retry with
exponential backoff on transient failures happens here, once, for every
provider. No station ever instantiates a provider client.
"""

from __future__ import annotations

import json
import time
import threading
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError

from palimpsest.factory.config import MODEL_PROVIDER_WORKERS
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ModelRequest,
    ModelResponse,
)
from palimpsest.factory.usage import combine_cost


MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
_provider_slots: dict[str, threading.BoundedSemaphore] = {}
_provider_slots_lock = threading.Lock()


def _provider_slot(model: str) -> threading.BoundedSemaphore:
    provider = model.partition("/")[0]
    with _provider_slots_lock:
        slot = _provider_slots.get(provider)
        if slot is None:
            slot = threading.BoundedSemaphore(MODEL_PROVIDER_WORKERS)
            _provider_slots[provider] = slot
        return slot


def generate(request: ModelRequest) -> ModelResponse:
    provider = _resolve_provider(request.model)
    slot = _provider_slot(request.model)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with slot:
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
    """Return schema-valid JSON and usage from every attempted model call."""
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise GatewayError("JSON generation attempts must be a positive integer")

    validator = _schema_validator(request.json_schema)
    prompt_tokens = output_tokens = thought_tokens = total_tokens = 0
    cost_usd: float | None = 0.0
    last_error: json.JSONDecodeError | ValidationError | None = None
    last_failure = "unparseable JSON"
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
            if validator is not None:
                validator.validate(value)
        except (json.JSONDecodeError, ValidationError) as error:
            last_error = error
            last_failure = (
                "unparseable JSON"
                if isinstance(error, json.JSONDecodeError)
                else "JSON that violates the requested schema"
            )
            if is_truncated(response):
                raise GatewayError(
                    f"Model returned truncated {last_failure}: {error}",
                    tokens_in=prompt_tokens,
                    tokens_out=output_tokens + thought_tokens,
                    cost_usd=cost_usd,
                    finish_reason=response.finish_reason,
                ) from error
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
        f"Model returned {last_failure} after {attempts} attempts: {last_error}",
        tokens_in=prompt_tokens,
        tokens_out=output_tokens + thought_tokens,
        cost_usd=cost_usd,
    )


def _schema_validator(schema: Any):
    if schema is None:
        return None
    if not isinstance(schema, Mapping):
        raise GatewayError("JSON schema must be a mapping")
    try:
        validator_class = validators.validator_for(schema)
        validator_class.check_schema(schema)
    except SchemaError as error:
        raise GatewayError(f"Invalid JSON schema: {error.message}") from error
    return validator_class(schema)


_TRUNCATION_REASONS = ("MAX_TOKENS", "LENGTH", "INCOMPLETE")


def is_truncated(response) -> bool:
    """True when a response's finish reason marks its output as truncated.

    Substring semantics: providers prefix or wrap the canonical reason (e.g.
    ``stop: MAX_TOKENS`` or ``LENGTH_LIMIT``), so a bare membership check
    would admit truncated output as complete.
    """
    return bool(
        response.finish_reason
        and any(
            reason in response.finish_reason.upper() for reason in _TRUNCATION_REASONS
        )
    )


def _resolve_provider(model: str):
    if model.startswith(("google/", "gemini")):
        raise RuntimeError(
            "Gemini was retired 2026-08; use token-plan/qwen3.8-max "
            "(see exodia tool registry rev 19)"
        )
    if "/" in model:
        from palimpsest.factory.gateway.omp import generate as omp_generate

        return omp_generate
    raise GatewayError(f"No provider registered for model: {model}")
