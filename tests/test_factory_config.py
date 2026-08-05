"""Environment-isolated tests for factory configuration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import dotenv
import pytest

_CONFIG_PATH = Path(__file__).parents[1] / "palimpsest" / "factory" / "config.py"
_CONFIG_ENVIRONMENT = {
    "PALIMPSEST_MODEL_VISION",
    "PALIMPSEST_MODEL_READING",
    "PALIMPSEST_MODEL_READING_SECONDARY",
    "PALIMPSEST_MODEL_EDITORIAL",
    "PALIMPSEST_MODEL_ADJUDICATOR",
    "PALIMPSEST_MODEL_PROVIDER_WORKERS",
    "PALIMPSEST_MODEL_TIMEOUT_SECONDS",
    "PALIMPSEST_AGENT_TIMEOUT_SECONDS",
    "PALIMPSEST_CELL_TIMEOUT_SECONDS",
}


def _load_config(monkeypatch: pytest.MonkeyPatch, **environment: str) -> ModuleType:
    for key in _CONFIG_ENVIRONMENT:
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

    assert config.MODEL_READING == "google/gemini-3.5-flash"
    assert config.MODEL_READING_SECONDARY == "openai-codex/gpt-5.6-sol"
    assert config.MODEL_EDITORIAL == "openai-codex/gpt-5.6-sol"
    assert config.MODEL_ADJUDICATOR == "openai-codex/gpt-5.6-sol"
    assert config.MODEL_PROVIDER_WORKERS == 3
    assert config.MODEL_TIMEOUT_SECONDS == 7200
    assert config.AGENT_TIMEOUT_SECONDS == 14400
    assert config.CELL_TIMEOUT_SECONDS == 28800


def test_operational_limits_accept_positive_overrides(monkeypatch):
    config = _load_config(
        monkeypatch,
        PALIMPSEST_MODEL_PROVIDER_WORKERS="2",
        PALIMPSEST_MODEL_TIMEOUT_SECONDS="9000",
        PALIMPSEST_AGENT_TIMEOUT_SECONDS="18000",
        PALIMPSEST_CELL_TIMEOUT_SECONDS="36000",
    )

    assert config.MODEL_PROVIDER_WORKERS == 2
    assert config.MODEL_TIMEOUT_SECONDS == 9000
    assert config.AGENT_TIMEOUT_SECONDS == 18000
    assert config.CELL_TIMEOUT_SECONDS == 36000


@pytest.mark.parametrize("value", ["0", "-1", "unbounded"])
def test_operational_limits_reject_invalid_values(monkeypatch, value):
    with pytest.raises(
        RuntimeError,
        match="PALIMPSEST_MODEL_TIMEOUT_SECONDS must be a positive integer",
    ):
        _load_config(monkeypatch, PALIMPSEST_MODEL_TIMEOUT_SECONDS=value)
