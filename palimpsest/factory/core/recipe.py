"""Recipe loading + validation (FACTORY.md §2.3).

A recipe is a YAML route sheet. ``${VAR}`` values interpolate from the
factory config's model defaults first, then the environment. Validation
happens entirely at load time: unknown stations, unknown artifact kinds, and
broken consumes/produces chains all fail before a single API call.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from palimpsest.factory import config
from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import SOURCE_KINDS, contract
from palimpsest.factory.core.contracts import SOURCE_KINDS
from palimpsest.factory.core.station import Station

_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_CONFIG_VARS = {
    "PALIMPSEST_MODEL_READING": config.MODEL_READING,
    "PALIMPSEST_MODEL_READING_SECONDARY": config.MODEL_READING_SECONDARY,
    "PALIMPSEST_MODEL_EDITORIAL": config.MODEL_EDITORIAL,
    "PALIMPSEST_MODEL_ADJUDICATOR": config.MODEL_ADJUDICATOR,
}
_SPEC_KEYS = {"station", "variant", "model", "prompt", "params"}


@dataclass(frozen=True)
class StationSpec:
    station: Station
    model: str | None
    prompt_name: str | None
    params: Mapping[str, Any]
    options: Mapping[str, Any]  # everything else in the slot (profile, overlap, …)


@dataclass(frozen=True)
class Recipe:
    name: str
    language: str
    steps: tuple[StationSpec, ...]


def load(name: str, recipes_dir: Path | None = None) -> Recipe:
    root = recipes_dir if recipes_dir is not None else config.RECIPES_DIR
    path = root / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Recipe not found: {path}")
    raw = yaml.safe_load(_interpolate(path.read_text(encoding="utf-8")))

    line = raw.get("line") or []
    if not isinstance(line, list):
        raise ValueError(f"Recipe {name!r} line must be an ordered list")
    recipe = Recipe(
        name=raw["name"],
        language=raw.get("language", ""),
        steps=tuple(_spec(slot) for slot in line),
    )
    _validate_chain(recipe)
    return recipe


def _interpolate(text: str) -> str:
    def resolve(match: re.Match) -> str:
        var = match.group(1)
        value = os.getenv(var) or _CONFIG_VARS.get(var)
        if not value:
            raise ValueError(f"Recipe variable ${{{var}}} is not set")
        return value

    return _VAR_RE.sub(resolve, text)


def _spec(slot: dict) -> StationSpec:
    station = registry.get(slot["station"], slot.get("variant"))
    if station.uses_model and not (slot.get("model") and slot.get("prompt")):
        raise ValueError(f"Station {station.name!r} requires 'model' and 'prompt'")
    if not station.uses_model and (slot.get("model") or slot.get("prompt")):
        raise ValueError(
            f"Station {station.name!r} is local but received 'model' or "
            "'prompt'; they would silently change freshness identity"
        )

    params = slot.get("params") or {}
    options = {key: value for key, value in slot.items() if key not in _SPEC_KEYS}
    unknown_params = sorted(set(params) - station.param_keys)
    unknown_options = sorted(set(options) - station.option_keys)
    if unknown_params or unknown_options:
        details = []
        if unknown_params:
            details.append(f"params={unknown_params}")
        if unknown_options:
            details.append(f"options={unknown_options}")
        raise ValueError(
            f"Station {station.name!r} received unknown recipe keys: "
            + ", ".join(details)
        )
    validate_options = getattr(station, "validate_options", None)
    if callable(validate_options):
        try:
            validate_options(options)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Station {station.name!r} rejected recipe options: {error}"
            ) from error
    return StationSpec(
        station=station,
        model=slot.get("model"),
        prompt_name=slot.get("prompt"),
        params=params,
        options=options,
    )


def _validate_chain(recipe: Recipe) -> None:
    if not recipe.steps:
        raise ValueError(f"Recipe {recipe.name!r} lists no stations")

    producers: dict[str, str] = {}
    available = set(SOURCE_KINDS)
    for spec in recipe.steps:
        kind = spec.station.produces
        if kind in producers:
            raise ValueError(
                f"Recipe {recipe.name!r} produces {kind!r} twice: "
                f"{producers[kind]!r} and {spec.station.name!r}"
            )
        missing = [kind for kind in spec.station.consumes if kind not in available]
        if missing:
            raise ValueError(
                f"Station {spec.station.name!r} consumes {missing} before "
                "any earlier station produces them"
            )
        producers[kind] = spec.station.name
        available.add(kind)
