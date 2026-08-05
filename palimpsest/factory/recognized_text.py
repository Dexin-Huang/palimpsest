"""Versioned normalization and provenance for recognized text."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping as _Mapping, Sequence as _Sequence

from palimpsest.factory.han_variants import (
    HAN_VARIANT_TABLE_SHA256 as _HAN_VARIANT_TABLE_SHA256,
    HAN_VARIANT_TABLE_V2_SHA256 as _HAN_VARIANT_TABLE_V2_SHA256,
    HAN_VARIANT_TABLE_V2_VERSION as _HAN_VARIANT_TABLE_V2_VERSION,
    HAN_VARIANT_TABLE_VERSION as _HAN_VARIANT_TABLE_VERSION,
    normalize_han_variants_v1 as _normalize_han_variants_v1,
    normalize_han_variants_v2 as _normalize_han_variants_v2,
)

__all__ = [
    "RECOGNIZED_TEXT_PROFILE_ID",
    "RECOGNIZED_TEXT_PROFILE_VERSION",
    "RECOGNIZED_TEXT_PROFILE_SHA256",
    "RECOGNIZED_TEXT_V2_PROFILE_ID",
    "RECOGNIZED_TEXT_V2_PROFILE_VERSION",
    "RECOGNIZED_TEXT_V2_PROFILE_SHA256",
    "normalize_recognized_text_v1",
    "normalize_recognized_text_v2",
    "recognized_provenance_sha256",
    "recognized_reference_text",
]

RECOGNIZED_TEXT_PROFILE_ID = "recognized_text_v1"
RECOGNIZED_TEXT_PROFILE_VERSION = 1

_PROFILE_DEFINITION = {
    "id": RECOGNIZED_TEXT_PROFILE_ID,
    "version": RECOGNIZED_TEXT_PROFILE_VERSION,
    "stages": [
        "line_endings",
        "unicode_nfc",
        "han_variants_v1",
        "compact_numerals",
        "han_spacing",
    ],
    "han_variant_table_version": _HAN_VARIANT_TABLE_VERSION,
    "han_variant_table_sha256": _HAN_VARIANT_TABLE_SHA256,
}
_PROFILE_CANONICAL_JSON = json.dumps(
    _PROFILE_DEFINITION,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
)
RECOGNIZED_TEXT_PROFILE_SHA256 = hashlib.sha256(
    _PROFILE_CANONICAL_JSON.encode("utf-8")
).hexdigest()

RECOGNIZED_TEXT_V2_PROFILE_ID = "recognized_text_v2"
RECOGNIZED_TEXT_V2_PROFILE_VERSION = 2

# The v2 profile swaps only the Han variant table stage. Gold provenance and
# the campaign metric stay pinned to v1 until an explicit ratified cutover;
# v2 exists for dual-reporting and the routing comparator.
_PROFILE_V2_DEFINITION = {
    "id": RECOGNIZED_TEXT_V2_PROFILE_ID,
    "version": RECOGNIZED_TEXT_V2_PROFILE_VERSION,
    "stages": [
        "line_endings",
        "unicode_nfc",
        "han_variants_v2",
        "compact_numerals",
        "han_spacing",
    ],
    "han_variant_table_version": _HAN_VARIANT_TABLE_V2_VERSION,
    "han_variant_table_sha256": _HAN_VARIANT_TABLE_V2_SHA256,
}
RECOGNIZED_TEXT_V2_PROFILE_SHA256 = hashlib.sha256(
    json.dumps(
        _PROFILE_V2_DEFINITION,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()

_PROVENANCE_KEYS = {
    "authority",
    "transformations",
    "input_sha256",
    "output_sha256",
    "canonical_payload_sha256",
}
_AUTHORITY_KEYS = {"method", "source", "version"}
_TRANSFORMATION_KEYS = {"line_index", "line_offset", "from", "to", "reason"}
_SHA256_HEX = "0123456789abcdef"


def _is_han(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
        or 0x2CEB0 <= codepoint <= 0x2EBEF
        or 0x2EBF0 <= codepoint <= 0x2EE5F
        or 0x2F800 <= codepoint <= 0x2FA1F
        or 0x30000 <= codepoint <= 0x3134F
        or 0x31350 <= codepoint <= 0x323AF
    )


def _compact_han_spacing(text: str) -> str:
    if " " not in text:
        return text

    compacted: list[str] = []
    index = 0
    text_length = len(text)
    while index < text_length:
        if text[index] != " ":
            compacted.append(text[index])
            index += 1
            continue

        run_start = index
        while index < text_length and text[index] == " ":
            index += 1
        if run_start > 0 and index < text_length:
            if _is_han(text[run_start - 1]) and _is_han(text[index]):
                continue
        compacted.extend(text[run_start:index])
    return "".join(compacted)


def normalize_recognized_text_v1(text: str) -> str:
    """Normalize recognized text with the version-one deterministic profile."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = _normalize_han_variants_v1(normalized)
    normalized = (
        normalized.replace("廿", "二十").replace("卅", "三十").replace("卌", "四十")
    )
    return _compact_han_spacing(normalized)


