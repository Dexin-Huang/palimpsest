"""Reconstruction, book-model, EPUB, and hosted-library behavior."""

from __future__ import annotations

import sqlite3
import zipfile

import pytest

from palimpsest.factory import (
    publication_bundle,
    publication_contract,
    site as site_builder,
)
from palimpsest.factory.core import registry
from palimpsest.factory.core.artifact import content_fingerprint, payload_fingerprint
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.ledger import Ledger, fingerprint
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.stations.assemble_page import AssemblePage
from palimpsest.factory.stations.publish import Publish
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path

from tests import test_factory_line as line_cases

DOC = line_cases.DOC
fetch = line_cases.fetch
gateway = line_cases.gateway
library = line_cases.library


@pytest.fixture
def ledger(library):  # noqa: F811 — intentionally overrides the shared fixture
    with Ledger(library / "factory.db") as ledger:
        ledger.adopt(DOC, recipe="latin_manuscript")
        yield ledger


def run_line(ledger, library_root, **kw):
    return Conductor(ledger, library_root=library_root, workers=2, **kw).run(DOC)


def test_full_line_to_book(ledger, library, gateway, fetch):  # noqa: F811
    report = run_line(ledger, library)
    assert report.count("failed") == 0

    manuscript = read_json(artifact_path(DOC, "manuscript", None, library))
    (section,) = manuscript["sections"]
    # sentence_continuation joins with a space, deterministically in code
    assert section["translation"] == "Translated body Translated body"
    assert section["original"] == "Experimenta ad morbos Ad febres tertianas"

    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", library))
    assert "alignment" not in assembled

    book = read_json(artifact_path(DOC, "book", None, library))
    assert book["identity"]["title"] == "Test"
    assert book["identity"]["archive"] == "Test Archive"
    assert book["sections"][0]["heading"] == "Remedies"
    assert book["readers_note"] == "Final reader's note."
    assert book["colophon"]["pages"] == 2
    assert book["colophon"]["transcribed_by"]
    assert book["colophon"]["referenced_by"]
    assert book["colophon"]["emended_by"]
    assert book["colophon"]["finalized_by"]
    assert book["colophon"]["cost_usd_total"] is None
    assert book["colophon"]["cost_usd_known"] > 0
    assert not book["colophon"]["cost_complete"]
    assert {stage["station"] for stage in book["colophon"]["pipeline"]} >= {
        "reference",
        "emend",
    }
    assert book["sections"][0]["folio_ids"] == ["f001r", "f001v"]
    assert [folio["page_id"] for folio in book["folios"]] == [
        "f001r",
        "f001v",
    ]
    assert book["schema_version"] == 2
    assert book["profile"] == "facsimile-spread"
    assert book["catalog_record_id"] is None
    first_folio = book["folios"][0]
    assert first_folio["images"]["original"]["kind"] == "page_image"
    assert first_folio["images"]["enhanced"]["kind"] == "page_image_clean"
    assert (
        first_folio["evidence"]["translation"]["source"]["kind"] == "page_translation"
    )
    section_content = book["sections"][0]["content"]
    assert section_content["translation"]["source"]["kind"] == "edition"
    assert section_content["emended_reading"]["source"]["kind"] == "emendations"
    assert section_content["diplomatic_transcription"]["source"]["kind"] == "manuscript"

    epub_path = artifact_path(DOC, "book_epub", None, library)
    assert epub_path.exists()
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        chapter = next(n for n in names if n.endswith("section-0001.xhtml"))
        content = zf.read(chapter).decode("utf-8")
        assert "Remedies" in content
        assert "Final translation of" in content
        assert "Experimenta" in content  # original included per chapter
        assert "Source evidence" in content
        assert "https://archive.test/f001r.jpg" in content
        assert "coordinate-alignment coverage" not in content
        colophon = next(n for n in names if n.endswith("colophon.xhtml"))
        colophon_content = zf.read(colophon).decode("utf-8")
        assert "finalized by" in colophon_content
        assert "final-edition review" in colophon_content


