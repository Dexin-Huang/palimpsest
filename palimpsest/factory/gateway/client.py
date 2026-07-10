"""Provider-agnostic model calls.

Stations call ``generate(ModelRequest)`` and get a ``ModelResponse`` with
text, token usage, and cost. Provider selection is by model id; retry with
exponential backoff on transient failures happens here, once, for every
provider. No station ever instantiates a provider client.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ImageContent:
    """An in-memory image (e.g. a lifted region tile that never touches disk)."""

    data: bytes
    mime: str = "image/png"


@dataclass(frozen=True)
class ModelRequest:
    model: str
    prompt: str
    system: str | None = None
    images: tuple[Path | ImageContent, ...] = ()
    temperature: float = 0.1
    max_output_tokens: int = 32768
    media_resolution: str | None = None  # "low" | "medium" | "high"
    json_output: bool = False            # constrain the response to JSON
    json_schema: Mapping[str, Any] | None = None  # constrained decoding: the
                                         # provider enforces this JSON Schema
    allow_empty: bool = False            # empty text is a valid answer, not an error


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    finish_reason: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None


class GatewayError(RuntimeError):
    """A model call failed after retries, or the request is unservable."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


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


def strip_json_fences(text: str) -> str:
    """Remove a Markdown ```json fence wrapper if the model added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].lstrip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_json_response(text: str):
    """Parse a model's JSON reply robustly: strip fences, then take the
    FIRST complete JSON value and ignore trailing data — some models append
    commentary or a second object even in JSON mode."""
    cleaned = strip_json_fences(text).lstrip()
    value, _ = json.JSONDecoder().raw_decode(cleaned)
    return value


def generate_json(request: ModelRequest, *, attempts: int = 3):
    """Call the model until it produces parseable JSON (some models emit
    intermittently malformed JSON even in JSON mode). Returns
    (parsed_value, response) with usage summed across all attempts."""
    tokens_in = tokens_out = total = 0
    cost = 0.0
    last_error: json.JSONDecodeError | None = None
    for _ in range(attempts):
        response = generate(request)
        tokens_in += response.prompt_tokens
        tokens_out += response.output_tokens
        total += response.total_tokens
        cost += response.cost_usd or 0.0
        try:
            value = parse_json_response(response.text)
        except json.JSONDecodeError as error:
            last_error = error
            continue
        summed = ModelResponse(
            text=response.text, model=response.model,
            finish_reason=response.finish_reason,
            prompt_tokens=tokens_in, output_tokens=tokens_out,
            total_tokens=total, cost_usd=cost or None,
        )
        return value, summed
    raise GatewayError(
        f"Model returned unparseable JSON after {attempts} attempts: {last_error}"
    )


def _resolve_provider(model: str):
    if model.startswith("gemini"):
        from palimpsest.factory.gateway import gemini

        return gemini.generate
    raise GatewayError(f"No provider registered for model: {model}")