def normalize_recognized_text_v2(text: str) -> str:
    """Normalize recognized text with the version-two deterministic profile."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = _normalize_han_variants_v2(normalized)
    normalized = (
        normalized.replace("廿", "二十").replace("卅", "三十").replace("卌", "四十")
    )
    return _compact_han_spacing(normalized)


def recognized_provenance_sha256(
    *,
    profile: str,
    authority: _Mapping[str, object],
    transformations: _Sequence[_Mapping[str, object]],
    input_sha256: str,
    output_sha256: str,
) -> str:
    """Hash the canonical recognized-text provenance payload."""

    payload = {
        "recognized_profile": profile,
        "authority": authority,
        "transformations": transformations,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_exact_keys(
    value: object, field: str, expected: set[str]
) -> _Mapping[str, object]:
    if not isinstance(value, _Mapping):
        raise ValueError(f"{field} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected, key=repr)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing!r}")
        if extra:
            details.append(f"unexpected {extra!r}")
        raise ValueError(f"{field} has invalid keys ({'; '.join(details)})")
    return value


def _require_sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase 64-hex SHA-256 string")
    return value


def _require_integer(value: object, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if (value <= 0) if positive else (value < 0):
        requirement = "positive" if positive else "nonnegative"
        raise ValueError(f"{field} must be a {requirement} integer")
    return value


def recognized_reference_text(gold: _Mapping[str, object]) -> str | None:
    """Validate recognized-text provenance and return its derived text."""

    if not isinstance(gold, _Mapping):
        raise ValueError("gold must be an object")

    recognized_fields = (
        "recognized_text",
        "recognized_profile",
        "recognized_provenance",
    )
    present = [field for field in recognized_fields if field in gold]
    if not present:
        return None
    if len(present) != len(recognized_fields):
        missing = [field for field in recognized_fields if field not in gold]
        raise ValueError(f"recognized fields are partial; missing {missing!r}")

    schema_version = gold.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 2
    ):
        raise ValueError("schema_version must be an integer >= 2")

    profile = gold["recognized_profile"]
    if not isinstance(profile, str) or profile != RECOGNIZED_TEXT_PROFILE_ID:
        raise ValueError(
            f"recognized_profile must equal {RECOGNIZED_TEXT_PROFILE_ID!r}"
        )

    text = gold.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    recognized_text = gold["recognized_text"]
    if not isinstance(recognized_text, str):
        raise ValueError("recognized_text must be a string")

    provenance = _require_exact_keys(
        gold["recognized_provenance"],
        "recognized_provenance",
        _PROVENANCE_KEYS,
    )
    authority = _require_exact_keys(
        provenance["authority"],
        "recognized_provenance.authority",
        _AUTHORITY_KEYS,
    )
    for field in ("method", "source"):
        value = authority[field]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"recognized_provenance.authority.{field} must be a nonempty string"
            )
    _require_integer(
        authority["version"],
        "recognized_provenance.authority.version",
        positive=True,
    )

    transformations_value = provenance["transformations"]
    if isinstance(transformations_value, (str, bytes, bytearray)) or not isinstance(
        transformations_value, _Sequence
    ):
        raise ValueError("recognized_provenance.transformations must be a sequence")
    transformations = list(transformations_value)
    lines = text.split("\n")
    previous_coordinate: tuple[int, int] | None = None
    previous_line_index: int | None = None
    previous_end = 0
    for index, transformation in enumerate(transformations):
        field = f"recognized_provenance.transformations[{index}]"
        transformation_mapping = _require_exact_keys(
            transformation,
            field,
            _TRANSFORMATION_KEYS,
        )
        line_index = _require_integer(
            transformation_mapping["line_index"],
            f"{field}.line_index",
        )
        line_offset = _require_integer(
            transformation_mapping["line_offset"],
            f"{field}.line_offset",
        )
        source = transformation_mapping["from"]
        if not isinstance(source, str) or not source:
            raise ValueError(f"{field}.from must be a nonempty string")
        replacement = transformation_mapping["to"]
        if not isinstance(replacement, str):
            raise ValueError(f"{field}.to must be a string")
        reason = transformation_mapping["reason"]
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"{field}.reason must be a nonempty string")

        coordinate = (line_index, line_offset)
        if previous_coordinate is not None and coordinate <= previous_coordinate:
            raise ValueError(
                f"{field}.line_index/line_offset must be in ascending order"
            )
        if line_index >= len(lines):
            raise ValueError(f"{field}.line_index is outside text lines")
        if line_index == previous_line_index and line_offset < previous_end:
            raise ValueError(f"{field}.from overlaps a prior transformation")
        source_line = lines[line_index]
        if source_line[line_offset : line_offset + len(source)] != source:
            raise ValueError(f"{field}.from does not match text at its coordinates")

        previous_coordinate = coordinate
        previous_line_index = line_index
        previous_end = line_offset + len(source)

    input_sha256 = _require_sha256(
        provenance["input_sha256"],
        "recognized_provenance.input_sha256",
    )
    output_sha256 = _require_sha256(
        provenance["output_sha256"],
        "recognized_provenance.output_sha256",
    )
    canonical_payload_sha256 = _require_sha256(
        provenance["canonical_payload_sha256"],
        "recognized_provenance.canonical_payload_sha256",
    )

    actual_input_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if input_sha256 != actual_input_sha256:
        raise ValueError("recognized_provenance.input_sha256 does not match text")

    expected_canonical_payload_sha256 = recognized_provenance_sha256(
        profile=profile,
        authority=authority,
        transformations=transformations,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
    )
    if canonical_payload_sha256 != expected_canonical_payload_sha256:
        raise ValueError(
            "recognized_provenance.canonical_payload_sha256 does not match payload"
        )

    updated_lines = lines[:]
    for transformation in reversed(transformations):
        line_index = transformation["line_index"]
        line_offset = transformation["line_offset"]
        source = transformation["from"]
        replacement = transformation["to"]
        updated_lines[line_index] = (
            updated_lines[line_index][:line_offset]
            + replacement
            + updated_lines[line_index][line_offset + len(source) :]
        )
    derived_text = "\n".join(updated_lines)
    if derived_text != recognized_text:
        raise ValueError("recognized_text does not equal transformed text")

    actual_output_sha256 = hashlib.sha256(recognized_text.encode("utf-8")).hexdigest()
    if output_sha256 != actual_output_sha256:
        raise ValueError(
            "recognized_provenance.output_sha256 does not match recognized_text"
        )
    return recognized_text
