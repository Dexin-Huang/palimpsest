from __future__ import annotations

from pathlib import Path
from typing import Any

from palimpsest.contracts import library_registry_path, metadata_path, page_list_path

from .io import atomic_write_json, read_json
from .registry import build_registry_entry, now_iso, update_registry


def update_metadata(doc_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    metadata = read_json(metadata_path(doc_dir))
    page_list = read_json(page_list_path(doc_dir))

    metadata.update(updates)
    metadata["updated_at"] = now_iso()
    atomic_write_json(metadata_path(doc_dir), metadata)

    registry_path = library_registry_path(doc_dir.parent)
    update_registry(registry_path, build_registry_entry(metadata, page_list))
    return metadata
