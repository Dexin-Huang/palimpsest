"""Provider-agnostic model calls.

Stations call ``generate(ModelRequest)`` and get a ``ModelResponse`` with
text, token usage, and cost. Provider selection is by model id; retry with
exponential backoff on transient failures happens here, once, for every
provider. No station ever instantiates a provider client.

Concurrency: the factory caps in-flight calls per provider prefix (the part
before ``/``) at ``MODEL_PROVIDER_WORKERS``. The cap is enforced by
:func:`provider_lease`, a cross-process file lease held for the duration of
every provider call. Each provider owns a row of lock files under the library
root (``<library>/.gateway-locks/<provider>.<i>.lock``), so every factory
process that issues provider calls contends for the same permits: inline
executor threads, subprocess executor cells, and canary lanes. Contention for
subprocess cells and canary lanes is transitive — their station code calls
``generate()``, which acquires the lease inside this module. Agent-cell
sessions are outside the gateway cap: they spawn external codex/omp CLIs and
never call the gateway. A per-process permit semaphore additionally guards
threads inside this process, where the OS byte-range lock semantics may not
distinguish two handles to the same file. Callers that invoke a provider
client directly from outside this module must wrap their calls in
:func:`provider_lease` so the documented "no more than N calls per provider
at once" guarantee holds factory-wide, not just within one process.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError

from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.config import MODEL_PROVIDER_WORKERS
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ModelRequest,
    ModelResponse,
)
from palimpsest.factory.usage import combine_cost


MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0

# A caller that cannot obtain a provider permit within this window fails
# transiently, so generate()'s backoff can absorb the busy period. The window
# is generous because model calls themselves run for minutes.
LEASE_WAIT_SECONDS = 120.0
LEASE_RETRY_SECONDS = 0.05

_lease_semaphores: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_lease_semaphores_lock = threading.Lock()


@contextmanager
def provider_lease(provider: str) -> Iterator[None]:
    """Hold one of ``MODEL_PROVIDER_WORKERS`` in-flight permits for ``provider``.

    The lease spans processes: the permit set is a per-provider row of lock
    files under the library root, so every factory process that issues
    provider calls (inline executor threads, subprocess executor cells,
    canary lanes) contends for the same cap. Contention for subprocess cells
    and canary lanes is transitive: their station code calls ``generate()``,
    which acquires this lease here. Agent-cell sessions spawn external
    codex/omp CLIs and never call the gateway, so they are outside this cap.
    A per-process semaphore guards threads in this process because OS
    byte-range lock semantics are not guaranteed to distinguish two handles
    from the same process.

    Raises :class:`GatewayError` (transient) when no permit frees up within
    :data:`LEASE_WAIT_SECONDS`. Callers that invoke a provider client
    directly from outside this module must acquire this lease around their
    calls so the documented worker bound holds factory-wide.
    """
    key = _lease_key(provider)
    semaphore = _lease_semaphore(key)
    deadline = time.monotonic() + LEASE_WAIT_SECONDS
    if not semaphore.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise GatewayError(
            f"No {MODEL_PROVIDER_WORKERS}-worker permit free for provider "
            f"{provider!r} within the lease window",
            transient=True,
        )
    try:
        fd = _acquire_file_permit(key, deadline)
    except BaseException:
        semaphore.release()
        raise
    try:
        yield
    finally:
        try:
            os.close(fd)
        finally:
            semaphore.release()


def _lease_key(provider: str) -> str:
    """Normalize a provider name to a stable, filesystem-safe lease key."""
    name = provider.strip().partition("/")[0]
    if not name:
        name = "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _lease_semaphore(key: str) -> threading.BoundedSemaphore:
    cache_key = (key, MODEL_PROVIDER_WORKERS)
    with _lease_semaphores_lock:
        semaphore = _lease_semaphores.get(cache_key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(MODEL_PROVIDER_WORKERS)
            _lease_semaphores[cache_key] = semaphore
        return semaphore


def _lease_directory() -> Path:
    directory = LIBRARY_ROOT / ".gateway-locks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _permit_paths(key: str) -> list[Path]:
    directory = _lease_directory()
    return [
        directory / f"{key}.{index}.lock" for index in range(MODEL_PROVIDER_WORKERS)
    ]


def _acquire_file_permit(key: str, deadline: float) -> int:
    paths = _permit_paths(key)
    while True:
        for path in paths:
            fd = _try_lock(path)
            if fd is not None:
                return fd
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayError(
                f"No {MODEL_PROVIDER_WORKERS}-worker permit free for provider "
                f"{key!r} within the lease window",
                transient=True,
            )
        time.sleep(min(LEASE_RETRY_SECONDS, remaining))


def _try_lock(path: Path) -> int | None:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock_file_region(fd)
        return fd
    except OSError:
        os.close(fd)
        return None


def _lock_file_region(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def generate(request: ModelRequest) -> ModelResponse:
    provider = _resolve_provider(request.model)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with provider_lease(request.model.partition("/")[0]):
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
