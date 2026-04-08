from __future__ import annotations

import re


def page_sort_key(page_id: str) -> tuple[int, int, int, str]:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        side = 0 if match.group(2).lower() == "r" else 1
        return (0, int(match.group(1)), side, page_id)
    match = re.match(r"^page_(\d+)$", page_id, re.IGNORECASE)
    if match:
        return (1, int(match.group(1)), 0, page_id)
    return (2, 0, 0, page_id)


def display_page_id(page_id: str) -> str:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        return f"Folio {int(match.group(1))}{match.group(2).lower()}"
    return page_id.replace("_", " ")
