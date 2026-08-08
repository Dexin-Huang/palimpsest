"""Contract graph tests: every station's I/O is known, payloads are enforced,
and the generated graph reflects the live registries."""

from __future__ import annotations
import json
from pathlib import Path

import pytest

from palimpsest.factory import graph
from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import CONTRACTS, validate_payload
from palimpsest.factory.core.station import Station

BOOK_FIXTURE = Path(__file__).with_name("fixtures") / "book-v1.json"


def _book_v1() -> dict:
    return json.loads(BOOK_FIXTURE.read_text(encoding="utf-8"))


def test_every_registered_station_references_known_kinds():
    for station in registry.all_stations():
        for kind in (
            *station.consumes,
            *station.optional_consumes,
            station.produces,
        ):
            assert kind in CONTRACTS, f"{station.name} references unknown kind {kind}"


def test_registering_station_with_unknown_kind_fails():
    class Bogus(Station):
        name = "bogus"
        grain = "page"
        consumes = ("nonexistent_kind",)
        produces = "page_transcription"

    with pytest.raises(ValueError, match="unknown artifact kind"):
        registry.register(Bogus())


def test_registering_station_with_wrong_output_grain_fails():
    class WrongGrain(Station):
        name = "wrong_grain"
        grain = "page"
        consumes = ()
        produces = "book"

    with pytest.raises(ValueError, match="page-grain.*manuscript-grain"):
        registry.register(WrongGrain())


def test_source_kinds_and_station_outputs_have_one_producer():
    registry.get("translate")  # load the built-in registry before checking conflicts

    class DuplicateProducer(Station):
        name = "duplicate_translation"
        grain = "page"
        consumes = ()
        produces = "page_translation"

    with pytest.raises(ValueError, match="already has producer 'translate'"):
        registry.register(DuplicateProducer())

    class SourceProducer(Station):
        name = "source_producer"
        grain = "manuscript"
        consumes = ()
        produces = "page_list"

    with pytest.raises(ValueError, match="cannot produce source artifact"):
        registry.register(SourceProducer())


def test_validate_payload_enforces_required_fields():
    validate_payload(
        "page_translation",
        {"doc_id": "d", "page_id": "p", "translation": "t", "flags": {}},
    )
    with pytest.raises(ValueError, match="missing required fields.*translation"):
        validate_payload("page_translation", {"doc_id": "d", "page_id": "p"})


def test_page_transcription_contract_requires_dual_reader_audit():
    payload = {
        "doc_id": "d",
        "page_id": "p",
        "text": "",
        "route": "blank",
        "regions": [],
        "candidate_readings": [],
        "adjudication_status": "not_needed",
        "adjudication_requested_model": None,
        "adjudication_model": None,
        "adjudication_reasoning": "",
        "unresolved": [],
        "adjudication_error": None,
    }
    validate_payload("page_transcription", payload)

    for field in (
        "candidate_readings",
        "adjudication_status",
        "adjudication_requested_model",
        "adjudication_model",
        "adjudication_reasoning",
        "unresolved",
        "adjudication_error",
    ):
        incomplete = dict(payload)
        del incomplete[field]
        with pytest.raises(ValueError, match=f"missing required fields.*{field}"):
            validate_payload("page_transcription", incomplete)


def test_validate_payload_rejects_binary_kinds():
    with pytest.raises(ValueError, match="not a JSON payload"):
        validate_payload("page_image", {})


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([], "nonempty list"),
        ([{"page_id": "p1", "url": "", "order": 1}], "invalid url"),
        (
            [
                {"page_id": "p1", "url": "https://example.test/1.jpg", "order": 1},
                {"page_id": "p1", "url": "https://example.test/2.jpg", "order": 2},
            ],
            "duplicate page_id",
        ),
        (
            [{"page_id": "p1", "url": "https://example.test/1.jpg", "order": "1"}],
            "invalid order",
        ),
    ],
)
def test_page_list_contract_validates_page_members(pages, message):
    with pytest.raises(ValueError, match=message):
        validate_payload("page_list", {"doc_id": "doc", "pages": pages})


