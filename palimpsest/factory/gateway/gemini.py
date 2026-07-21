"""Gemini Interactions API provider for the model gateway.

Owns Gemini client lifecycle, stateless multimodal request assembly, response
normalization, usage extraction, and transient-error classification.
"""

from __future__ import annotations

import base64
import contextlib
import math
import threading
import warnings
from collections.abc import Mapping
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from google.genai._gaos.lib import compat_errors as interaction_errors

from palimpsest.factory.gateway.pricing import estimate_cost
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ImageContent,
    ModelRequest,
    ModelResponse,
)

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_SUPPORTED_IMAGE_MIMES = frozenset(_MIME_TYPES.values())
_MEDIA_RESOLUTIONS = frozenset({"low", "medium", "high"})
_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


# One client, created under an explicit lock: concurrent first calls through
# functools.lru_cache would construct duplicate clients, and the discarded
# duplicate's cleanup closes transport state shared with the survivor.
_client_lock = threading.Lock()
_client_instance: genai.Client | None = None


def _client() -> genai.Client:
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            # The gateway owns retries. Disabling SDK retries keeps one attempt
            # count and one backoff policy across every model provider.
            _client_instance = genai.Client(
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=0)
                )
            )
        return _client_instance


def _interactions_client():
    # Interactions is GA and Google's recommended API, but google-genai 2.12.1
    # still emits an outdated experimental warning when this property is read.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"Interactions usage is experimental and may change in future "
                r"versions\."
            ),
            category=UserWarning,
        )
        return _client().interactions


def _reset_client() -> None:
    global _client_instance
    with _client_lock:
        stale_client = _client_instance
        _client_instance = None
        if stale_client is not None:
            with contextlib.suppress(Exception):
                stale_client.close()


def _mime_type(path: Path) -> str:
    mime = _MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise GatewayError(f"Unsupported image type: {path}")
    return mime


def _image_block(image: Path | ImageContent, resolution: str | None) -> dict:
    if isinstance(image, ImageContent):
        data = image.data
        mime = image.mime
        label = mime
    elif isinstance(image, Path):
        mime = _mime_type(image)
        label = str(image)
        try:
            data = image.read_bytes()
        except OSError as error:
            raise GatewayError(f"Could not read image: {image}") from error
    else:
        raise GatewayError(f"Unsupported image value: {type(image).__name__}")
    if mime not in _SUPPORTED_IMAGE_MIMES:
        raise GatewayError(f"Unsupported image type: {label}")
    if not isinstance(data, bytes) or not data:
        raise GatewayError(f"Image content is empty or invalid: {label}")
    block = {
        "type": "image",
        "mime_type": mime,
        "data": base64.b64encode(data).decode("ascii"),
    }
    if resolution is not None:
        block["resolution"] = resolution
    return block


def generate(request: ModelRequest) -> ModelResponse:
    kwargs = _request_kwargs(request)
    try:
        response = _interactions_client().create(**kwargs)
    except (
        interaction_errors.APIError,
        errors.APIError,
        httpx.TransportError,
        RuntimeError,
    ) as error:
        closed_client = isinstance(error, RuntimeError) and (
            "client has been closed" in str(error)
        )
        if isinstance(error, RuntimeError) and not closed_client:
            raise
        connection_failure = (
            isinstance(
                error,
                (interaction_errors.APIConnectionError, httpx.TransportError),
            )
            or closed_client
        )
        if connection_failure:
            _reset_client()
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(error, "code", None)
        raise GatewayError(
            f"Gemini call failed: {error}",
            transient=connection_failure or status in _TRANSIENT_STATUS_CODES,
            tokens_in=None,
            tokens_out=None,
            cost_usd=None,
        ) from error

    try:
        text, finish_reason = _response_text(response, allow_empty=request.allow_empty)
    except GatewayError as error:
        prompt_tokens, output_tokens, thought_tokens, _ = _usage(response)
        raise GatewayError(
            str(error),
            tokens_in=prompt_tokens,
            tokens_out=output_tokens + thought_tokens,
            cost_usd=estimate_cost(
                request.model, prompt_tokens, output_tokens + thought_tokens
            ),
        ) from error
    prompt_tokens, output_tokens, thought_tokens, total_tokens = _usage(response)
    return ModelResponse(
        text=text,
        model=request.model,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        thought_tokens=thought_tokens,
        total_tokens=total_tokens,
        cost_usd=estimate_cost(
            request.model, prompt_tokens, output_tokens + thought_tokens
        ),
    )


def _request_kwargs(request: ModelRequest) -> dict:
    if (
        request.media_resolution is not None
        and request.media_resolution not in _MEDIA_RESOLUTIONS
    ):
        raise GatewayError(f"Unknown media resolution: {request.media_resolution}")
    if (
        request.thinking_level is not None
        and request.thinking_level not in _THINKING_LEVELS
    ):
        raise GatewayError(f"Unknown thinking level: {request.thinking_level}")
    if (
        isinstance(request.temperature, bool)
        or not isinstance(request.temperature, (int, float))
        or not math.isfinite(request.temperature)
        or not 0 <= request.temperature <= 2
    ):
        raise GatewayError(f"Invalid temperature: {request.temperature}")
    if (
        isinstance(request.max_output_tokens, bool)
        or not isinstance(request.max_output_tokens, int)
        or request.max_output_tokens < 1
    ):
        raise GatewayError(f"Invalid max output tokens: {request.max_output_tokens}")
    if request.json_schema is not None and not isinstance(request.json_schema, Mapping):
        raise GatewayError("JSON schema must be a mapping")

    inputs = [{"type": "text", "text": request.prompt}]
    inputs.extend(
        _image_block(image, request.media_resolution) for image in request.images
    )
    generation_config = {
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
    }
    if request.thinking_level is not None:
        generation_config["thinking_level"] = request.thinking_level

    kwargs = {
        "model": request.model,
        "input": inputs,
        "store": False,
        "generation_config": generation_config,
    }
    if request.system is not None:
        kwargs["system_instruction"] = request.system
    if request.json_output or request.json_schema is not None:
        response_format = {"type": "text", "mime_type": "application/json"}
        if request.json_schema is not None:
            response_format["schema"] = dict(request.json_schema)
        kwargs["response_format"] = [response_format]
    return kwargs


def _response_text(
    response: object, *, allow_empty: bool = False
) -> tuple[str, str | None]:
    status = str(getattr(response, "status", "") or "").lower()
    if status not in {"completed", "incomplete"}:
        raise GatewayError(f"Model interaction ended with status={status or 'unknown'}")
    finish_reason = "INCOMPLETE" if status == "incomplete" else None
    text = (getattr(response, "output_text", None) or "").strip()
    if not text and not allow_empty:
        raise GatewayError(f"Model returned no text (finish_reason={finish_reason})")
    return text, finish_reason


def _usage(response: object) -> tuple[int, int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0, 0
    prompt_tokens = getattr(usage, "total_input_tokens", 0) or 0
    output_tokens = getattr(usage, "total_output_tokens", 0) or 0
    thought_tokens = getattr(usage, "total_thought_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or (
        prompt_tokens + output_tokens + thought_tokens
    )
    return prompt_tokens, output_tokens, thought_tokens, total_tokens