def test_publish_maps_apparatus_to_final_reader_heading(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    edition_path = artifact_path(DOC, "edition", None, library)
    edition = read_json(edition_path)
    edition["sections"][0]["heading"] = "Final Remedies"
    atomic_write_json(edition_path, edition)
    job = Job(
        DOC,
        tuple(line_cases.PAGES),
        None,
        library,
        StationConfig(options={"original_language": "la"}),
    )

    book = Publish().run(job).payload

    assert book["sections"][0]["heading"] == "Final Remedies"
    assert book["apparatus"][0]["section_id"] == book["sections"][0]["id"]


def test_publish_copies_catalog_record_id_unchanged(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    metadata_file = artifact_path(DOC, "metadata", None, library)
    metadata = read_json(metadata_file)
    record_id = "source-record:" + "9" * 64
    metadata["catalog_record_id"] = record_id
    atomic_write_json(metadata_file, metadata)
    job = Job(
        DOC,
        tuple(line_cases.PAGES),
        None,
        library,
        StationConfig(options={"original_language": "la"}),
    )

    book = Publish().run(job).payload

    assert book["catalog_record_id"] == record_id
    validate_payload("book", book, expected_doc_id=DOC)


def test_publish_rejects_metadata_without_catalog_record_id(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    metadata_file = artifact_path(DOC, "metadata", None, library)
    metadata = read_json(metadata_file)
    del metadata["catalog_record_id"]
    atomic_write_json(metadata_file, metadata)
    job = Job(
        DOC,
        tuple(line_cases.PAGES),
        None,
        library,
        StationConfig(options={"original_language": "la"}),
    )

    with pytest.raises(KeyError, match="catalog_record_id"):
        Publish().run(job)


def test_publish_rejects_mismatched_editorial_section_counts(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    edition_path = artifact_path(DOC, "edition", None, library)
    edition = read_json(edition_path)
    edition["sections"].append(
        {
            "section_index": 1,
            "heading": "Unexpected section",
            "translation": "Unexpected translation",
        }
    )
    atomic_write_json(edition_path, edition)
    job = Job(
        DOC,
        tuple(line_cases.PAGES),
        None,
        library,
        StationConfig(options={"original_language": "la"}),
    )

    with pytest.raises(ValueError, match="editorial section counts do not match"):
        Publish().run(job)


def test_publish_labels_blank_folios_and_section_without_fabricating_sources(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    for page in line_cases.PAGES:
        page_id = page["page_id"]
        transcription_path = artifact_path(
            DOC, "page_transcription", page_id, library
        )
        transcription = read_json(transcription_path)
        transcription["text"] = ""
        transcription["route"] = "blank"
        atomic_write_json(transcription_path, transcription)
        translation_path = artifact_path(DOC, "page_translation", page_id, library)
        translation = read_json(translation_path)
        translation["translation"] = ""
        atomic_write_json(translation_path, translation)

    source_fields = {
        "manuscript": ("original", ""),
        "emendations": ("reading", "\n\n"),
        "edition": ("translation", ""),
    }
    for kind, (field, blank_text) in source_fields.items():
        path = artifact_path(DOC, kind, None, library)
        artifact = read_json(path)
        artifact["sections"][0][field] = blank_text
        atomic_write_json(path, artifact)

    job = Job(
        DOC,
        tuple(line_cases.PAGES),
        None,
        library,
        StationConfig(options={"original_language": "la"}),
    )
    book = Publish().run(job).payload

    for folio in book["folios"]:
        assert folio["evidence"]["diplomatic"]["text"] == "[Blank page]"
        assert folio["evidence"]["translation"]["text"] == "[Blank page]"
    content = book["sections"][0]["content"]
    assert {layer["text"] for layer in content.values()} == {"[Blank page]"}
    for kind, (field, blank_text) in source_fields.items():
        assert read_json(artifact_path(DOC, kind, None, library))["sections"][0][
            field
        ] == blank_text
    validate_payload("book", book, expected_doc_id=DOC)

    for page in line_cases.PAGES:
        transcription_path = artifact_path(
            DOC, "page_transcription", page["page_id"], library
        )
        transcription = read_json(transcription_path)
        transcription["route"] = "segmented"
        atomic_write_json(transcription_path, transcription)

    book = Publish().run(job).payload
    for folio in book["folios"]:
        assert folio["evidence"]["diplomatic"]["text"] == "[No transcribed text]"
        assert folio["evidence"]["translation"]["text"] == "[No transcribed text]"
    content = book["sections"][0]["content"]
    assert {layer["text"] for layer in content.values()} == {"[No transcribed text]"}
    validate_payload("book", book, expected_doc_id=DOC)


def test_failed_transcription_cannot_assemble_or_publish(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    transcription_path = artifact_path(DOC, "page_transcription", "f001r", library)
    transcription = read_json(transcription_path)
    transcription["adjudication_status"] = "failed"
    transcription["adjudication_error"] = "adjudicator unavailable"
    atomic_write_json(transcription_path, transcription)
    page = line_cases.PAGES[0]
    page_job = Job(DOC, tuple(line_cases.PAGES), page, library, StationConfig())
    manuscript_job = Job(DOC, tuple(line_cases.PAGES), None, library, StationConfig())

    with pytest.raises(
        ValueError,
        match=r"Cannot assemble page f001r: transcription adjudication failed: "
        r"adjudicator unavailable",
    ):
        AssemblePage().run(page_job)
    with pytest.raises(
        ValueError,
        match=r"Cannot publish page f001r: transcription adjudication failed: "
        r"adjudicator unavailable",
    ):
        Publish().run(manuscript_job)

    assert read_json(transcription_path) == transcription


def test_unresolved_transcription_publishes_with_full_audit(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    transcription_path = artifact_path(DOC, "page_transcription", "f001r", library)
    transcription = read_json(transcription_path)
    transcription["text"] = "Experimenta 〔?〕 ad morbos"
    audit = {
        "candidate_readings": [
            {
                "role": "primary",
                "requested_model": "anthropic/claude-fable-5",
                "model": "anthropic/claude-fable-5",
                "raw_text": "Experimenta 〔?〕 ad morbos",
                "text": "Experimenta 〔?〕 ad morbos",
            },
            {
                "role": "secondary",
                "requested_model": "openai/gpt-5.4",
                "model": "openai/gpt-5.4-2026-06-01",
                "raw_text": "Experimenta [?] ad morbos",
                "text": "Experimenta [?] ad morbos",
            },
        ],
        "adjudication_status": "adjudicated",
        "adjudication_requested_model": "anthropic/claude-fable-5",
        "adjudication_model": "anthropic/claude-fable-5",
        "adjudication_reasoning": "The damaged span remains illegible.",
        "unresolved": ["damaged span after Experimenta"],
        "adjudication_error": None,
    }
    transcription.update(audit)
    atomic_write_json(transcription_path, transcription)
    pages = tuple(line_cases.PAGES)

    assembled = (
        AssemblePage().run(Job(DOC, pages, pages[0], library, StationConfig())).payload
    )
    book = Publish().run(Job(DOC, pages, None, library, StationConfig())).payload
    first_page = book["folios"][0]

    assert assembled["original"]["text"] == "Experimenta 〔?〕 ad morbos"
    assert assembled["transcription_audit"] == audit
    assert first_page["evidence"]["diplomatic"]["text"] == (
        "Experimenta 〔?〕 ad morbos"
    )
    assert first_page["evidence"]["diplomatic"]["audit"] == audit


def test_full_line_second_run_fresh(ledger, library, gateway, fetch):  # noqa: F811
    run_line(ledger, library)
    report = run_line(ledger, library)
    assert report.count("ran") == 0
    assert report.count("failed") == 0


def test_site_build(ledger, library, gateway, fetch, tmp_path):  # noqa: F811
    run_line(ledger, library)
    site_root = tmp_path / "site"
    shelved = site_builder.build(library, site_root)
    assert shelved == [DOC]

    shelf = (site_root / "index.html").read_text(encoding="utf-8")
    assert "The Palimpsest Library" in shelf
    assert "Test" in shelf

    reader = (site_root / DOC / "index.html").read_text(encoding="utf-8")
    assert "Remedies" in reader
    assert "Final translation of" in reader
    assert "Show editorial layers" in reader
    assert (site_root / DOC / f"{DOC}.epub").exists()
    assert "source f001r" in reader
    assert "finalized by" in reader
    assert (site_root / DOC / "book.json").exists()
    evidence_page = site_root / DOC / "evidence" / "f001r.html"
    assert evidence_page.exists()
    evidence = evidence_page.read_text(encoding="utf-8")
    assert "Archive image" in evidence
    assert "Experimenta ad morbos" in evidence
    assert (site_root / DOC / "evidence" / "f001r.jpg").exists()


def test_publication_bundle_exports_renderer_independent_books(
    ledger, library, gateway, fetch, tmp_path
):  # noqa: F811
    run_line(ledger, library)
    bundle_root = tmp_path / "publication"

    exported = publication_bundle.export_library(library, bundle_root)

    assert [book.doc_id for book in exported.books] == [DOC]
    payload = read_json(bundle_root / "library.json")
    assert payload["schema_version"] == 2
    assert payload["profile"] == "palimpsest-library"
    assert payload["contract"] == "palimpsest-publication"
    assert payload["contract_version"] == "2.0.0"
    assert payload["bundle_id"] == exported.bundle_id
    assert [entry["doc_id"] for entry in payload["books"]] == [DOC]
    (record,) = payload["books"]
    assert record["model"] == f"books/{DOC}/book.json"
    assert record["epub"] == f"books/{DOC}/{DOC}.epub"
    assert [folio["page_id"] for folio in record["folios"]] == ["f001r", "f001v"]
    assert all(folio["original"] for folio in record["folios"])
    assert "catalog_record_id" not in record  # LibraryObject entries are pointer-free
    assert not list(bundle_root.rglob("*.html"))
    assert all((bundle_root / item["path"]).is_file() for item in payload["files"])
    assert set(payload["schemas"]) == {"book", "library"}
    assert payload["schemas"]["book"]["path"] == "contract/book-object.schema.json"
    assert (
        payload["schemas"]["library"]["path"]
        == "contract/library-object.schema.json"
    )
    for name in ("book", "library"):
        reference = payload["schemas"][name]
        file_record = next(
            item for item in payload["files"] if item["path"] == reference["path"]
        )
        assert file_record["sha256"] == reference["sha256"]
    exported_model = read_json(bundle_root / record["model"])
    assert exported_model["schema_version"] == 2
    assert exported_model["profile"] == "facsimile-spread"
    assert exported_model["catalog_record_id"] is None
    publication_bundle.validate_library_object(payload)


def test_publication_contract_schema_bytes_are_platform_stable():
    for path in publication_contract.schema_paths().values():
        body = path.read_bytes()
        assert b"\r\n" not in body
        assert body.endswith(b"\n")


def test_publication_bundle_preserves_previous_export_when_epub_is_stale(
    ledger, library, gateway, fetch, tmp_path
):  # noqa: F811
    run_line(ledger, library)
    bundle_root = tmp_path / "publication"
    bundle_root.mkdir()
    sentinel = bundle_root / "previous.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    book_path = artifact_path(DOC, "book", None, library)
    book = read_json(book_path)
    book["readers_note"] = "Changed after EPUB rendering."
    atomic_write_json(book_path, book)

    with pytest.raises(ValueError, match="has no current EPUB"):
        publication_bundle.export_library(library, bundle_root)

    assert sentinel.read_text(encoding="utf-8") == "preserved"
    assert not (bundle_root / "library.json").exists()


def test_shelf_excerpt_ends_at_a_word_boundary():
    model = {
        "doc_id": "long-note",
        "identity": {
            "title": "Long note",
            "shelfmark": None,
            "date": None,
        },
        "languages": {"original": "la"},
        "readers_note": "complete words " * 30,
    }

    shelf = site_builder._shelf_html([model])
    excerpt = shelf.rsplit("<p class='muted'>", 1)[1].split("</p>", 1)[0]

    assert len(excerpt) <= 220
    assert excerpt.endswith(("complete…", "words…"))


def test_site_rejects_invalid_book_before_writing(
    ledger, library, gateway, fetch, tmp_path
):  # noqa: F811
    run_line(ledger, library)
    book_path = artifact_path(DOC, "book", None, library)
    book = read_json(book_path)
    del book["profile"]
    atomic_write_json(book_path, book)
    site_root = tmp_path / "site"

    with pytest.raises(ValueError, match="missing required fields.*profile"):
        site_builder.build(library, site_root)

    assert not site_root.exists()


def test_publish_embeds_only_current_character_alignment(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    alignment_path = artifact_path(DOC, "page_alignment", "f001r", library)
    transcription_path = artifact_path(DOC, "page_transcription", "f001r", library)
    clean_image_path = artifact_path(DOC, "page_image_clean", "f001r", library)
    payload = {
        "doc_id": DOC,
        "page_id": "f001r",
        "columns": [
            {
                "bbox": [10, 20, 30, 40],
                "chars": [
                    {
                        "ch": "E",
                        "bbox": [10, 20, 3, 4],
                        "confidence": 0.95,
                        "method": "blob",
                    }
                ],
            }
        ],
        "stats": {"transcribed": 1, "boxed": 1},
    }
    input_fingerprint = fingerprint(
        content_fingerprint(clean_image_path),
        content_fingerprint(transcription_path),
    )
    payload["provenance"] = {
        "station": "align",
        "station_fingerprint": "stale-alignment",
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": payload_fingerprint(payload),
    }
    atomic_write_json(alignment_path, payload)

    run_line(ledger, library)
    book = read_json(artifact_path(DOC, "book", None, library))
    assert "alignment" not in book["folios"][0]["evidence"]

    payload["provenance"]["station_fingerprint"] = registry.get(
        "align"
    ).implementation_fingerprint
    atomic_write_json(alignment_path, payload)
    report = run_line(ledger, library)
    book = read_json(artifact_path(DOC, "book", None, library))
    first_page = book["folios"][0]

    assert ("publish", None) in {
        (cell.station, cell.page_id) for cell in report.cells if cell.action == "ran"
    }
    assert first_page["evidence"]["alignment"]["columns"][0]["chars"][0]["ch"] == "E"
    assert first_page["evidence"]["alignment"]["stats"] == {
        "transcribed": 1,
        "boxed": 1,
    }
    with zipfile.ZipFile(artifact_path(DOC, "book_epub", None, library)) as zf:
        chapter_name = next(
            name for name in zf.namelist() if name.endswith("section-0001.xhtml")
        )
        chapter = zf.read(chapter_name).decode("utf-8")
    assert "coordinate-alignment coverage is summarized below" in chapter
    assert "1 of 1 ink characters aligned" in chapter
    assert first_page["evidence"]["alignment"]["source"]["kind"] == "page_alignment"


def test_binary_artifact_recovers_after_ledger_interruption(
    ledger, library, gateway, fetch
):  # noqa: F811
    run_line(ledger, library)
    epub_path = artifact_path(DOC, "book_epub", None, library)
    before = epub_path.read_bytes()
    stamp = read_json(epub_path.with_suffix(".epub.provenance.json"))
    assert stamp["output_fingerprint"] == content_fingerprint(epub_path)

    with sqlite3.connect(library / "factory.db") as database:
        database.execute(
            "DELETE FROM stage_runs WHERE doc_id = ? AND station = 'render_epub'",
            (DOC,),
        )

    report = run_line(ledger, library)

    assert ("render_epub", None) in {
        (cell.station, cell.page_id)
        for cell in report.cells
        if cell.action == "recovered"
    }
    assert epub_path.read_bytes() == before


def test_site_omits_epub_when_book_has_changed(
    ledger, library, gateway, fetch, tmp_path
):  # noqa: F811
    run_line(ledger, library)
    book_path = artifact_path(DOC, "book", None, library)
    book = read_json(book_path)
    book["identity"]["title"] = "Changed after EPUB rendering"
    atomic_write_json(book_path, book)

    site_root = tmp_path / "site"
    site_builder.build(library, site_root)

    reader = (site_root / DOC / "index.html").read_text(encoding="utf-8")
    assert "Download EPUB" not in reader
    assert not (site_root / DOC / f"{DOC}.epub").exists()


def test_hyphenation_repair_assembly():
    from palimpsest.factory.stations.reconstruct import _assemble

    by_id = {
        "p1": {"original": {"text": "medica-"}, "translation": {"text": "some medi-"}},
        "p2": {"original": {"text": "menta"}, "translation": {"text": "cines"}},
    }
    joins = {("p1", "p2"): {"kind": "hyphenation_repair"}}
    assert _assemble(["p1", "p2"], by_id, joins, "original") == "medicamenta"
    assert _assemble(["p1", "p2"], by_id, joins, "translation") == "some medicines"


def test_paragraph_break_assembly():
    from palimpsest.factory.stations.reconstruct import _assemble

    by_id = {
        "p1": {"original": {"text": "Finis."}, "translation": {"text": "The end."}},
        "p2": {"original": {"text": "Incipit."}, "translation": {"text": "It begins."}},
    }
    assert _assemble(["p1", "p2"], by_id, {}, "translation") == "The end.\n\nIt begins."


def test_reconstruction_rejects_a_backward_section():
    from palimpsest.factory.stations.reconstruct import _section_span

    with pytest.raises(ValueError, match="runs backward"):
        _section_span(
            ["p1", "p2"],
            {"heading": "Bad span", "from_page": "p2", "to_page": "p1"},
        )


def test_reconstruction_rejects_a_nonadjacent_join():
    from palimpsest.factory.stations.reconstruct import _index_joins

    with pytest.raises(ValueError, match="does not connect adjacent pages"):
        _index_joins(
            ["p1", "p2", "p3"],
            [{"from_page": "p1", "to_page": "p3", "kind": "paragraph_break"}],
        )
