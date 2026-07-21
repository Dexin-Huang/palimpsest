"""Data contract shared by stations, gateway routing, and providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from palimpsest.factory.usage import combine_cost, combine_count


@dataclass(frozen=True)
class ImageContent:
    """An in-memory image, such as a region tile that never touches disk."""

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
    media_resolution: str | None = None
    json_output: bool = False
    json_schema: Mapping[str, Any] | None = None
    thinking_level: str | None = None
    allow_empty: bool = False


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    finish_reason: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    thought_tokens: int = 0
    cost_usd: float | None = None

    @property
    def billable_output_tokens(self) -> int:
        return self.output_tokens + self.thought_tokens


class GatewayError(RuntimeError):
    """A model call failed after retries, or the request is unservable."""

    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        tokens_in: int | None = 0,
        tokens_out: int | None = 0,
        cost_usd: float | None = 0.0,
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost_usd = cost_usd

    def with_prior_usage(
        self,
        *,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: float | None,
    ) -> "GatewayError":
        return GatewayError(
            str(self),
            transient=self.transient,
            tokens_in=combine_count(tokens_in, self.tokens_in),
            tokens_out=combine_count(tokens_out, self.tokens_out),
            cost_usd=combine_cost(cost_usd, self.cost_usd),
        )
