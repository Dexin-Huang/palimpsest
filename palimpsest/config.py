from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value if value is not None and value != "" else default


DEFAULT_MODEL_TRIAGE = _env("PALIMPSEST_MODEL_TRIAGE", _env("PALIMPSEST_MODEL", "gemini-3.1-flash-lite-preview"))
DEFAULT_MODEL_VISION = _env("PALIMPSEST_MODEL_VISION", "gemini-3.1-flash-lite-preview")
DEFAULT_MODEL_READING = _env("PALIMPSEST_MODEL_READING", DEFAULT_MODEL_VISION)
DEFAULT_MODEL_RECON = _env("PALIMPSEST_MODEL_RECON", "gemini-3.1-flash-image-preview")
DEFAULT_MODEL_AGENT = _env("PALIMPSEST_MODEL_AGENT", "claude-sonnet-4-5")
DEFAULT_MODEL_SCHOLAR_AGENT = _env("PALIMPSEST_MODEL_SCHOLAR_AGENT", DEFAULT_MODEL_AGENT)

DEFAULT_THINKING_LEVEL = _env("PALIMPSEST_THINKING_LEVEL", "")  # Disabled: SDK uses thinkingBudget not thinking_level
DEFAULT_MEDIA_RESOLUTION = _env("PALIMPSEST_MEDIA_RESOLUTION", "high")
DEFAULT_TECTONIC_BIN = _env("PALIMPSEST_TECTONIC_BIN", "")
DEFAULT_EDITION_FONT_LATIN = _env("PALIMPSEST_EDITION_FONT_LATIN", "")
DEFAULT_EDITION_FONT_CJK = _env("PALIMPSEST_EDITION_FONT_CJK", "")
