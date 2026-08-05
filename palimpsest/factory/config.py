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


def _positive_int_env(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be a positive integer, got {raw!r}") from None
    if value < 1:
        raise RuntimeError(f"{key} must be a positive integer, got {raw!r}")
    return value


LIBRARY_ROOT = Path(_env("PALIMPSEST_LIBRARY_ROOT", str(PROJECT_ROOT / "library")))
FACTORY_DB_PATH = Path(_env("PALIMPSEST_FACTORY_DB", str(LIBRARY_ROOT / "factory.db")))
CATALOG_DB_PATH = Path(_env("PALIMPSEST_CATALOG_DB", str(LIBRARY_ROOT / "catalog.db")))

# Model defaults used by recipe interpolation.
MODEL_READING = _env("PALIMPSEST_MODEL_READING", "token-plan/qwen3.8-max")
MODEL_READING_SECONDARY = _env(
    "PALIMPSEST_MODEL_READING_SECONDARY", "openai-codex/gpt-5.6-sol"
)
MODEL_EDITORIAL = _env("PALIMPSEST_MODEL_EDITORIAL", "openai-codex/gpt-5.6-sol")
MODEL_ADJUDICATOR = _env("PALIMPSEST_MODEL_ADJUDICATOR", "openai-codex/gpt-5.6-sol")

# Operational limits. Model calls are expensive enough that a local timeout is
# terminal rather than retried; keep these generous and tune concurrency first.
MODEL_PROVIDER_WORKERS = _positive_int_env("PALIMPSEST_MODEL_PROVIDER_WORKERS", 3)
MODEL_TIMEOUT_SECONDS = _positive_int_env("PALIMPSEST_MODEL_TIMEOUT_SECONDS", 7200)
AGENT_TIMEOUT_SECONDS = _positive_int_env("PALIMPSEST_AGENT_TIMEOUT_SECONDS", 14400)
CELL_TIMEOUT_SECONDS = _positive_int_env("PALIMPSEST_CELL_TIMEOUT_SECONDS", 28800)
