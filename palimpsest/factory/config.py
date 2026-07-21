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
load_dotenv(ENV_PATH)

FACTORY_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = FACTORY_ROOT / "prompts"
RECIPES_DIR = FACTORY_ROOT / "recipes"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key) or default


LIBRARY_ROOT = Path(_env("PALIMPSEST_LIBRARY_ROOT", str(PROJECT_ROOT / "library")))
FACTORY_DB_PATH = Path(_env("PALIMPSEST_FACTORY_DB", str(LIBRARY_ROOT / "factory.db")))

# Model defaults used by recipe interpolation.
MODEL_VISION = _env("PALIMPSEST_MODEL_VISION", "gemini-3.6-flash")
MODEL_READING = _env("PALIMPSEST_MODEL_READING", "gemini-3.5-flash-lite")
