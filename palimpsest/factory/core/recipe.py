"""Recipe loading + validation (FACTORY.md §2.3).

A recipe is a YAML route sheet. ``${VAR}`` values interpolate from the
factory config's model defaults first, then the environment. Validation
happens entirely at load time: unknown stations, unknown artifact kinds, and
broken consumes/produces chains all fail before a single API call.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from palimpsest.factory import config
from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import SOURCE_KINDS
from palimpsest.factory.core.station import Station
from palimpsest.factory.workspace.layout import DOC_KIND_FILENAME, PAGE_KIND_SUFFIX

_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_CONFIG_VARS = {
    "PALIMPSEST_MODEL_VISION": config.MODEL_VISION,
    "PALIMPSEST_MODEL_READING": config.MODEL_READING,
}
_SPEC_KEYS = {"station", "model", "prompt", "params"}


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
    page_stations: tuple[StationSpec, ...] = field(default_factory=tuple)
    manuscript_stations: tuple[StationSpec, ...] = field(default_factory=tuple)


def load(name: str, recipes_dir: Path | None = None) -> Recipe:
    root = recipes_dir if recipes_dir is not None else config.RECIPES_DIR
    path = root / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Recipe not found: {path}")
    raw = yaml.safe_load(_interpolate(path.read_text(encoding="utf-8")))

    line = raw.get("line") or {}
    recipe = Recipe(
        name=raw["name"],
        language=raw.get("language", ""),
        page_stations=tuple(
            _spec(slot, expected_grain="page") for slot in line.get("page", [])
        ),
        manuscript_stations=tuple(
            _spec(slot, expected_grain="manuscript")
            for slot in line.get("manuscript", [])
        ),
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


def _spec(slot: dict, *, expected_grain: str) -> StationSpec:
    station = registry.get(slot["station"])
    if station.grain != expected_grain:
        raise ValueError(
            f"Station {station.name!r} is {station.grain}-grain but listed "
            f"under line.{expected_grain}"
        )
    if station.uses_model and not (slot.get("model") and slot.get("prompt")):
        raise ValueError(f"Station {station.name!r} requires 'model' and 'prompt'")

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
    return StationSpec(
        station=station,
        model=slot.get("model"),
        prompt_name=slot.get("prompt"),
        params=params,
        options=options,
    )


def _validate_chain(recipe: Recipe) -> None:
    known_kinds = set(PAGE_KIND_SUFFIX) | set(DOC_KIND_FILENAME)
    specs = list(recipe.page_stations) + list(recipe.manuscript_stations)
    if not specs:
        raise ValueError(f"Recipe {recipe.name!r} lists no stations")

    producible = set(SOURCE_KINDS) | {spec.station.produces for spec in specs}
    for spec in specs:
        station = spec.station
        for kind in (*station.consumes, station.produces):
            if kind not in known_kinds:
                raise ValueError(
                    f"Station {station.name!r} references unknown kind {kind!r}"
                )
        missing = [kind for kind in station.consumes if kind not in producible]
        if missing:
            raise ValueError(
                f"Station {station.name!r} consumes {missing} which nothing "
                f"in recipe {recipe.name!r} produces"
            )

    # Page-line order: a page station's page-grain inputs must be produced
    # by an EARLIER page station (manuscript-grain jigs are gate-checked at
    # run time by the conductor instead).
    available = set(SOURCE_KINDS)
    for spec in recipe.page_stations:
        for kind in spec.station.consumes:
            if kind in PAGE_KIND_SUFFIX and kind not in available:
                raise ValueError(
                    f"Page station {spec.station.name!r} consumes {kind!r} "
                    f"before any earlier station produces it"
                )
        available.add(spec.station.produces)