def test_page_list_contract_rejects_cross_document_reload():
    with pytest.raises(ValueError, match="does not match"):
        validate_payload(
            "page_list",
            {
                "doc_id": "other",
                "pages": [
                    {
                        "page_id": "p1",
                        "url": "https://example.test/1.jpg",
                        "order": 1,
                    }
                ],
            },
            expected_doc_id="doc",
        )


def test_book_v1_contract_accepts_the_canonical_fixture():
    validate_payload("book", _book_v1(), expected_doc_id="fixture_ms")


def test_book_v1_contract_accepts_deletion_apparatus():
    book = _book_v1()
    book["sections"][0]["apparatus_ids"] = ["apparatus-0001"]
    book["apparatus"] = [
        {
            "id": "apparatus-0001",
            "section_id": "section-0001",
            "original": "layout marker",
            "emended": "",
            "reason": "The mark is not manuscript text.",
            "evidence": "structure",
        }
    ]

    validate_payload("book", book)


def test_book_v1_contract_rejects_legacy_top_level_shape():
    legacy = _book_v1()
    del legacy["schema_version"]

    with pytest.raises(ValueError, match="missing required fields.*schema_version"):
        validate_payload("book", legacy)


def test_book_v1_contract_rejects_dangling_folio_reference():
    book = _book_v1()
    book["sections"][0]["folio_ids"] = ["missing-folio"]

    with pytest.raises(ValueError, match="cites unknown folio"):
        validate_payload("book", book)


def test_book_v1_contract_rejects_wrong_content_source_kind():
    book = _book_v1()
    book["sections"][0]["content"]["translation"]["source"]["kind"] = "manuscript"

    with pytest.raises(ValueError, match="translation source kind must be 'edition'"):
        validate_payload("book", book)


def test_book_v1_contract_rejects_uncited_apparatus():
    book = _book_v1()
    book["apparatus"] = [
        {
            "id": "apparatus-0001",
            "section_id": "section-0001",
            "original": "a",
            "emended": "b",
            "reason": "evidence",
            "evidence": "",
        }
    ]

    with pytest.raises(ValueError, match="apparatus_ids do not match"):
        validate_payload("book", book)


def test_graph_reflects_live_registries():
    data = graph.build()
    station_names = {s["station"] for s in data["stations"]}
    assert {
        "acquire",
        "deframe",
        "dewatermark",
        "flatten",
        "segment",
        "read",
        "translate",
        "assemble_page",
        "survey",
        "reconstruct",
        "publish",
        "render_epub",
    } <= station_names
    assert {k["kind"] for k in data["kinds"]} == set(CONTRACTS)

    mermaid = graph.to_mermaid()
    assert "kind_page_image_clean --> station_segment" in mermaid
    assert "station_read --> kind_page_transcription" in mermaid
    assert "kind_translation_brief --> station_translate" in mermaid
    assert "kind_reference --> station_emend" in mermaid
    assert "station_reference --> kind_reference" in mermaid


def test_contracts_doc_is_current(tmp_path):
    """docs/CONTRACTS.md must match the live registries — regenerate with
    `palimpsest graph --write-docs` after changing any station or contract."""
    regenerated = graph.write_docs(tmp_path / "CONTRACTS.md").read_text(
        encoding="utf-8"
    )
    committed = graph.DEFAULT_DOC_PATH.read_text(encoding="utf-8")
    assert committed == regenerated, (
        "docs/CONTRACTS.md is stale — run: palimpsest graph --write-docs"
    )


def test_json_kinds_used_by_stations_have_required_fields():
    json_produced = {
        s.produces
        for s in registry.all_stations()
        if CONTRACTS[s.produces].format == "json"
    }
    for kind in json_produced:
        assert CONTRACTS[kind].required, f"{kind} has an empty contract"
