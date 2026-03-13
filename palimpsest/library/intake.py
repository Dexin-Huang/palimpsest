from __future__ import annotations

import json
import re
from pathlib import Path

from palimpsest.contracts import (
    images_dir,
    library_registry_path,
    metadata_path,
    page_list_path,
    runs_dir,
)

from .config import LIBRARY_ROOT
from .io import atomic_write_json
from .registry import build_registry_entry, now_iso, update_registry

DOC_ID_RE = re.compile(r"^[a-z0-9_]+$")


def validate_doc_id(doc_id: str) -> None:
    if not DOC_ID_RE.match(doc_id):
        raise ValueError("doc_id must be lowercase ASCII with underscores only")


def _ensure_layout(doc_dir: Path) -> None:
    images_dir(doc_dir).mkdir(parents=True, exist_ok=True)
    runs_dir(doc_dir).mkdir(parents=True, exist_ok=True)


def ingest_document(
    *,
    doc_id: str,
    metadata: dict,
    page_list: dict,
    library_root: Path = LIBRARY_ROOT,
) -> Path:
    validate_doc_id(doc_id)
    doc_dir = library_root / doc_id
    _ensure_layout(doc_dir)

    created_at = metadata.get("created_at") or now_iso()
    updated_at = now_iso()
    metadata = {
        **metadata,
        "doc_id": doc_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    if "status" not in metadata:
        metadata["status"] = "ingested"

    page_list = {**page_list, "doc_id": doc_id}

    atomic_write_json(metadata_path(doc_dir), metadata)
    atomic_write_json(page_list_path(doc_dir), page_list)

    registry_path = library_registry_path(library_root)
    update_registry(registry_path, build_registry_entry(metadata, page_list))

    return doc_dir
