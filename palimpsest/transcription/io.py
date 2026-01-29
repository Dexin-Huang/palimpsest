import hashlib
import json
import os
from pathlib import Path
from typing import Optional


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def validate_json_text(text: str) -> Optional[str]:
    try:
        json.loads(text)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)


def validate_json_file(path: Path) -> Optional[str]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return None
    except Exception as exc:
        return str(exc)
