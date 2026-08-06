"""Atomic file I/O for workspace artifacts.

Workspace artifact writes go through this module (FACTORY.md invariant 6,
"artifact commits are atomic"): temp file + fsync + ``os.replace`` so a
crash never leaves a half-written artifact. Evaluation-side record files
are exempt by design; they are written through their own runners.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now(*, timespec: str = "seconds") -> str:
    """Current UTC time as ISO-8601 with a Z suffix (record contract form)."""
    return (
        datetime.now(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@contextmanager
def _staged_path(path: Path) -> Iterator[Path]:
    """Yield a unique sibling file and replace ``path`` only after success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(path: Path, write_body) -> None:
    with _staged_path(path) as temporary:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            write_body(handle)
            handle.flush()
            os.fsync(handle.fileno())


def atomic_write_text(path: Path, text: str) -> None:
    _atomic_write(path, lambda handle: handle.write(text))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    with _staged_path(path) as temporary:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())


def atomic_write_json(path: Path, payload: Any, *, ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=ensure_ascii))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file strictly: every non-blank line is a JSON object."""
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    def body(handle) -> None:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    _atomic_write(path, body)
