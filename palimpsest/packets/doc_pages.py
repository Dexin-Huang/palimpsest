from __future__ import annotations

import json
from pathlib import Path

from palimpsest.contracts import packet_workspace_dir, page_list_path


def load_doc_pages(doc_dir: Path) -> list[dict]:
    """Load and order the canonical page list for a library document."""
    resolved_page_list_path = page_list_path(doc_dir)
    if not resolved_page_list_path.exists():
        raise FileNotFoundError(f"missing page_list.json in {doc_dir}")
    payload = json.loads(resolved_page_list_path.read_text(encoding="utf-8"))
    pages = list(payload.get("pages", []))
    pages.sort(key=lambda item: int(item.get("order", 0)))
    return pages


def select_doc_pages(
    pages: list[dict],
    *,
    start_page: str | None = None,
    end_page: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Select a contiguous page tranche from the ordered document page list."""
    selected = pages
    if start_page:
        try:
            start_index = next(index for index, page in enumerate(selected) if page.get("page_id") == start_page)
        except StopIteration as exc:
            raise ValueError(f"start page not found: {start_page}") from exc
        selected = selected[start_index:]
    if end_page:
        try:
            end_index = next(index for index, page in enumerate(selected) if page.get("page_id") == end_page)
        except StopIteration as exc:
            raise ValueError(f"end page not found: {end_page}") from exc
        selected = selected[: end_index + 1]
    if limit is not None:
        selected = selected[:limit]
    return selected


def packet_dir_for_page(doc_dir: Path, page_id: str) -> Path:
    """Resolve the canonical packet workspace directory for one page."""
    return packet_workspace_dir(doc_dir, page_id)


__all__ = [
    "load_doc_pages",
    "packet_dir_for_page",
    "select_doc_pages",
]
