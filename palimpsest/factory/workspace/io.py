"""Atomic file I/O for workspace artifacts.

Every artifact write in the factory goes through this module (design rule
FACTORY.md §6.4). Atomic means temp file + fsync + ``os.replace`` — a crash
never leaves a half-written artifact.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_write(path: Path, write_body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        write_body(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, text: str) -> None:
    _atomic_write(path, lambda handle: handle.write(text))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


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
