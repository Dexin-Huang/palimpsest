"""Factory configuration: the one place for paths and model defaults.

Env-backed via ``.env`` at the project root. Recipes reference these values
with ``${VAR}`` interpolation; nothing else in the factory reads ``os.environ``
directly.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

FACTORY_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = FACTORY_ROOT / "prompts"
RECIPES_DIR = FACTORY_ROOT / "recipes"


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value if value is not None and value != "" else default


LIBRARY_ROOT = Path(_env("PALIMPSEST_LIBRARY_ROOT", str(PROJECT_ROOT / "library")))
FACTORY_DB_PATH = Path(_env("PALIMPSEST_FACTORY_DB", str(LIBRARY_ROOT / "factory.db")))

# Model defaults per lane. Recipe bindings override these per station.
MODEL_VISION = _env("PALIMPSEST_MODEL_VISION", "gemini-3.1-pro-preview")
MODEL_READING = _env("PALIMPSEST_MODEL_READING", "gemini-3.1-flash-lite-preview")
MODEL_TRIAGE = _env("PALIMPSEST_MODEL_TRIAGE", MODEL_READING)
MODEL_RECON = _env("PALIMPSEST_MODEL_RECON", "gemini-3.1-flash-image-preview")
MODEL_SCOUT = _env("PALIMPSEST_MODEL_SCHOLAR_AGENT", "claude-sonnet-4-5")
