"""Strict candidate records and content-derived candidate identity."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml

from palimpsest.factory import prompt_store

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_MOVING_MODEL_PART = re.compile(r"(?:^|[-_/])(latest|stable|auto)(?:$|[-_/])", re.I)
_JSON_SCALARS = (str, int, float, bool, type(None))


class RecordError(ValueError):
    """A tracked evaluation record is malformed or cannot be resolved."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        try:
            duplicate = key in result
        except TypeError as error:
            raise RecordError("YAML mapping keys must be scalar") from error
        if duplicate:
            raise RecordError(f"Duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=False)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def canonical_json(value: object) -> str:
    """Serialize a JSON value in the one representation used for identities."""
    try:
        return json.dumps(
            _mutable_json(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise RecordError(f"Record is not canonical JSON data: {error}") from error


def content_fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def immutable_json(value: object, *, field: str = "record") -> object:
    """Validate and recursively freeze JSON data."""
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RecordError(f"{field} keys must be strings")
            frozen[key] = immutable_json(item, field=f"{field}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            immutable_json(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, _JSON_SCALARS):
        if isinstance(value, float) and not (-float("inf") < value < float("inf")):
            raise RecordError(f"{field} must be finite")
        return value
    raise RecordError(
        f"{field} must contain only JSON values, got {type(value).__name__}"
    )


def _mutable_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_json(item) for item in value]
    return value


def _strict_mapping(
    value: object,
    *,
    field: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecordError(f"{field} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise RecordError(f"{field} keys must be strings")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise RecordError(f"Unknown {field} keys: {sorted(unknown)}")
    if missing:
        raise RecordError(f"Missing {field} keys: {sorted(missing)}")
    return value


def _string(value: object, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RecordError(f"{field} must be a non-empty string")
    return value


def _record_id(value: object, *, field: str) -> str:
    result = _string(value, field=field)
    if (
        not _RECORD_ID.fullmatch(result)
        or "\\" in result
        or result.startswith("/")
        or any(part in {"", ".", ".."} for part in result.split("/"))
    ):
        raise RecordError(f"{field} is not a safe record name: {result!r}")
    return result


def _schema_version(value: object) -> int:
    if type(value) is not int or value != 1:
        raise RecordError("schema_version must be integer 1")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise RecordError(f"Expected a YAML record: {path}")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise RecordError(f"Cannot load {path}: {error}") from error
    return (
        _strict_mapping(value, field="record", required=set()) if value == {} else value
    )


def _reject_environment_values(value: object, *, field: str = "record") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_environment_values(item, field=f"{field}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_environment_values(item, field=f"{field}[{index}]")
    elif isinstance(value, str) and (
        "${" in value or re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", value)
    ):
        raise RecordError(f"Environment interpolation is forbidden in {field}")


def default_model_identity(model: str) -> Literal["fixed", "moving"]:
    """Classify explicit moving aliases conservatively without provider calls."""
    lower = model.lower()
    moving = bool(_MOVING_MODEL_PART.search(lower)) or lower.endswith("-preview")
    return "moving" if moving else "fixed"


def _resolve_prompt(
    name: str,
    resolver: Callable[[str], object],
) -> tuple[str, str]:
    try:
        prompt = resolver(name)
    except Exception as error:
        raise RecordError(f"Cannot resolve prompt {name!r}: {error}") from error
    resolved_name = getattr(prompt, "name", name)
    digest = getattr(prompt, "sha256", None)
    text = getattr(prompt, "text", None)
    if digest is None and isinstance(prompt, str):
        text = prompt
    if digest is None and isinstance(text, str):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (
        resolved_name != name
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise RecordError(f"Prompt resolver returned an invalid identity for {name!r}")
    return resolved_name, digest


def _call_accepts_two_arguments(call: Callable[..., object]) -> bool:
    try:
        inspect.signature(call).bind("station", "variant")
    except (TypeError, ValueError):
        return False
    return True


def _resolve_station(
    station_name: str, variant: str, registry: object | None
) -> object:
    if registry is None:
        from palimpsest.factory.core import registry as station_registry

        lookup: object = station_registry.get
    elif isinstance(registry, Mapping):
        try:
            station_group = registry[station_name]
            resolved = (
                station_group[variant]
                if isinstance(station_group, Mapping)
                else station_group
            )
        except (KeyError, TypeError):
            raise RecordError(
                f"Unknown station variant: {station_name!r}/{variant!r}"
            ) from None
        return _validate_resolved_station(resolved, station_name, variant)
    else:
        lookup = registry if callable(registry) else getattr(registry, "get", None)
    if not callable(lookup):
        raise TypeError("registry must be a mapping, callable, or expose get()")
    try:
        if _call_accepts_two_arguments(lookup):
            resolved = lookup(station_name, variant)
        else:
            resolved = lookup(station_name)
    except (KeyError, LookupError, ValueError) as error:
        raise RecordError(
            f"Unknown station variant: {station_name!r}/{variant!r}"
        ) from error
    return _validate_resolved_station(resolved, station_name, variant)


def _validate_resolved_station(
    resolved: object, station_name: str, variant: str
) -> object:
    if getattr(resolved, "name", None) != station_name:
        raise RecordError(f"Registry returned the wrong station for {station_name!r}")
    resolved_variant = getattr(resolved, "variant", None)
    if resolved_variant is not None and resolved_variant != variant:
        raise RecordError(
            f"Station {station_name!r} has variant {resolved_variant!r}, not {variant!r}"
        )
    return resolved


def _validate_station_config(
    station: object,
    params: Mapping[str, object],
    options: Mapping[str, object],
) -> None:
    for label, values, method_name, keys_name in (
        ("params", params, "validate_params", "param_keys"),
        ("options", options, "validate_options", "option_keys"),
    ):
        validator = getattr(station, method_name, None)
        if callable(validator):
            try:
                validator(values)
            except (TypeError, ValueError) as error:
                raise RecordError(f"Invalid station {label}: {error}") from error
            continue
        allowed = getattr(station, keys_name, frozenset())
        unknown = set(values) - set(allowed)
        if unknown:
            raise RecordError(f"Unknown station {label}: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class ResolvedCandidate:
    schema_version: int
    id: str
    station: str
    variant: str
    grain: Literal["page", "manuscript"]
    consumes: tuple[str, ...]
    optional_consumes: tuple[str, ...]
    produces: str
    model: str | None
    model_identity: Literal["fixed", "moving"]
    prompt_name: str | None
    prompt_hash: str | None
    params: Mapping[str, object]
    options: Mapping[str, object]
    notes: str | None
    implementation_fingerprint: str
    fingerprint: str
    tracked: bool = True

    @property
    def can_auto_qualify(self) -> bool:
        return self.tracked and self.model_identity == "fixed"


def load_candidate(
    path: str | Path,
    *,
    registry: object | None = None,
    prompt_resolver: Callable[[str], object] = prompt_store.load,
    model_identity_resolver: Callable[
        [str], Literal["fixed", "moving"]
    ] = default_model_identity,
    tracked: bool = True,
) -> ResolvedCandidate:
    """Load, fully validate, resolve, and fingerprint one candidate YAML file."""
    record = _load_yaml(Path(path))
    record = _strict_mapping(
        record,
        field="candidate",
        required={"schema_version", "id", "station", "variant", "params", "options"},
        optional={"model", "prompt", "notes"},
    )
    _reject_environment_values(record)
    schema_version = _schema_version(record["schema_version"])
    candidate_id = _record_id(record["id"], field="candidate.id")
    station_name = _record_id(record["station"], field="candidate.station")
    variant = _record_id(record["variant"], field="candidate.variant")
    if not candidate_id.startswith(f"{station_name}/"):
        raise RecordError("candidate.id must be namespaced by candidate.station")
    station = _resolve_station(station_name, variant, registry)
    grain = getattr(station, "grain", None)
    if grain not in {"page", "manuscript"}:
        raise RecordError(f"Station {station_name!r} has an invalid grain")
    consumes = tuple(getattr(station, "consumes", ()))
    optional_consumes = tuple(getattr(station, "optional_consumes", ()))
    produces = getattr(station, "produces", None)
    if (
        not all(
            isinstance(kind, str) and kind for kind in (*consumes, *optional_consumes)
        )
        or not isinstance(produces, str)
        or not produces
    ):
        raise RecordError(f"Station {station_name!r} has an invalid artifact socket")

    params_value = _strict_mapping(
        record["params"],
        field="candidate.params",
        required=set(),
        optional=set(record["params"]) if isinstance(record["params"], dict) else set(),
    )
    options_value = _strict_mapping(
        record["options"],
        field="candidate.options",
        required=set(),
        optional=set(record["options"])
        if isinstance(record["options"], dict)
        else set(),
    )
    params = immutable_json(params_value, field="candidate.params")
    options = immutable_json(options_value, field="candidate.options")
    assert isinstance(params, Mapping) and isinstance(options, Mapping)
    _validate_station_config(station, params, options)

    uses_model = bool(getattr(station, "uses_model", False))
    model_raw = record.get("model")
    prompt_raw = record.get("prompt")
    if uses_model:
        model = _string(model_raw, field="candidate.model")
        prompt_name = _record_id(prompt_raw, field="candidate.prompt")
        prompt_name, prompt_hash = _resolve_prompt(prompt_name, prompt_resolver)
        model_identity = model_identity_resolver(model)
        if model_identity not in {"fixed", "moving"}:
            raise RecordError("model identity resolver must return 'fixed' or 'moving'")
    else:
        if model_raw is not None or prompt_raw is not None:
            raise RecordError("Deterministic station variants reject model and prompt")
        model = None
        prompt_name = None
        prompt_hash = None
        model_identity = "fixed"

    notes_raw = record.get("notes")
    notes = (
        None
        if notes_raw is None
        else _string(notes_raw, field="candidate.notes", allow_empty=True)
    )
    implementation_fingerprint = getattr(station, "implementation_fingerprint", None)
    if (
        not isinstance(implementation_fingerprint, str)
        or not implementation_fingerprint
    ):
        raise RecordError("Station variant has no implementation fingerprint")

    identity = {
        "schema_version": schema_version,
        "id": candidate_id,
        "station": station_name,
        "variant": variant,
        "grain": grain,
        "consumes": consumes,
        "optional_consumes": optional_consumes,
        "produces": produces,
        "model": model,
        "model_identity": model_identity,
        "prompt": prompt_name,
        "prompt_hash": prompt_hash,
        "params": params,
        "options": options,
        "notes": notes,
        "implementation_fingerprint": implementation_fingerprint,
    }
    return ResolvedCandidate(
        schema_version=schema_version,
        id=candidate_id,
        station=station_name,
        variant=variant,
        grain=grain,
        consumes=consumes,
        optional_consumes=optional_consumes,
        produces=produces,
        model=model,
        model_identity=model_identity,
        prompt_name=prompt_name,
        prompt_hash=prompt_hash,
        params=params,
        options=options,
        notes=notes,
        implementation_fingerprint=implementation_fingerprint,
        fingerprint=content_fingerprint(identity),
        tracked=tracked,
    )
