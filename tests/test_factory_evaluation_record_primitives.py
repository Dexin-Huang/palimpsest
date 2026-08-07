"""Focused contracts for the shared strict record-validation primitives.

The five record domains (candidate, suite, promotion, store, Exodia adapter)
all delegate to ``_record``; their own tests exercise each primitive through
one error class.  These tests pin the parameterization surface itself: error
class injection and the strictness flags.
"""

from __future__ import annotations

import json

import pytest

from palimpsest.factory.evaluation import _record


class _DomainError(ValueError):
    pass


class _OtherError(ValueError):
    pass


def test_unique_key_loader_rejects_duplicates_and_unhashable_keys() -> None:
    loader = _record.make_unique_key_loader(_DomainError)
    with pytest.raises(_DomainError, match="Duplicate YAML key"):
        __import__("yaml").load("a: 1\na: 2", Loader=loader)
    with pytest.raises(_DomainError, match="must be scalar"):
        __import__("yaml").load("? [1, 2]\n: value", Loader=loader)
    # A duplicate YAML key must not raise the wrong domain error class.
    other_loader = _record.make_unique_key_loader(_OtherError)
    with pytest.raises(_OtherError):
        __import__("yaml").load("a: 1\na: 2", Loader=other_loader)


def test_strict_mapping_validates_keys_and_strict_type() -> None:
    value: object = {"a": 1, "extra": 2}
    with pytest.raises(_DomainError, match="Unknown record keys: \\['extra'\\]"):
        _record.strict_mapping(
            value, field="record", required={"a"}, error_cls=_DomainError
        )
    with pytest.raises(_DomainError, match="Missing record keys: \\['b'\\]"):
        _record.strict_mapping(
            {"a": 1}, field="record", required={"a", "b"}, error_cls=_DomainError
        )
    with pytest.raises(_DomainError, match="must be a mapping"):
        _record.strict_mapping(
            [1], field="record", required=set(), error_cls=_DomainError
        )

    class DictSubclass(dict):
        pass

    subclass: object = DictSubclass({"a": 1})
    assert (
        _record.strict_mapping(
            subclass, field="record", required={"a"}, error_cls=_DomainError
        )
        is subclass
    )
    with pytest.raises(_DomainError, match="must be a mapping"):
        _record.strict_mapping(
            subclass,
            field="record",
            required={"a"},
            error_cls=_DomainError,
            strict_type=True,
        )


def test_string_flags_allow_empty_strict_type_and_reject_nul() -> None:
    assert (
        _record.string(" x ", field="f", error_cls=_DomainError, allow_empty=True)
        == " x "
    )
    with pytest.raises(_DomainError, match="non-empty string"):
        _record.string("", field="f", error_cls=_DomainError)
    assert _record.string("  ", field="f", error_cls=_DomainError, allow_empty=True) == "  "
    with pytest.raises(_DomainError, match="non-empty string"):
        _record.string("  ", field="f", error_cls=_DomainError)
    with pytest.raises(_DomainError, match="non-empty string"):
        _record.string("a\x00b", field="f", error_cls=_DomainError, reject_nul=True)
    assert _record.string("a\x00b", field="f", error_cls=_DomainError) == "a\x00b"

    class StrSubclass(str):
        pass

    assert _record.string(StrSubclass("ok"), field="f", error_cls=_DomainError) == "ok"
    with pytest.raises(_DomainError):
        _record.string(
            StrSubclass("ok"), field="f", error_cls=_DomainError, strict_type=True
        )


def test_number_rejects_bool_and_non_finite_and_applies_minimum() -> None:
    assert _record.number(3, field="f", error_cls=_DomainError) == 3.0
    assert _record.number(3.5, field="f", error_cls=_DomainError, minimum=3.0) == 3.5
    for bad in (True, "3", None, float("nan"), float("inf")):
        with pytest.raises(_DomainError):
            _record.number(bad, field="f", error_cls=_DomainError)
    with pytest.raises(_DomainError, match="at least"):
        _record.number(2.9, field="f", error_cls=_DomainError, minimum=3.0)


def test_strict_keys_requires_exact_key_set() -> None:
    value = {"a": 1, "c": 2}
    _record.strict_keys(value, {"a", "c"}, field="f", error_cls=_DomainError)
    with pytest.raises(_DomainError, match="missing=\\['b'\\], unknown=\\['c'\\]"):
        _record.strict_keys(value, {"a", "b"}, field="f", error_cls=_DomainError)


def test_schema_version_requires_integer_one() -> None:
    assert _record.schema_version(1, field="schema_version", error_cls=_DomainError) == 1
    for bad in (0, 2, True, "1", 1.0):
        with pytest.raises(_DomainError, match="must be integer 1"):
            _record.schema_version(bad, field="schema_version", error_cls=_DomainError)


def test_duplicate_key_json_hook_rejects_duplicates() -> None:
    hook = _record.make_duplicate_key_json_hook(_DomainError)
    with pytest.raises(_DomainError, match="Duplicate JSON key"):
        json.loads('{"a": 1, "a": 2}', object_pairs_hook=hook)
    assert json.loads('{"a": 1}', object_pairs_hook=hook) == {"a": 1}


def test_canonical_json_and_fingerprint_propagate_injected_error_class() -> None:
    assert _record.canonical_json({"b": 1, "a": [1, 2]}, error_cls=_DomainError) == (
        '{"a":[1,2],"b":1}'
    )
    with pytest.raises(_DomainError, match="not canonical JSON"):
        _record.canonical_json({object(): 1}, error_cls=_DomainError)
    with pytest.raises(_DomainError):
        _record.content_fingerprint({object(): 1}, error_cls=_DomainError)
    digest = _record.content_fingerprint({"a": 1}, error_cls=_DomainError)
    assert digest == _record.content_fingerprint({"a": 1}, error_cls=_OtherError)
    assert len(digest) == 64
