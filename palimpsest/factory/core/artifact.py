"""Artifact content identity and provenance verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping


def payload_fingerprint(payload: Mapping[str, object]) -> str:
    content = {key: value for key, value in payload.items() if key != "provenance"}
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def content_fingerprint(path: Path) -> str:
    """Hash artifact content while excluding a JSON provenance stamp."""
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        else:
            if isinstance(payload, dict):
                return payload_fingerprint(payload)
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def provenance_path(path: Path) -> Path:
    if path.suffix == ".json":
        return path
    return path.with_suffix(path.suffix + ".provenance.json")


def read_provenance(path: Path) -> dict | None:
    try:
        payload = json.loads(provenance_path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    stamp = payload.get("provenance") if path.suffix == ".json" else payload
    return stamp if isinstance(stamp, dict) else None


def provenance_fingerprint(
    path: Path, fields: tuple[str, ...] | None = None
) -> str | None:
    stamp = read_provenance(path)
    if stamp is None:
        return None
    if fields is not None:
        stamp = {field: stamp.get(field) for field in fields}
    canonical = json.dumps(stamp, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def provenance_matches(path: Path, expected: Mapping[str, object]) -> bool:
    stamp = read_provenance(path)
    return stamp is not None and all(
        stamp.get(key) == value for key, value in expected.items()
    )
