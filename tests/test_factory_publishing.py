"""Reconstruction, book-model, EPUB, and hosted-library behavior."""

from __future__ import annotations

import sqlite3
import zipfile

import pytest

from palimpsest.factory import site as site_builder
from palimpsest.factory.core import registry
from palimpsest.factory.core.artifact import content_fingerprint, payload_fingerprint
from palimpsest.factory.core.conductor import Conductor
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
    assert book["title"] == "Test"
    assert book["chapters"][0]["heading"] == "Remedies"
    assert book["readers_note"] == "A small test codex of remedies."
    assert book["colophon"]["pages"] == 2
    assert book["colophon"]["transcribed_by"]
    assert book["colophon"]["referenced_by"]
    assert book["colophon"]["emended_by"]
    assert book["colophon"]["cost_usd_total"] is None
    assert book["colophon"]["cost_usd_known"] > 0
    assert not book["colophon"]["cost_complete"]
    assert {stage["station"] for stage in book["colophon"]["pipeline"]} >= {
        "reference",
        "emend",
    }
    assert book["chapters"][0]["source_pages"] == ["f001r", "f001v"]
    assert [page["page_id"] for page in book["evidence"]["pages"]] == [
        "f001r",
        "f001v",
    ]

    epub_path = artifact_path(DOC, "book_epub", None, library)
    assert epub_path.exists()
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        chapter = next(n for n in names if n.endswith("ch01.xhtml"))
        content = zf.read(chapter).decode("utf-8")
        assert "Remedies" in content
        assert "Translated body" in content
        assert "Experimenta" in content  # original included per chapter
        assert "Source evidence" in content
        assert "https://archive.test/f001r.jpg" in content


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
    first_page = book["evidence"]["pages"][0]

    assert assembled["original"]["text"] == "Experimenta 〔?〕 ad morbos"
    assert assembled["transcription_audit"] == audit
    assert first_page["diplomatic"] == "Experimenta 〔?〕 ad morbos"
    assert first_page["transcription_audit"] == audit


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
    assert "Translated body" in reader
    assert "Show original text" in reader
    assert (site_root / DOC / f"{DOC}.epub").exists()
    assert "source f001r" in reader
    assert (site_root / DOC / "book.json").exists()
    evidence_page = site_root / DOC / "evidence" / "f001r.html"
    assert evidence_page.exists()
    evidence = evidence_page.read_text(encoding="utf-8")
    assert "Archive image" in evidence
    assert "Experimenta ad morbos" in evidence
    assert (site_root / DOC / "evidence" / "f001r.jpg").exists()


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
    assert "alignment" not in book["evidence"]["pages"][0]

    payload["provenance"]["station_fingerprint"] = registry.get(
        "align"
    ).implementation_fingerprint
    atomic_write_json(alignment_path, payload)
    report = run_line(ledger, library)
    book = read_json(artifact_path(DOC, "book", None, library))
    first_page = book["evidence"]["pages"][0]

    assert ("publish", None) in {
        (cell.station, cell.page_id) for cell in report.cells if cell.action == "ran"
    }
    assert first_page["alignment"]["columns"][0]["chars"][0]["ch"] == "E"
    assert first_page["alignment"]["stats"] == {"transcribed": 1, "boxed": 1}
    assert first_page["alignment"]["provenance"]["station"] == "align"


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
    book["title"] = "Changed after EPUB rendering"
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
