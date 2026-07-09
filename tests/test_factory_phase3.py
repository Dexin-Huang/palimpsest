"""Phase 3 tests: reconstruct assembly, book model, EPUB, hosted library."""

from __future__ import annotations

import json
import zipfile

import pytest

from palimpsest.factory import site as site_builder
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.gateway.client import ModelResponse
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path

from tests.test_factory_phase2 import (  # noqa: F401
    DOC,
    fetch,
    gateway,
    library,
)


@pytest.fixture
def ledger(library):  # noqa: F811 — shadows phase2's fixture on purpose
    with Ledger(library / "factory.db") as ledger:
        ledger.adopt(DOC, recipe="latin_manuscript")
        yield ledger


def run_line(ledger, library, **kw):
    return Conductor(ledger, library_root=library, workers=2, **kw).run(DOC)


def test_full_line_to_book(ledger, library, gateway, fetch):  # noqa: F811
    report = run_line(ledger, library)
    assert report.count("failed") == 0

    manuscript = read_json(artifact_path(DOC, "manuscript", None, library))
    (section,) = manuscript["sections"]
    # sentence_continuation joins with a space, deterministically in code
    assert section["translation"] == "Translated body Translated body"
    assert section["original"] == "Experimenta ad morbos Ad febres tertianas"

    book = read_json(artifact_path(DOC, "book", None, library))
    assert book["title"] == "Test"
    assert book["chapters"][0]["heading"] == "Remedies"
    assert book["readers_note"] == "A small test codex of remedies."
    assert book["colophon"]["pages"] == 2
    assert book["colophon"]["transcribed_by"]  # model recorded from stamps
    assert book["colophon"]["cost_usd_total"] > 0

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
