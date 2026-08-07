"""Shared strict record-validation primitives for the evaluation plane.

Every record domain (candidate, suite, promotion, store, and the Exodia
adapter) used to re-derive the same micro-validators with a per-domain error
class.  This module is the single implementation: each checker takes the
domain's error class explicitly, so the validation logic lives here while the
error taxonomy stays per-domain.

The strictness flags mirror the domains that hardened externally-facing
checks: ``strict_type`` rejects subclasses (Exodia), ``reject_nul`` forbids
NUL bytes (Exodia), and ``allow_empty`` permits blank strings where a record
does so.  The fingerprint serializer (``canonical_json`` /
``content_fingerprint``) is shared because it is content-addressing; the
Exodia adapter keeps its own raw-UTF-8 wire serializer, which is a different
representation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import yaml

_ErrorT = TypeVar("_ErrorT", bound=type[Exception])


def make_unique_key_loader(error_cls: _ErrorT) -> type[yaml.SafeLoader]:
    """A SafeLoader subclass whose duplicate mapping keys raise ``error_cls``.

    Merge keys are flattened first, so an alias merged over an explicit key is
    still detected as a duplicate.
    """

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
                raise error_cls("YAML mapping keys must be scalar") from error
            if duplicate:
                raise error_cls(f"Duplicate YAML key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=False)
        return result

    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
    )
    return _UniqueKeyLoader


def make_duplicate_key_json_hook(
    error_cls: _ErrorT,
) -> Callable[[list[tuple[str, object]]], dict[str, object]]:
    """An ``object_pairs_hook`` for ``json.loads`` that rejects duplicate keys."""

    def _reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise error_cls(f"Duplicate JSON key: {key!r}")
            result[key] = value
        return result

    return _reject_duplicate_keys


def strict_mapping(
    value: object,
    *,
    field: str,
    required: set[str],
    optional: set[str] = frozenset(),
    error_cls: _ErrorT,
    strict_type: bool = False,
) -> dict[str, Any]:
    """Validate a mapping's type, string keys, and exact key set."""
    if strict_type:
        valid = type(value) is dict
    else:
        valid = isinstance(value, dict)
    if not valid:
        raise error_cls(f"{field} must be a mapping")
    for key in value:
        is_string = type(key) is str if strict_type else isinstance(key, str)
        if not is_string:
            raise error_cls(f"{field} keys must be strings")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown:
        raise error_cls(f"Unknown {field} keys: {sorted(unknown)}")
    if missing:
        raise error_cls(f"Missing {field} keys: {sorted(missing)}")
    return value  # type: ignore[return-value]


def string(
    value: object,
    *,
    field: str,
    error_cls: _ErrorT,
    allow_empty: bool = False,
    strict_type: bool = False,
    reject_nul: bool = False,
) -> str:
    """Validate a string, rejecting blanks unless ``allow_empty`` is set."""
    valid = type(value) is str if strict_type else isinstance(value, str)
    if not valid or (not allow_empty and not value.strip()) or (
        reject_nul and "\x00" in value
    ):
        raise error_cls(f"{field} must be a non-empty string")
    return value


def number(
    value: object,
    *,
    field: str,
    error_cls: _ErrorT,
    minimum: float | None = None,
) -> float:
    """Validate a finite number, optionally with a lower bound."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_cls(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise error_cls(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise error_cls(f"{field} must be at least {minimum}")
    return result


def strict_keys(
    value: Mapping[str, object],
    keys: set[str],
    *,
    field: str,
    error_cls: _ErrorT,
) -> None:
    """Require the mapping's keys to equal ``keys`` exactly."""
    actual = set(value)
    missing = sorted(keys - actual)
    unknown = sorted(actual - keys)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise error_cls(f"{field} fields are invalid: {', '.join(details)}")


def schema_version(value: object, *, field: str, error_cls: _ErrorT) -> int:
    """Require the record's schema version to be exactly 1."""
    if type(value) is not int or value != 1:
        raise error_cls(f"{field} must be integer 1")
    return value


def _mutable_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_json(item) for item in value]
    return value


def canonical_json(value: object, *, error_cls: _ErrorT) -> str:
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
        raise error_cls(f"Record is not canonical JSON data: {error}") from error


def content_fingerprint(value: object, *, error_cls: _ErrorT) -> str:
    """SHA-256 of the canonical JSON representation of ``value``."""
    return hashlib.sha256(
        canonical_json(value, error_cls=error_cls).encode("utf-8")
    ).hexdigest()
