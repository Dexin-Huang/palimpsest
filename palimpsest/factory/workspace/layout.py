"""The one path contract for ``library/<doc_id>/`` workspaces.

Every station resolves artifact locations through these helpers rather than
constructing paths independently.
"""

from __future__ import annotations

from pathlib import Path

from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.core.contracts import CONTRACTS, FORMAT_SUFFIX

METADATA_FILENAME = CONTRACTS["metadata"].store
PAGE_LIST_FILENAME = CONTRACTS["page_list"].store

# Storage layout derives from the contract registry (core/contracts.py):
# page-grain kinds live one-file-per-page under library/<doc_id>/<kind>/;
# manuscript-grain kinds are single files at their `store` template.
PAGE_KIND_SUFFIX: dict[str, str] = {
    c.kind: FORMAT_SUFFIX[c.format] for c in CONTRACTS.values() if c.grain == "page"
}
DOC_KIND_FILENAME: dict[str, str] = {
    c.kind: c.store for c in CONTRACTS.values() if c.grain == "manuscript"
}


def doc_dir(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return library_root / doc_id


def metadata_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / METADATA_FILENAME


def page_list_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / PAGE_LIST_FILENAME


def artifact_path(
    doc_id: str, kind: str, page_id: str | None, library_root: Path = LIBRARY_ROOT
) -> Path:
    """Resolve one artifact kind and optional page to its workspace path."""
    root = doc_dir(doc_id, library_root)
    if kind in PAGE_KIND_SUFFIX:
        if page_id is None:
            raise ValueError(f"Page-grain kind {kind} requires a page_id")
        return root / kind / f"{page_id}{PAGE_KIND_SUFFIX[kind]}"
    return root / DOC_KIND_FILENAME[kind].format(doc_id=doc_id)
