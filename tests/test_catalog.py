"""Catalog contracts: source-local identity, normalization, and revisions."""

from __future__ import annotations

import json

import pytest

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.heads import build_head, parse_gallica_response
from palimpsest.catalog.sync import sync_source
from palimpsest.cli import build_parser


def _line(
    source_key: str,
    title: str,
    *,
    raw_title: str | None = None,
    source_modified_at: str | None = None,
) -> str:
    value = {
        "source_key": source_key,
        "source_url": f"https://archive.test/{source_key}",
        "record": {
            "record_type": "manuscript",
            "titles": [title],
            "repository": "Test Repository",
            "access": "open",
        },
        "raw": {"title": raw_title or title},
    }
    if source_modified_at is not None:
        value["source_modified_at"] = source_modified_at
    return json.dumps(value)


def _write(path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_sync_is_idempotent_versions_changes_and_tracks_tombstones(tmp_path):
    source_path = tmp_path / "records.jsonl"
    _write(source_path, _line("MS-1", "One"), _line("MS-2", "Two"))
    database = CatalogDB(tmp_path / "catalog.db")
    database.add_source(
        "archive-a",
        "normalized-jsonl",
        {"path": str(source_path), "page_size": 1},
    )

    first = sync_source(database, "archive-a")
    second = sync_source(database, "archive-a")

    assert (first.records_inserted, first.records_revised) == (2, 0)
    assert (second.records_unchanged, second.records_revised) == (2, 0)
    assert database.stats() == {
        "sources": 1,
        "records": 2,
        "active_records": 2,
        "tombstoned_records": 0,
        "revisions": 2,
    }

    _write(source_path, _line("MS-1", "One, revised"))
    third = sync_source(database, "archive-a")

    assert third.records_revised == 1
    assert third.records_tombstoned == 1
    assert database.stats()["revisions"] == 3
    records = {record["source_key"]: record for record in database.records("archive-a")}
    assert records["MS-1"]["record"]["titles"] == ["One, revised"]
    assert records["MS-2"]["tombstoned"] is True

    _write(source_path, _line("MS-1", "One, revised"), _line("MS-2", "Two"))
    fourth = sync_source(database, "archive-a")

    assert fourth.records_revived == 1
    assert fourth.records_revised == 0
    assert database.stats()["revisions"] == 3
    database.close()


def test_same_source_key_from_two_heads_remains_two_source_records(tmp_path):
    first_path = tmp_path / "a.jsonl"
    second_path = tmp_path / "b.jsonl"
    _write(first_path, _line("MS-1", "Archive A title"))
    _write(second_path, _line("MS-1", "Conflicting Archive B title"))

    with CatalogDB(tmp_path / "catalog.db") as database:
        database.add_source("archive-a", "normalized-jsonl", {"path": str(first_path)})
        database.add_source("archive-b", "normalized-jsonl", {"path": str(second_path)})
        sync_source(database, "archive-a")
        sync_source(database, "archive-b")

        first = database.records("archive-a")[0]
        second = database.records("archive-b")[0]
        assert first["record_id"] != second["record_id"]
        assert first["record"]["titles"] == ["Archive A title"]
        assert second["record"]["titles"] == ["Conflicting Archive B title"]


def test_source_modified_time_is_revision_provenance(tmp_path):
    source_path = tmp_path / "records.jsonl"
    _write(
        source_path,
        _line("MS-1", "One", source_modified_at="2026-01-01T00:00:00Z"),
    )
    with CatalogDB(tmp_path / "catalog.db") as database:
        database.add_source("archive-a", "normalized-jsonl", {"path": str(source_path)})
        sync_source(database, "archive-a")
        _write(
            source_path,
            _line("MS-1", "One", source_modified_at="2026-02-01T00:00:00Z"),
        )

        second = sync_source(database, "archive-a")

        assert second.records_revised == 1
        assert database.stats()["revisions"] == 2


def test_duplicate_source_key_fails_the_page_atomically(tmp_path):
    source_path = tmp_path / "records.jsonl"
    _write(source_path, _line("MS-1", "One"), _line("MS-1", "Conflicting one"))
    with CatalogDB(tmp_path / "catalog.db") as database:
        database.add_source("archive-a", "normalized-jsonl", {"path": str(source_path)})

        with pytest.raises(ValueError, match="duplicate source_key 'MS-1'"):
            sync_source(database, "archive-a")

        assert database.stats()["records"] == 0


def test_failed_jsonl_sync_resumes_after_last_committed_page(tmp_path):
    source_path = tmp_path / "records.jsonl"
    source_path.write_text(_line("MS-1", "One") + "\n{bad json\n", encoding="utf-8")

    with CatalogDB(tmp_path / "catalog.db") as database:
        database.add_source(
            "archive-a",
            "normalized-jsonl",
            {"path": str(source_path), "page_size": 1},
        )
        with pytest.raises(ValueError, match="line 2"):
            sync_source(database, "archive-a")

        _write(source_path, _line("MS-1", "One"), _line("MS-2", "Two"))
        resumed = sync_source(database, "archive-a", resume=True)

        assert resumed.sync_id == 1
        assert resumed.records_seen == 2
        assert resumed.records_inserted == 2
        assert database.stats()["active_records"] == 2


def test_gallica_sru_normalizes_dc_without_discarding_source_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <srw:numberOfRecords>2</srw:numberOfRecords>
      <srw:nextRecordPosition>2</srw:nextRecordPosition>
      <srw:records><srw:record>
        <srw:recordPosition>0</srw:recordPosition>
        <srw:recordData><oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/btv1b123</dc:identifier>
          <dc:title>Pelliot chinois 123 — Fragment administratif</dc:title>
          <dc:description>Recto and verso contain different texts.</dc:description>
          <dc:date>0801-0900</dc:date>
          <dc:language>chi</dc:language>
          <dc:type>manuscrit</dc:type>
          <dc:format>papier</dc:format>
          <dc:rights>domaine public</dc:rights>
        </oai_dc:dc></srw:recordData>
      </srw:record></srw:records>
    </srw:searchRetrieveResponse>"""

    page, total = parse_gallica_response(
        xml,
        repository="Bibliothèque nationale de France",
        collection="Pelliot chinois",
    )

    assert total == 2
    assert page.next_cursor == "2"
    record = page.records[0]
    assert record.source_key == "ark:/12148/btv1b123"
    assert record.normalized.record_type == "manuscript_fragment"
    assert record.normalized.languages == ("zh",)
    assert (record.normalized.date_start, record.normalized.date_end) == (801, 900)
    assert record.normalized.manifest_url.endswith(
        "/iiif/ark:/12148/btv1b123/manifest.json"
    )
    assert "record_data_xml" in record.raw


def test_gallica_sru_accepts_stable_non_ark_identifiers():
    xml = """<srw:searchRetrieveResponse
        xmlns:srw="http://www.loc.gov/zing/srw/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <srw:numberOfRecords>1</srw:numberOfRecords>
      <srw:records><srw:record><srw:recordData><oai_dc:dc>
        <dc:identifier>https://archive.test/record/1</dc:identifier>
        <dc:title>Federated catalog result</dc:title>
      </oai_dc:dc></srw:recordData></srw:record></srw:records>
    </srw:searchRetrieveResponse>"""

    page, _ = parse_gallica_response(
        xml,
        repository="Federated repository",
        collection="SRU results",
    )

    record = page.records[0]
    assert record.source_key == "https://archive.test/record/1"
    assert record.normalized.catalog_url == record.source_key
    assert record.normalized.manifest_url is None


def test_gallica_head_follows_sru_next_record_position(monkeypatch):
    def response(ark: str, next_cursor: str | None) -> str:
        next_element = (
            f"<srw:nextRecordPosition>{next_cursor}</srw:nextRecordPosition>"
            if next_cursor
            else ""
        )
        return f"""<srw:searchRetrieveResponse
            xmlns:srw="http://www.loc.gov/zing/srw/"
            xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/">
          <srw:numberOfRecords>2</srw:numberOfRecords>{next_element}
          <srw:records><srw:record>
            <srw:recordPosition>0</srw:recordPosition>
            <srw:recordData><oai_dc:dc>
              <dc:identifier>https://gallica.bnf.fr/{ark}</dc:identifier>
              <dc:title>{ark}</dc:title>
            </oai_dc:dc></srw:recordData>
          </srw:record></srw:records>
        </srw:searchRetrieveResponse>"""

    head = build_head(
        "gallica-sru",
        {"query": "test", "page_size": 1, "minimum_interval_seconds": 0},
    )
    requested: list[int] = []

    def request(start_record: int) -> str:
        requested.append(start_record)
        if start_record == 1:
            return response("ark:/12148/first", "2")
        return response("ark:/12148/second", None)

    monkeypatch.setattr(head, "_request", request)

    pages = list(head.pages())

    assert requested == [1, 2]
    assert [page.records[0].source_key for page in pages] == [
        "ark:/12148/first",
        "ark:/12148/second",
    ]


def test_catalog_is_a_top_level_cli_surface():
    parser = build_parser()
    choices = next(
        action.choices for action in parser._actions if getattr(action, "choices", None)
    )
    assert "catalog" in choices
