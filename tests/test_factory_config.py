"""Environment-isolated tests for factory configuration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import dotenv
import pytest

_CONFIG_PATH = Path(__file__).parents[1] / "palimpsest" / "factory" / "config.py"
_MODEL_ENVIRONMENT = {
    "PALIMPSEST_MODEL_VISION",
    "PALIMPSEST_MODEL_READING",
    "PALIMPSEST_MODEL_READING_SECONDARY",
    "PALIMPSEST_MODEL_ADJUDICATOR",
}


def _load_config(monkeypatch: pytest.MonkeyPatch, **environment: str) -> ModuleType:
    for key in _MODEL_ENVIRONMENT:
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)

    spec = importlib.util.spec_from_file_location(
        "_isolated_factory_config", _CONFIG_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_vision_model_requires_explicit_rename(monkeypatch):
    with pytest.raises(
        RuntimeError,
        match=(
            "PALIMPSEST_MODEL_VISION is no longer supported; rename it to "
            "PALIMPSEST_MODEL_READING"
        ),
    ):
        _load_config(
            monkeypatch,
            PALIMPSEST_MODEL_VISION="legacy-vision-override",
        )


def test_reading_model_takes_precedence_over_legacy_model(monkeypatch):
    config = _load_config(
        monkeypatch,
        PALIMPSEST_MODEL_VISION="legacy-vision-override",
        PALIMPSEST_MODEL_READING="current-reading-override",
    )

    assert config.MODEL_READING == "current-reading-override"


def test_model_defaults_load_unchanged(monkeypatch):
    config = _load_config(monkeypatch)

    assert config.MODEL_READING == "openai-codex/gpt-5.6-sol"
    assert config.MODEL_READING_SECONDARY == "google/gemini-3.6-flash"
    assert config.MODEL_ADJUDICATOR == "anthropic/claude-fable-5"
