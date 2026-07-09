"""Gemini provider for the gateway.

Owns everything Gemini-specific: client lifecycle, content assembly,
response-part walking, usage extraction, and transient-error
classification. The legacy pipeline had five copies of this logic; this is
the only one.
"""

from __future__ import annotations

import threading
from pathlib import Path

from google import genai
from google.genai import errors, types

from palimpsest.factory.gateway.client import (
    GatewayError,
    ImageContent,
    ModelRequest,
    ModelResponse,
)
from palimpsest.factory.gateway.pricing import estimate_cost

_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_MEDIA_RESOLUTIONS = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}

_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


# One client, created under an explicit lock: concurrent first calls through
# functools.lru_cache would construct duplicate clients, and the discarded
# duplicate's cleanup closes transport state shared with the survivor —
# observed live as "Cannot send a request, as the client has been closed".
_client_lock = threading.Lock()
_client_instance: genai.Client | None = None


def _client() -> genai.Client:
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            _client_instance = genai.Client()
        return _client_instance


def _reset_client() -> None:
    global _client_instance
    with _client_lock:
        _client_instance = None


def _mime_type(path: Path) -> str:
    mime = _MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise GatewayError(f"Unsupported image type: {path}")
    return mime


def generate(request: ModelRequest) -> ModelResponse:
    contents: list = [request.prompt]
    for image in request.images:
        if isinstance(image, ImageContent):
            contents.append(types.Part.from_bytes(data=image.data, mime_type=image.mime))
        else:
            contents.append(
                types.Part.from_bytes(data=image.read_bytes(), mime_type=_mime_type(image))
            )

    config_kwargs: dict = {
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
    }
    if request.system is not None:
        config_kwargs["system_instruction"] = request.system
    if request.json_output:
        config_kwargs["response_mime_type"] = "application/json"
    if request.media_resolution is not None:
        try:
            config_kwargs["media_resolution"] = _MEDIA_RESOLUTIONS[request.media_resolution]
        except KeyError:
            raise GatewayError(f"Unknown media resolution: {request.media_resolution}")

    try:
        response = _client().models.generate_content(
            model=request.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except errors.APIError as error:
        transient = getattr(error, "code", None) in _TRANSIENT_STATUS_CODES
        raise GatewayError(f"Gemini call failed: {error}", transient=transient) from error
    except RuntimeError as error:
        if "client has been closed" in str(error):
            _reset_client()  # next attempt builds a fresh client
            raise GatewayError(f"Gemini client closed: {error}", transient=True) from error
        raise

    text, finish_reason = _response_text(response, allow_empty=request.allow_empty)
    prompt_tokens, output_tokens, total_tokens = _usage(response)
    return ModelResponse(
        text=text,
        model=request.model,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=estimate_cost(request.model, prompt_tokens, output_tokens),
    )


def _response_text(response: object, *, allow_empty: bool = False) -> tuple[str, str | None]:
    finish_reason = None
    text_parts: list[str] = []
    for index, candidate in enumerate(getattr(response, "candidates", None) or []):
        if index == 0:
            raw_reason = getattr(candidate, "finish_reason", None)
            finish_reason = str(raw_reason) if raw_reason is not None else None
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if isinstance(value, str) and value:
                text_parts.append(value)
    text = "\n".join(text_parts).strip()
    if not text and not allow_empty:
        raise GatewayError(f"Model returned no text (finish_reason={finish_reason})")
    return text, finish_reason


def _usage(response: object) -> tuple[int, int, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0
    return (
        getattr(usage, "prompt_token_count", 0) or 0,
        getattr(usage, "candidates_token_count", 0) or 0,
        getattr(usage, "total_token_count", 0) or 0,
    )
