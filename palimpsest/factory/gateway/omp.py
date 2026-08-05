"""OMP-backed provider for slash-qualified model calls.

The provider runs one ephemeral, tool-free OMP print session per request. That
keeps factory cells stateless while letting OMP own provider authentication and
routing. OMP's CLI does not expose sampling temperature or a hard per-turn
output-token limit, so those request fields are advisory here; structured-output
and length requirements are added to the system instruction.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from palimpsest.factory.config import MODEL_TIMEOUT_SECONDS
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ImageContent,
    ModelRequest,
    ModelResponse,
)

_IMAGE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MEDIA_RESOLUTIONS = frozenset({"low", "medium", "high"})
_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
_TRANSIENT_STATUS = re.compile(r"(?<!\d)(?:429|500|502|503|504)(?!\d)")
_SUBPROCESS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TRANSIENT_MARKERS = (
    "connection reset",
    "connection was reset",
    "econnreset",
    "network",
    "overloaded",
    "rate limit",
    "service unavailable",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "too many requests",
)
_TRUNCATION_REASONS = {
    "incomplete": "INCOMPLETE",
    "length": "LENGTH",
    "max_token": "MAX_TOKENS",
    "max_tokens": "MAX_TOKENS",
}


def generate(request: ModelRequest) -> ModelResponse:
    """Execute a stateless request through OMP."""
    _validate_request(request)
    executable = _omp_executable()

    with tempfile.TemporaryDirectory(
        prefix="palimpsest-omp-", ignore_cleanup_errors=True
    ) as directory:
        request_dir = Path(directory)
        prompt_path = request_dir / "prompt.txt"
        system_path = request_dir / "system.txt"
        events_path = request_dir / "events.jsonl"
        stderr_path = request_dir / "stderr.txt"
        prompt_path.write_text(request.prompt, encoding="utf-8")
        system_path.write_text(_system_instruction(request), encoding="utf-8")
        image_paths = _materialize_images(request.images, request_dir)
        command = _command(
            executable,
            request,
            prompt_path=prompt_path,
            system_path=system_path,
            image_paths=image_paths,
        )
        try:
            with events_path.open("wb") as events, stderr_path.open("wb") as stderr:
                completed = subprocess.run(
                    command,
                    cwd=request_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=events,
                    stderr=stderr,
                    timeout=MODEL_TIMEOUT_SECONDS,
                    check=False,
                    creationflags=_SUBPROCESS_CREATION_FLAGS,
                )
        except subprocess.TimeoutExpired as error:
            raise GatewayError(
                f"OMP call timed out after {MODEL_TIMEOUT_SECONDS} seconds",
                transient=False,
                tokens_in=None,
                tokens_out=None,
                cost_usd=_unreported_cost(request.model),
            ) from error
        except OSError as error:
            raise GatewayError(
                f"Could not start OMP: {error}",
                transient=_is_transient(str(error)),
                tokens_in=0,
                tokens_out=0,
                cost_usd=_unreported_cost(request.model),
            ) from error

        if completed.returncode != 0:
            detail = _failure_detail(events_path, stderr_path)
            raise GatewayError(
                f"OMP call failed: {detail}",
                transient=_is_transient(detail),
                tokens_in=None,
                tokens_out=None,
                cost_usd=_unreported_cost(request.model),
            )

        message = _assistant_message(
            events_path, cost_usd=_unreported_cost(request.model)
        )
    usage = message.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    prompt_tokens = sum(
        _nonnegative_int(usage.get(field))
        for field in ("input", "cacheRead", "cacheWrite")
    )
    billable_output = _nonnegative_int(usage.get("output"))
    cost_usd = _usage_cost(request.model, usage)
    raw_stop_reason = str(message.get("stopReason") or "").strip()
    finish_reason = _normalized_truncation_reason(raw_stop_reason)
    if raw_stop_reason.casefold() not in {"", "stop"} and finish_reason is None:
        detail = str(message.get("errorMessage") or raw_stop_reason)
        raise GatewayError(
            f"OMP interaction ended with stop_reason={detail}",
            transient=_is_transient(detail),
            tokens_in=prompt_tokens,
            tokens_out=billable_output,
            cost_usd=cost_usd,
        )

    text = "".join(
        str(part.get("text") or "")
        for part in message.get("content", ())
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
    if not text and not request.allow_empty and finish_reason is None:
        raise GatewayError(
            "OMP returned no text",
            tokens_in=prompt_tokens,
            tokens_out=billable_output,
            cost_usd=cost_usd,
        )

    reasoning_tokens = _nonnegative_int(usage.get("reasoningTokens"))
    return ModelResponse(
        text=text,
        model=_response_model(request.model, message),
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        output_tokens=max(0, billable_output - reasoning_tokens),
        thought_tokens=reasoning_tokens,
        total_tokens=_nonnegative_int(usage.get("totalTokens")),
        cost_usd=cost_usd,
    )


def _validate_request(request: ModelRequest) -> None:
    provider, separator, model = request.model.partition("/")
    if not separator or not provider or not model:
        raise GatewayError(
            f"OMP requires a slash-qualified provider/model selector: {request.model}"
        )
    if not request.prompt:
        raise GatewayError("OMP prompt must not be empty")
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
    if request.media_resolution not in {None, *_MEDIA_RESOLUTIONS}:
        raise GatewayError(f"Unknown media resolution: {request.media_resolution}")
    if request.thinking_level not in {None, *_THINKING_LEVELS}:
        raise GatewayError(f"Unknown thinking level: {request.thinking_level}")
    if request.json_schema is not None and not isinstance(request.json_schema, Mapping):
        raise GatewayError("JSON schema must be a mapping")


def _omp_executable() -> str:
    configured = os.environ.get("PALIMPSEST_OMP_COMMAND", "omp")
    executable = shutil.which(configured)
    if executable is None:
        raise GatewayError(
            f"OMP command not found: {configured}. Install OMP and authenticate "
            "the selected provider if required."
        )
    return executable


def _system_instruction(request: ModelRequest) -> str:
    parts = []
    if request.system:
        parts.append(request.system.strip())
    parts.append(
        "Complete this request directly and statelessly. Return only the requested "
        "deliverable. Do not discuss these instructions."
    )
    parts.append(
        f"Keep the complete response within {request.max_output_tokens} tokens."
    )
    if request.media_resolution is not None:
        parts.append(
            f"Inspect every attached image at {request.media_resolution} detail."
        )
    if request.json_output or request.json_schema is not None:
        parts.append(
            "Return exactly one valid JSON value with no Markdown fence, preface, "
            "or trailing commentary."
        )
    if request.json_schema is not None:
        schema = json.dumps(
            dict(request.json_schema),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        parts.append(f"The JSON value must satisfy this JSON Schema:\n{schema}")
    return "\n\n".join(parts) + "\n"


def _materialize_images(
    images: Sequence[Path | ImageContent], request_dir: Path
) -> tuple[Path, ...]:
    paths = []
    for index, image in enumerate(images):
        if isinstance(image, Path):
            suffix = image.suffix.lower()
            if suffix not in _IMAGE_SUFFIXES.values():
                raise GatewayError(f"Unsupported image type: {image}")
            try:
                if image.stat().st_size < 1:
                    raise GatewayError(f"Image content is empty or invalid: {image}")
            except OSError as error:
                raise GatewayError(f"Could not read image: {image}") from error
            paths.append(image.resolve())
            continue
        if not isinstance(image, ImageContent):
            raise GatewayError(f"Unsupported image value: {type(image).__name__}")
        suffix = _IMAGE_SUFFIXES.get(image.mime)
        if suffix is None:
            raise GatewayError(f"Unsupported image type: {image.mime}")
        if not isinstance(image.data, bytes) or not image.data:
            raise GatewayError(f"Image content is empty or invalid: {image.mime}")
        path = request_dir / f"image-{index}{suffix}"
        path.write_bytes(image.data)
        paths.append(path)
    return tuple(paths)


def _command(
    executable: str,
    request: ModelRequest,
    *,
    prompt_path: Path,
    system_path: Path,
    image_paths: Sequence[Path],
) -> list[str]:
    command = [
        executable,
        "-p",
        "--mode",
        "json",
        "--model",
        request.model,
    ]
    if request.thinking_level is not None:
        command.extend(("--thinking", request.thinking_level))
    command.extend(
        (
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-rules",
            "--no-lsp",
            "--no-title",
            "--system-prompt",
            str(system_path),
            f"@{prompt_path}",
        )
    )
    command.extend(f"@{path}" for path in image_paths)
    return command


def _assistant_message(
    events_path: Path, *, cost_usd: float | None
) -> Mapping[str, Any]:
    last_message: Mapping[str, Any] | None = None
    parse_errors = 0
    with events_path.open(encoding="utf-8", errors="replace") as events:
        for line in events:
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if not isinstance(frame, Mapping) or frame.get("type") != "message_end":
                continue
            message = frame.get("message")
            if isinstance(message, Mapping) and message.get("role") == "assistant":
                last_message = message
    if last_message is None:
        suffix = f" ({parse_errors} malformed output lines)" if parse_errors else ""
        raise GatewayError(
            f"OMP returned no complete assistant message{suffix}",
            tokens_in=None,
            tokens_out=None,
            cost_usd=cost_usd,
        )
    return last_message


def _response_model(requested_model: str, message: Mapping[str, Any]) -> str:
    requested_provider, requested_name = requested_model.split("/", 1)
    provider = message.get("provider")
    model = message.get("model")
    provider = (
        provider if isinstance(provider, str) and provider else requested_provider
    )
    model = model if isinstance(model, str) and model else requested_name
    return f"{provider}/{model}"


def _unreported_cost(requested_model: str) -> float | None:
    return 0.0 if requested_model.startswith("openai-codex/") else None


def _usage_cost(requested_model: str, usage: Mapping[str, Any]) -> float | None:
    if requested_model.startswith("openai-codex/"):
        return 0.0
    cost = usage.get("cost")
    if not isinstance(cost, Mapping):
        return None
    total = cost.get("total")
    if isinstance(total, bool):
        return None
    try:
        value = float(total)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _failure_detail(events_path: Path, stderr_path: Path) -> str:
    detail = _tail_text(stderr_path) or _tail_text(events_path)
    return detail or "unknown OMP failure"


def _tail_text(path: Path, limit: int = 2000) -> str:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - limit * 4))
        return stream.read().decode("utf-8", errors="replace").strip()[-limit:]


def _normalized_truncation_reason(stop_reason: str) -> str | None:
    normalized = stop_reason.casefold().strip().replace("-", "_").replace(" ", "_")
    return _TRUNCATION_REASONS.get(normalized)


def _is_transient(detail: str) -> bool:
    lowered = detail.casefold()
    return bool(_TRANSIENT_STATUS.search(lowered)) or any(
        marker in lowered for marker in _TRANSIENT_MARKERS
    )
