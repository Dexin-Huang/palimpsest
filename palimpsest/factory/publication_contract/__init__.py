"""Versioned publication objects shared across the Palimpsest boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

LIBRARY_SCHEMA_VERSION = 2
LIBRARY_PROFILE = "palimpsest-library/v2"
CONTRACT_NAME = "palimpsest-publication"
CONTRACT_VERSION = "1.0.0"
LIBRARY_SCHEMA_NAME = "library-object-v1.schema.json"
BOOK_SCHEMA_NAME = "book-object-v1.schema.json"


@dataclass(frozen=True)
class PublishedFile:
    path: str
    bytes: int
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class SchemaReference:
    path: str
    sha256: str

    def to_payload(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class PublishedFolio:
    page_id: str
    original: str
    enhanced: str | None
    alignment: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "page_id": self.page_id,
            "original": self.original,
            "enhanced": self.enhanced,
            "alignment": self.alignment,
        }


@dataclass(frozen=True)
class Book:
    """One published Book object and its portable assets."""

    doc_id: str
    model: str
    epub: str
    folios: tuple[PublishedFolio, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "model": self.model,
            "epub": self.epub,
            "folios": [folio.to_payload() for folio in self.folios],
        }


@dataclass(frozen=True)
class Library:
    """One immutable Library object containing published Book objects."""

    bundle_id: str
    schemas: Mapping[str, SchemaReference]
    books: tuple[Book, ...]
    files: tuple[PublishedFile, ...]

    @classmethod
    def create(
        cls,
        *,
        schemas: Mapping[str, SchemaReference],
        books: Sequence[Book],
        files: Sequence[PublishedFile],
    ) -> Library:
        library = cls(
            bundle_id="",
            schemas=dict(schemas),
            books=tuple(sorted(books, key=lambda book: book.doc_id)),
            files=tuple(sorted(files, key=lambda record: record.path)),
        )
        return replace(library, bundle_id=_bundle_id(library.to_payload()))

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": LIBRARY_SCHEMA_VERSION,
            "profile": LIBRARY_PROFILE,
            "contract": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "bundle_id": self.bundle_id,
            "schemas": {
                name: reference.to_payload()
                for name, reference in sorted(self.schemas.items())
            },
            "books": [book.to_payload() for book in self.books],
            "files": [record.to_payload() for record in self.files],
        }


def schema_paths() -> Mapping[str, Path]:
    root = Path(__file__).parent
    return {
        "library": root / LIBRARY_SCHEMA_NAME,
        "book": root / BOOK_SCHEMA_NAME,
    }


def validate_book_object(payload: Mapping[str, Any]) -> None:
    _validate_with_schema("book", payload)


def validate_library_object(payload: Mapping[str, Any]) -> None:
    _validate_with_schema("library", payload)
    expected_bundle_id = _bundle_id(payload)
    if payload["bundle_id"] != expected_bundle_id:
        raise ValueError(
            f"library bundle_id must be {expected_bundle_id!r}, got {payload['bundle_id']!r}"
        )

    file_records = payload["files"]
    file_paths = [record["path"] for record in file_records]
    if len(file_paths) != len(set(file_paths)):
        raise ValueError("library contains duplicate file paths")
    for path in file_paths:
        _validate_relative_path(path)
    files_by_path = {record["path"]: record for record in file_records}

    doc_ids: set[str] = set()
    for book in payload["books"]:
        doc_id = book["doc_id"]
        if doc_id in doc_ids:
            raise ValueError(f"library contains duplicate Book object {doc_id!r}")
        doc_ids.add(doc_id)
        page_ids: set[str] = set()
        for path in (book["model"], book["epub"]):
            _require_published_file(path, files_by_path)
        for folio in book["folios"]:
            page_id = folio["page_id"]
            if page_id in page_ids:
                raise ValueError(f"Book object {doc_id!r} repeats folio {page_id!r}")
            page_ids.add(page_id)
            for path in (folio["original"], folio["enhanced"], folio["alignment"]):
                if path is not None:
                    _require_published_file(path, files_by_path)

    for name, reference in payload["schemas"].items():
        record = files_by_path.get(reference["path"])
        if record is None or record["sha256"] != reference["sha256"]:
            raise ValueError(f"library {name} schema does not match its published file")


@lru_cache(maxsize=2)
def _validator(name: str) -> Draft202012Validator:
    path = schema_paths()[name]
    schema = json.loads(path.read_text(encoding="utf-8"))
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise RuntimeError(f"invalid publication schema {path.name}: {error.message}") from error
    return Draft202012Validator(schema)


def _validate_with_schema(name: str, payload: Mapping[str, Any]) -> None:
    try:
        _validator(name).validate(payload)
    except ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(f"invalid {name} object at {location}: {error.message}") from error


def _bundle_id(payload: Mapping[str, Any]) -> str:
    """Content address of a library payload; any ``bundle_id`` field is excluded."""
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != "bundle_id"},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe publication path {value!r}")


def _require_published_file(path: str, files_by_path: Mapping[str, object]) -> None:
    _validate_relative_path(path)
    if path not in files_by_path:
        raise ValueError(f"unlisted publication file {path!r}")
