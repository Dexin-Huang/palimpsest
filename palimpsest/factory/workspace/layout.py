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


def doc_dir(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return library_root / doc_id


def metadata_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / METADATA_FILENAME


def page_list_path(doc_id: str, library_root: Path = LIBRARY_ROOT) -> Path:
    return doc_dir(doc_id, library_root) / PAGE_LIST_FILENAME


def artifact_dir(doc_id: str, kind: str, library_root: Path = LIBRARY_ROOT) -> Path:
    """Directory holding all artifacts of one kind for one document.

    ``kind`` is an artifact kind from the station contract, e.g.
    ``page_image``, ``page_image_clean``, ``page_transcription``. One kind,
    one directory — stations discover each other's outputs only this way.
    """
    return doc_dir(doc_id, library_root) / kind
