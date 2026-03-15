from __future__ import annotations

from datetime import datetime
import re


DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS = 32768


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _coerce_json_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


__all__ = [
    "DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS",
    "_coerce_json_text",
    "_utc_now",
]
