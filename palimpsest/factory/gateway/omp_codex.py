"""OMP-backed Codex provider for subscription-authenticated model calls.

The provider runs one ephemeral, tool-free OMP print session per request. That
keeps factory cells stateless while letting OMP own OpenAI Codex OAuth refresh
and subscription routing. OMP's CLI does not expose sampling temperature or a
hard per-turn output-token limit, so those request fields are advisory here;
structured-output and length requirements are added to the system instruction.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
_TRANSIENT_MARKERS = (
    "429",
    "connection",
    "network",
    "rate limit",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "too many requests",
)
_TIMEOUT_SECONDS = 900


def generate(request: ModelRequest) -> ModelResponse:
    """Execute a stateless request through OMP's OpenAI Codex provider."""
    _validate_request(request)
    executable = _omp_executable()

    with tempfile.TemporaryDirectory(prefix="palimpsest-omp-") as directory:
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
                    timeout=_TIMEOUT_SECONDS,
                    check=False,
                )
        except subprocess.TimeoutExpired as error:
            raise GatewayError(
                f"OMP Codex call timed out after {_TIMEOUT_SECONDS} seconds",
                transient=True,
                tokens_in=None,
                tokens_out=None,
                cost_usd=0.0,
            ) from error
        except OSError as error:
            raise GatewayError(
                f"Could not start OMP Codex: {error}",
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
            ) from error

        if completed.returncode != 0:
            detail = _failure_detail(events_path, stderr_path)
            raise GatewayError(
                f"OMP Codex call failed: {detail}",
                transient=_is_transient(detail),
                tokens_in=None,
                tokens_out=None,
                cost_usd=0.0,
            )

        message = _assistant_message(events_path)
    stop_reason = str(message.get("stopReason") or "").lower()
    if stop_reason not in {"", "stop"}:
        detail = str(message.get("errorMessage") or stop_reason)
        raise GatewayError(
            f"OMP Codex interaction ended with stop_reason={detail}",
            transient=_is_transient(detail),
            tokens_in=_prompt_tokens(message),
            tokens_out=_billable_output_tokens(message),
            cost_usd=0.0,
        )

    text = "".join(
        str(part.get("text") or "")
        for part in message.get("content", ())
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
    if not text and not request.allow_empty:
        raise GatewayError(
            "OMP Codex returned no text",
            tokens_in=_prompt_tokens(message),
            tokens_out=_billable_output_tokens(message),
            cost_usd=0.0,
        )

    usage = message.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    reasoning_tokens = _nonnegative_int(usage.get("reasoningTokens"))
    billable_output = _nonnegative_int(usage.get("output"))
    return ModelResponse(
        text=text,
        model=f"{message.get('provider', 'openai-codex')}/{message.get('model', request.model.split('/', 1)[-1])}",
        finish_reason=None,
        prompt_tokens=_prompt_tokens(message),
        output_tokens=max(0, billable_output - reasoning_tokens),
        thought_tokens=reasoning_tokens,
        total_tokens=_nonnegative_int(usage.get("totalTokens")),
        # OMP reports API-equivalent pricing metadata, but OAuth-backed Codex
        # subscription calls have no marginal API charge to the factory.
        cost_usd=0.0,
    )


def _validate_request(request: ModelRequest) -> None:
    if not request.model.startswith("openai-codex/"):
        raise GatewayError(f"OMP Codex requires an openai-codex model: {request.model}")
    if not request.prompt:
        raise GatewayError("OMP Codex prompt must not be empty")
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
            "the openai-codex provider first."
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
        "--thinking",
        request.thinking_level or "low",
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
    ]
    command.extend(f"@{path}" for path in image_paths)
    return command


def _assistant_message(events_path: Path) -> Mapping[str, Any]:
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
            f"OMP Codex returned no complete assistant message{suffix}",
            tokens_in=None,
            tokens_out=None,
            cost_usd=0.0,
        )
    return last_message


def _prompt_tokens(message: Mapping[str, Any]) -> int:
    usage = message.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    return sum(
        _nonnegative_int(usage.get(field))
        for field in ("input", "cacheRead", "cacheWrite")
    )


def _billable_output_tokens(message: Mapping[str, Any]) -> int:
    usage = message.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    return _nonnegative_int(usage.get("output"))


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


def _is_transient(detail: str) -> bool:
    lowered = detail.casefold()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)
