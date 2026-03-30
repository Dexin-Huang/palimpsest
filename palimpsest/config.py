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


# Transcription (VLM OCR) — Pro for quality
DEFAULT_MODEL_TRANSCRIPTION = _env("PALIMPSEST_MODEL_TRANSCRIPTION", "gemini-3.1-pro-preview")

# Lightweight tasks (triage, translation, continuity) — Flash Lite for cost
DEFAULT_MODEL_READING = _env("PALIMPSEST_MODEL_READING", "gemini-3.1-flash-lite-preview")
DEFAULT_MODEL_TRIAGE = _env("PALIMPSEST_MODEL_TRIAGE", DEFAULT_MODEL_READING)

# Agent tasks (scholar, scout) — Claude
DEFAULT_MODEL_SCHOLAR_AGENT = _env("PALIMPSEST_MODEL_SCHOLAR_AGENT", "claude-sonnet-4-5")
