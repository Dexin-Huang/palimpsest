"""The one path contract for ``library/<doc_id>/`` workspaces.

All stations resolve artifact locations through these helpers — never
through string literals (the legacy pipeline's `download.py` bypassing its
own contracts module is exactly the defect this exists to prevent).
"""

from __future__ import annotations

from pathlib import Path

from palimpsest.factory.config import LIBRARY_ROOT

METADATA_FILENAME = "metadata.json"
PAGE_LIST_FILENAME = "page_list.json"

# Page-grain kinds live one-file-per-page under library/<doc_id>/<kind>/;
# manuscript-grain kinds are single files. This mapping IS the kind registry —
# a station producing an unlisted kind is a validation error.
PAGE_KIND_SUFFIX: dict[str, str] = {
    "page_image": ".jpg",
    "page_image_framed": ".jpg",
    "page_image_unmarked": ".jpg",
    "page_image_clean": ".jpg",
    "page_regions": ".json",
    "page_transcription": ".json",
    "page_translation": ".json",
    "page_assembled": ".json",
}
DOC_KIND_FILENAME: dict[str, str] = {
    "translation_brief": "translation_brief.json",
    "manuscript": "manuscript.json",
    "book": "book/book.json",
    "book_epub": "book/{doc_id}.epub",
}


def doc_dir(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return library_root / doc_id


def metadata_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / METADATA_FILENAME


def page_list_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / PAGE_LIST_FILENAME


def page_artifact(
    doc_id: str, kind: str, page_id: str, library_root: Path = LIBRARY_ROOT
) -> Path:
    return doc_dir(doc_id, library_root) / kind / f"{page_id}{PAGE_KIND_SUFFIX[kind]}"


def doc_artifact(doc_id: str, kind: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / DOC_KIND_FILENAME[kind].format(doc_id=doc_id)


def artifact_path(
    doc_id: str, kind: str, page_id: str | None, library_root: Path = LIBRARY_ROOT
) -> Path:
    """The one resolver stations and the conductor share: kind + page → file."""
    if kind in PAGE_KIND_SUFFIX:
        if page_id is None:
            raise ValueError(f"Page-grain kind {kind} requires a page_id")
        return page_artifact(doc_id, kind, page_id, library_root)
    return doc_artifact(doc_id, kind, library_root)
