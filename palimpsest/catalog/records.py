"""Canonical source-record shapes at the catalog ingestion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


RECORD_TYPES = frozenset(
    {
        "manuscript",
        "manuscript_fragment",
        "scroll",
        "codex",
        "leaf",
        "document",
        "collection_unit",
        "printed_book",
        "rubbing",
        "map",
        "image",
        "unknown",
    }
)
ACCESS_STATES = frozenset({"open", "restricted", "unknown"})
_NORMALIZED_KEYS = frozenset(
    {
        "record_type",
        "titles",
        "descriptions",
        "subjects",
        "languages",
        "scripts",
        "contributors",
        "identifiers",
        "relations",
        "rights",
        "repository",
        "collection",
        "catalog_url",
        "manifest_url",
        "access",
        "date_label",
        "date_start",
        "date_end",
        "material",
        "extent",
    }
)
_LIST_KEYS = frozenset(
    {
        "titles",
        "descriptions",
        "subjects",
        "languages",
        "scripts",
        "contributors",
        "identifiers",
        "relations",
        "rights",
    }
)
_STRING_KEYS = frozenset(
    {
        "repository",
        "collection",
        "catalog_url",
        "manifest_url",
        "date_label",
        "material",
    }
)


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"normalized field {field!r} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"normalized field {field!r} contains an empty value")
        clean = item.strip()
        if clean not in result:
            result.append(clean)
    return tuple(result)


@dataclass(frozen=True)
class NormalizedRecord:
    """Source claims in one queryable shape; never an object-identity assertion."""

    record_type: str = "unknown"
    titles: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    contributors: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()
    rights: tuple[str, ...] = ()
    repository: str | None = None
    collection: str | None = None
    catalog_url: str | None = None
    manifest_url: str | None = None
    access: str = "unknown"
    date_label: str | None = None
    date_start: int | None = None
    date_end: int | None = None
    material: str | None = None
    extent: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NormalizedRecord":
        if not isinstance(value, Mapping):
            raise ValueError("normalized record must be a JSON object")
        unknown = sorted(set(value) - _NORMALIZED_KEYS)
        if unknown:
            raise ValueError(f"unknown normalized fields: {', '.join(unknown)}")

        record_type = value.get("record_type", "unknown")
        if record_type not in RECORD_TYPES:
            raise ValueError(f"unknown record_type {record_type!r}")
        access = value.get("access", "unknown")
        if access not in ACCESS_STATES:
            raise ValueError(f"unknown access state {access!r}")

        strings: dict[str, str | None] = {}
        for field in _STRING_KEYS:
            item = value.get(field)
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ValueError(
                    f"normalized field {field!r} must be a non-empty string"
                )
            strings[field] = item.strip() if isinstance(item, str) else None

        dates: dict[str, int | None] = {}
        for field in ("date_start", "date_end"):
            item = value.get(field)
            if item is not None and (
                not isinstance(item, int) or isinstance(item, bool)
            ):
                raise ValueError(f"normalized field {field!r} must be an integer")
            dates[field] = item
        if (
            dates["date_start"] is not None
            and dates["date_end"] is not None
            and dates["date_start"] > dates["date_end"]
        ):
            raise ValueError("date_start must not be later than date_end")

        extent = value.get("extent")
        if extent is not None and not isinstance(extent, Mapping):
            raise ValueError("normalized field 'extent' must be an object")

        return cls(
            record_type=record_type,
            **{field: _strings(value.get(field), field) for field in _LIST_KEYS},
            **strings,
            access=access,
            **dates,
            extent=dict(extent) if extent is not None else None,
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "record_type": self.record_type,
            "access": self.access,
        }
        for field in sorted(_LIST_KEYS):
            values = getattr(self, field)
            if values:
                result[field] = list(values)
        for field in sorted(_STRING_KEYS):
            value = getattr(self, field)
            if value is not None:
                result[field] = value
        if self.date_start is not None:
            result["date_start"] = self.date_start
        if self.date_end is not None:
            result["date_end"] = self.date_end
        if self.extent is not None:
            result["extent"] = dict(self.extent)
        return result


@dataclass(frozen=True)
class SourceRecord:
    """One source-local record before persistence."""

    source_key: str
    normalized: NormalizedRecord
    raw: Any
    source_url: str | None = None
    source_modified_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_key, str) or not self.source_key.strip():
            raise ValueError("source_key must be a non-empty string")
        if self.source_url is not None and (
            not isinstance(self.source_url, str) or not self.source_url.strip()
        ):
            raise ValueError("source_url must be a non-empty string when provided")
        if self.source_modified_at is not None and (
            not isinstance(self.source_modified_at, str)
            or not self.source_modified_at.strip()
        ):
            raise ValueError(
                "source_modified_at must be a non-empty string when provided"
            )


@dataclass(frozen=True)
class RecordPage:
    records: tuple[SourceRecord, ...]
    next_cursor: str | None
