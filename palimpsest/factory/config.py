"""Factory configuration: the one place for paths and model defaults.

Env-backed via ``.env`` at the project root. Recipes reference these values
with ``${VAR}`` interpolation; nothing else in the factory reads ``os.environ``
directly.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _reject_legacy_model_vision() -> None:
    if os.getenv("PALIMPSEST_MODEL_VISION") is not None and not os.getenv(
        "PALIMPSEST_MODEL_READING"
    ):
        raise RuntimeError(
            "PALIMPSEST_MODEL_VISION is no longer supported; rename it to "
            "PALIMPSEST_MODEL_READING."
        )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)
_reject_legacy_model_vision()

FACTORY_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = FACTORY_ROOT / "prompts"
RECIPES_DIR = FACTORY_ROOT / "recipes"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key) or default


LIBRARY_ROOT = Path(_env("PALIMPSEST_LIBRARY_ROOT", str(PROJECT_ROOT / "library")))
FACTORY_DB_PATH = Path(_env("PALIMPSEST_FACTORY_DB", str(LIBRARY_ROOT / "factory.db")))
CATALOG_DB_PATH = Path(_env("PALIMPSEST_CATALOG_DB", str(LIBRARY_ROOT / "catalog.db")))

# Model defaults used by recipe interpolation.
MODEL_READING = _env("PALIMPSEST_MODEL_READING", "openai-codex/gpt-5.6-sol")
MODEL_READING_SECONDARY = _env(
    "PALIMPSEST_MODEL_READING_SECONDARY", "google/gemini-3.6-flash"
)
MODEL_ADJUDICATOR = _env("PALIMPSEST_MODEL_ADJUDICATOR", "anthropic/claude-fable-5")
