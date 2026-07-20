"""Data contract shared by stations, gateway routing, and providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


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
    thinking_budget: int | None = None
    allow_empty: bool = False


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
