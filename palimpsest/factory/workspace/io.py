"""Atomic file I/O for workspace artifacts.

Every artifact write in the factory goes through this module (design rule
FACTORY.md §6.4). Atomic means temp file + fsync + ``os.replace`` — a crash
never leaves a half-written artifact.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def atomic_write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    def body(handle) -> None:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    _atomic_write(path, body)
