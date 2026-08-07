"""Export validated books as a renderer-independent publication bundle."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.core.artifact import content_fingerprint, read_provenance
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.artifact import fingerprint
from palimpsest.factory.publication_contract import (
    Book,
    Library,
    PublishedFile,
    PublishedFolio,
    SchemaReference,
    schema_paths,
    validate_book_object,
    validate_library_object,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path

DEFAULT_BUNDLE_ROOT = PROJECT_ROOT / "publication"


@dataclass(frozen=True)
class _FolioPlan:
    page_id: str
    original_path: Path
    enhanced_path: Path | None
    alignment: dict | None


@dataclass(frozen=True)
class _BookPlan:
    model: dict
    book_path: Path
    epub_path: Path
    folios: tuple[_FolioPlan, ...]


def export_library(
    library_root: Path = LIBRARY_ROOT,
    output_root: Path = DEFAULT_BUNDLE_ROOT,
) -> Library:
    """Export every valid published Book as one immutable Library object."""
    plans = [
        _plan_book(path, library_root)
        for path in sorted(library_root.glob("*/book/book.json"))
    ]

    staged_root = output_root.parent / f".{output_root.name}.staged"
    backup_root = output_root.parent / f".{output_root.name}.backup"
    shutil.rmtree(staged_root, ignore_errors=True)
    shutil.rmtree(backup_root, ignore_errors=True)
    staged_root.mkdir(parents=True)

    try:
        books = []
        files = []
        for plan in plans:
            book, book_files = _write_book(plan, staged_root)
            books.append(book)
            files.extend(book_files)

        contract_root = staged_root / "contract"
        contract_root.mkdir()
        schema_references = {}
        for name, source in schema_paths().items():
            target = contract_root / source.name
            shutil.copyfile(source, target)
            record = _file_record(target, staged_root)
            files.append(record)
            schema_references[name] = SchemaReference(
                path=record.path,
                sha256=record.sha256,
            )

        library = Library.create(
            schemas=schema_references,
            books=books,
            files=files,
        )
        payload = library.to_payload()
        validate_library_object(payload)
        atomic_write_json(staged_root / "library.json", payload)

        if output_root.exists():
            output_root.replace(backup_root)
        try:
            staged_root.replace(output_root)
        except BaseException:
            if backup_root.exists():
                backup_root.replace(output_root)
            raise
        shutil.rmtree(backup_root, ignore_errors=True)
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)

    return library


def load_book(book_path: Path) -> dict:
    model = read_json(book_path)
    validate_payload("book", model, expected_doc_id=book_path.parents[1].name)
    validate_book_object(model)
    return model


def epub_is_current(book_path: Path, epub_path: Path) -> bool:
    if not epub_path.is_file():
        return False
    stamp = read_provenance(epub_path)
    return bool(
        stamp
        and stamp.get("station") == "render_epub"
        and stamp.get("input_fingerprint")
        == fingerprint(content_fingerprint(book_path))
    )


def _plan_book(book_path: Path, library_root: Path) -> _BookPlan:
    model = load_book(book_path)
    doc_id = model["doc_id"]
    epub_path = artifact_path(doc_id, "book_epub", None, library_root)
    if not epub_is_current(book_path, epub_path):
        raise ValueError(f"book {doc_id!r} has no current EPUB")

    folios = []
    for folio in model["folios"]:
        page_id = _safe_component(folio["page_id"], "page_id")
        images = folio["images"]
        original_path = artifact_path(doc_id, images["original"]["kind"], page_id, library_root)
        if not original_path.is_file():
            raise ValueError(f"book {doc_id!r} is missing original image for {page_id}")
        enhanced_path = None
        if images.get("enhanced"):
            enhanced_path = artifact_path(
                doc_id,
                images["enhanced"]["kind"],
                page_id,
                library_root,
            )
            if not enhanced_path.is_file():
                raise ValueError(f"book {doc_id!r} is missing enhanced image for {page_id}")
        folios.append(
            _FolioPlan(
                page_id=page_id,
                original_path=original_path,
                enhanced_path=enhanced_path,
                alignment=folio["evidence"].get("alignment"),
            )
        )
    return _BookPlan(
        model=model,
        book_path=book_path,
        epub_path=epub_path,
        folios=tuple(folios),
    )


def _write_book(plan: _BookPlan, staged_root: Path) -> tuple[Book, list[PublishedFile]]:
    doc_id = plan.model["doc_id"]
    book_root = staged_root / "books" / doc_id
    evidence_root = book_root / "evidence"
    evidence_root.mkdir(parents=True)

    book_target = book_root / "book.json"
    epub_target = book_root / f"{doc_id}.epub"
    shutil.copyfile(plan.book_path, book_target)
    shutil.copyfile(plan.epub_path, epub_target)

    folio_records = []
    targets = [book_target, epub_target]
    for folio in plan.folios:
        original_target = (
            evidence_root
            / f"{folio.page_id}.original{folio.original_path.suffix.lower()}"
        )
        shutil.copyfile(folio.original_path, original_target)
        targets.append(original_target)

        enhanced_target = None
        if folio.enhanced_path is not None:
            enhanced_target = (
                evidence_root
                / f"{folio.page_id}.enhanced{folio.enhanced_path.suffix.lower()}"
            )
            shutil.copyfile(folio.enhanced_path, enhanced_target)
            targets.append(enhanced_target)

        alignment_target = None
        if folio.alignment:
            alignment_target = evidence_root / f"{folio.page_id}.alignment.json"
            atomic_write_json(
                alignment_target,
                {
                    "doc_id": doc_id,
                    "page_id": folio.page_id,
                    **folio.alignment,
                },
            )
            targets.append(alignment_target)

        folio_records.append(
            PublishedFolio(
                page_id=folio.page_id,
                original=_relative(original_target, staged_root),
                enhanced=(
                    _relative(enhanced_target, staged_root)
                    if enhanced_target is not None
                    else None
                ),
                alignment=(
                    _relative(alignment_target, staged_root)
                    if alignment_target is not None
                    else None
                ),
            )
        )

    return (
        Book(
            doc_id=doc_id,
            model=_relative(book_target, staged_root),
            epub=_relative(epub_target, staged_root),
            folios=tuple(folio_records),
        ),
        [_file_record(path, staged_root) for path in targets],
    )


def _safe_component(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{field} must be a non-empty path component")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError(f"{field} must be a single path component")
    return value


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_record(path: Path, root: Path) -> PublishedFile:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return PublishedFile(
        path=_relative(path, root),
        bytes=path.stat().st_size,
        sha256=digest.hexdigest(),
    )
