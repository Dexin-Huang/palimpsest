from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from palimpsest.factory.publication_contract import (
    Book,
    Library,
    PublishedFile,
    SchemaReference,
    schema_paths,
    validate_book_object,
    validate_library_object,
)

BOOK_FIXTURE = Path(__file__).with_name("fixtures") / "book.json"


def _book_fixture() -> dict:
    return json.loads(BOOK_FIXTURE.read_text(encoding="utf-8"))


def _schema_records() -> tuple[list[PublishedFile], dict[str, SchemaReference]]:
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
    return records, references


def test_book_schema_accepts_the_canonical_book_fixture() -> None:
    validate_book_object(_book_fixture())


def test_book_schema_rejects_an_incomplete_book_fixture() -> None:
    incomplete = deepcopy(_book_fixture())
    del incomplete["identity"]["title"]

    with pytest.raises(ValueError, match="invalid book object at identity"):
        validate_book_object(incomplete)


def test_book_schema_requires_catalog_record_id() -> None:
    book = _book_fixture()
    del book["catalog_record_id"]

    with pytest.raises(ValueError, match="invalid book object at <root>"):
        validate_book_object(book)


def test_book_schema_accepts_null_catalog_record_id() -> None:
    book = _book_fixture()
    book["catalog_record_id"] = None

    validate_book_object(book)


@pytest.mark.parametrize(
    "record_id",
    [
        "source-record:not-a-hex-digest",
        "source-record:" + "0" * 63,
        "catalog-record:" + "0" * 64,
        "source-record:" + "A" * 64,
        "",
        123,
    ],
)
def test_book_schema_rejects_malformed_catalog_record_id(record_id) -> None:
    book = _book_fixture()
    book["catalog_record_id"] = record_id

    with pytest.raises(ValueError, match="invalid book object at catalog_record_id"):
        validate_book_object(book)


def test_library_object_has_a_content_addressed_identity() -> None:
    records, references = _schema_records()
    library = Library.create(schemas=references, books=(), files=records)
    payload = library.to_payload()

    validate_library_object(payload)
    payload["contract_version"] = "2.0.1"
    with pytest.raises(ValueError, match="invalid library object"):
        validate_library_object(payload)


def test_library_contract_is_2_0_0() -> None:
    records, references = _schema_records()
    payload = Library.create(schemas=references, books=(), files=records).to_payload()

    assert payload["schema_version"] == 2
    assert payload["profile"] == "palimpsest-library"
    assert payload["contract"] == "palimpsest-publication"
    assert payload["contract_version"] == "2.0.0"


def test_library_embeds_the_canonical_schema_files() -> None:
    records, references = _schema_records()
    payload = Library.create(schemas=references, books=(), files=records).to_payload()

    assert payload["schemas"]["book"]["path"] == "contract/book-object.schema.json"
    assert payload["schemas"]["library"]["path"] == "contract/library-object.schema.json"
    for name in ("book", "library"):
        reference = payload["schemas"][name]
        record = next(
            item for item in payload["files"] if item["path"] == reference["path"]
        )
        assert record["sha256"] == reference["sha256"]
        assert record["bytes"] == schema_paths()[name].stat().st_size


def test_library_rejects_a_schema_reference_diverging_from_its_file() -> None:
    records, references = _schema_records()
    references = dict(references)
    references["book"] = SchemaReference(
        path=references["book"].path,
        sha256="0" * 64,
    )
    payload = Library.create(schemas=references, books=(), files=records).to_payload()

    with pytest.raises(ValueError, match="does not match its published file"):
        validate_library_object(payload)


def test_library_book_entries_are_pointer_free() -> None:
    book = _book_fixture()
    records, references = _schema_records()
    records.append(
        PublishedFile(path="books/fixture_ms/book.json", bytes=1, sha256="0" * 64)
    )
    books = (
        Book(
            doc_id=book["doc_id"],
            model="books/fixture_ms/book.json",
            epub="books/fixture_ms/fixture_ms.epub",
            folios=(),
        ),
    )
    payload = Library.create(schemas=references, books=books, files=records).to_payload()

    (entry,) = payload["books"]
    # The pointer lives in the Book model; LibraryObject entries stay pointer-free.
    assert set(entry) == {"doc_id", "model", "epub", "folios"}
