"""Contract graph tests: every station's I/O is known, payloads are enforced,
and the generated graph reflects the live registries."""

from __future__ import annotations

import pytest

from palimpsest.factory import graph
from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import CONTRACTS, SOURCE_KINDS, validate_payload
from palimpsest.factory.core.station import Station


def test_every_registered_station_references_known_kinds():
    for station in registry.all_stations():
        for kind in (*station.consumes, station.produces):
            assert kind in CONTRACTS or kind in SOURCE_KINDS, (
                f"{station.name} references unknown kind {kind}")


def test_registering_station_with_unknown_kind_fails():
    class Bogus(Station):
        name = "bogus"
        version = "bogus/v1"
        grain = "page"
        consumes = ("nonexistent_kind",)
        produces = "page_transcription"

    with pytest.raises(ValueError, match="unknown artifact kind"):
        registry.register(Bogus())


def test_validate_payload_enforces_required_fields():
    validate_payload("page_translation", {
        "doc_id": "d", "page_id": "p", "translation": "t", "flags": {}})
    with pytest.raises(ValueError, match="missing required fields.*translation"):
        validate_payload("page_translation", {"doc_id": "d", "page_id": "p"})


def test_validate_payload_rejects_binary_kinds():
    with pytest.raises(ValueError, match="not a JSON payload"):
        validate_payload("page_image", {})


def test_graph_reflects_live_registries():
    data = graph.build()
    station_names = {s["station"] for s in data["stations"]}
    assert {"acquire", "deframe", "dewatermark", "flatten", "segment", "read",
            "translate", "assemble_page", "survey", "reconstruct", "publish",
            "render_epub"} <= station_names
    assert {k["kind"] for k in data["kinds"]} == set(CONTRACTS)

    mermaid = graph.to_mermaid()
    assert "page_image_clean --> segment" in mermaid
    assert "read --> page_transcription" in mermaid
    assert "translation_brief --> translate" in mermaid


def test_contracts_doc_is_current(tmp_path):
    """docs/CONTRACTS.md must match the live registries — regenerate with
    `palimpsest factory graph --write-docs` after changing any station or
    contract."""
    regenerated = graph.write_docs(tmp_path / "CONTRACTS.md").read_text(encoding="utf-8")
    committed = graph.DEFAULT_DOC_PATH.read_text(encoding="utf-8")
    assert committed == regenerated, (
        "docs/CONTRACTS.md is stale — run: palimpsest factory graph --write-docs")


def test_json_kinds_used_by_stations_have_required_fields():
    json_produced = {
        s.produces for s in registry.all_stations()
        if CONTRACTS[s.produces].format == "json"
    }
    for kind in json_produced:
        assert CONTRACTS[kind].required, f"{kind} has an empty contract"
