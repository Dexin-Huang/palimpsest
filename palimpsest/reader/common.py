from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from palimpsest.web import display_page_id as web_display_page_id
from palimpsest.web import page_sort_key as web_page_sort_key


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def relpath(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), start=from_dir.resolve())).as_posix()


def page_sort_key(page_id: str) -> tuple[int, int, int, str]:
    return web_page_sort_key(page_id)


def display_page_id(page_id: str) -> str:
    return web_display_page_id(page_id)


def read_text(path: str | Path | None) -> str:
    if not path:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8")
