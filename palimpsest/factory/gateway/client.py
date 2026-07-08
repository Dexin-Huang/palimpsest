"""Provider-agnostic model calls.

Stations call ``generate(ModelRequest)`` and get a ``ModelResponse`` with
text, token usage, and cost. Provider selection is by model id; retry with
exponential backoff on transient failures happens here, once, for every
provider. No station ever instantiates a provider client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelRequest:
    model: str
    prompt: str
    system: str | None = None
    images: tuple[Path, ...] = ()
    temperature: float = 0.1
    max_output_tokens: int = 32768
    media_resolution: str | None = None  # "low" | "medium" | "high"
    json_output: bool = False            # constrain the response to JSON


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


def _resolve_provider(model: str):
    if model.startswith("gemini"):
        from palimpsest.factory.gateway import gemini

        return gemini.generate
    raise GatewayError(f"No provider registered for model: {model}")
