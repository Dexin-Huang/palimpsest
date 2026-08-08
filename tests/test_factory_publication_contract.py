from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from palimpsest.factory.publication_contract import (
    Library,
    PublishedFile,
    SchemaReference,
    schema_paths,
    validate_book_object,
    validate_library_object,
)

BOOK_FIXTURE = Path(__file__).with_name("fixtures") / "book-v1.json"


def test_book_schema_accepts_the_canonical_book_fixture() -> None:
    book = json.loads(BOOK_FIXTURE.read_text(encoding="utf-8"))

    validate_book_object(book)


def test_book_schema_rejects_an_incomplete_book_fixture() -> None:
    book = json.loads(BOOK_FIXTURE.read_text(encoding="utf-8"))
    incomplete = deepcopy(book)
    del incomplete["identity"]["title"]

    with pytest.raises(ValueError, match="invalid book object at identity"):
        validate_book_object(incomplete)


def test_library_object_has_a_content_addressed_identity() -> None:
    records = []
    references = {}
    for name, path in schema_paths().items():
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        published_path = f"contract/{path.name}"
        records.append(
            PublishedFile(path=published_path, bytes=len(body), sha256=digest)
        )
        references[name] = SchemaReference(path=published_path, sha256=digest)
    library = Library.create(schemas=references, books=(), files=records)
    payload = library.to_payload()

    validate_library_object(payload)
    payload["contract_version"] = "1.0.1"
    with pytest.raises(ValueError, match="invalid library object"):
        validate_library_object(payload)
