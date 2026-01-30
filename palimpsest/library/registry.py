from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def write_registry(path: Path, entries: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=True))
            handle.write("\n")


def update_registry(path: Path, entry: dict) -> None:
    entries = load_registry(path)
    doc_id = entry.get("doc_id")
    if not doc_id:
        raise ValueError("registry entry missing doc_id")
    updated = False
    for i, existing in enumerate(entries):
        if existing.get("doc_id") == doc_id:
            entries[i] = entry
            updated = True
            break
    if not updated:
        entries.append(entry)
    write_registry(path, entries)


def build_registry_entry(metadata: dict, page_list: dict) -> dict:
    return {
        "doc_id": metadata.get("doc_id", ""),
        "source_url": metadata.get("source_url", ""),
        "title": metadata.get("title", ""),
        "date": metadata.get("date", ""),
        "language": metadata.get("language", ""),
        "collection": metadata.get("collection", ""),
        "triage_score": metadata.get("triage_score"),
        "triage_reason": metadata.get("triage_reason", ""),
        "newly_digitized": metadata.get("newly_digitized"),
        "status": metadata.get("status", ""),
        "manifest_url": page_list.get("manifest_url"),
        "page_count": len(page_list.get("pages", [])),
        "created_at": metadata.get("created_at", ""),
        "updated_at": metadata.get("updated_at", ""),
    }
