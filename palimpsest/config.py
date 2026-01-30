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


DEFAULT_MODEL_VISION = _env("PALIMPSEST_MODEL_VISION", _env("PALIMPSEST_MODEL", "gemini-3-flash-preview"))
DEFAULT_MODEL_TRIAGE = _env("PALIMPSEST_MODEL_TRIAGE", DEFAULT_MODEL_VISION)
DEFAULT_MODEL_RECON = _env("PALIMPSEST_MODEL_RECON", "gemini-3-pro-image-preview")

DEFAULT_THINKING_LEVEL = _env("PALIMPSEST_THINKING_LEVEL", "high")
DEFAULT_MEDIA_RESOLUTION = _env("PALIMPSEST_MEDIA_RESOLUTION", "high")

