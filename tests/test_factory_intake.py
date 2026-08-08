"""Cutover contracts: IIIF intake, top-level CLI, and strict recipes."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from palimpsest.catalog.database import CATALOG_DB_PATH, CatalogDB
from palimpsest.catalog.sync import sync_source
from palimpsest.cli import build_parser
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.intake import build_records, fetch_manifest, write_records
from palimpsest.factory.workspace.io import read_json

import pytest


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_loc_cloudflare_block_falls_back_to_public_catalog_json(monkeypatch):
    calls = []
    record = {
        "item": {
            "title": "He gong shu : yi juan",
            "shelf_id": "http://lccn.loc.gov/2012402413",
            "contributor_names": [
                "Lü, Kun, 1536-1618",
                "Chinese Rare Book Collection (Library of Congress)",
            ],
            "date": "1605",
            "language": ["chinese"],
            "summary": ["A manual for regulating rivers."],
        },
        "resources": [
            {
                "url": "https://www.loc.gov/resource/book",
                "files": [
                    [
                        {
                            "mimetype": "image/jpeg",
                            "url": "https://tile.loc.gov/page-1-small.jpg",
                            "width": 400,
                            "height": 500,
                        },
                        {
                            "mimetype": "image/jpeg",
                            "url": "https://tile.loc.gov/page-1-full.jpg",
                            "width": 1600,
                            "height": 2000,
                        },
                    ],
                    [
                        {
                            "mimetype": "image/jpeg",
                            "url": "https://tile.loc.gov/page-2-full.jpg",
                            "width": 1700,
                            "height": 2100,
                        }
                    ],
                ],
            }
        ],
    }

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return FakeResponse(403, None)
        return FakeResponse(200, record)

    monkeypatch.setattr("palimpsest.factory.intake.requests.get", get)
    manifest_url = "https://www.loc.gov/item/2012402413/manifest.json"
    manifest = fetch_manifest(manifest_url)
    metadata, page_list = build_records("he_gong_shu_1605", manifest_url, manifest)

    assert [call[0] for call in calls] == [
        manifest_url,
        "https://www.loc.gov/item/2012402413/?fo=json",
    ]
    assert metadata["source_catalog"] == {
        "label": "He gong shu : yi juan",
        "title": "He gong shu : yi juan",
        "shelfmark": "http://lccn.loc.gov/2012402413",
        "archive": "Library of Congress",
        "author": "Lü, Kun, 1536-1618",
        "date": "1605",
        "language": "chinese",
        "description": "A manual for regulating rivers.",
        "canvas_count": 2,
        "metadata_entries": [
            {"label": "Archive", "value": "Library of Congress"},
            {"label": "Title", "value": "He gong shu : yi juan"},
            {
                "label": "Shelfmark",
                "value": "http://lccn.loc.gov/2012402413",
            },
            {"label": "Creator", "value": "Lü, Kun, 1536-1618"},
            {"label": "Date", "value": "1605"},
            {"label": "Language", "value": "chinese"},
        ],
    }
    assert [page["url"] for page in page_list["pages"]] == [
        "https://tile.loc.gov/page-1-full.jpg",
        "https://tile.loc.gov/page-2-full.jpg",
    ]


def test_non_loc_manifest_error_does_not_use_catalog_fallback(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(403, None)

    monkeypatch.setattr("palimpsest.factory.intake.requests.get", get)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        fetch_manifest("https://archive.test/manifest.json")

    assert len(calls) == 1


def test_v2_manifest_becomes_source_contracts(tmp_path):
    manifest = {
        "label": "Test Codex",
        "metadata": [
            {"label": "Shelfmark", "value": "MS 1"},
            {"label": "Language", "value": "Latin"},
        ],
        "sequences": [
            {
                "canvases": [
                    {
                        "@id": "canvas-1",
                        "label": "1r",
                        "width": 1200,
                        "height": 1600,
                        "images": [
                            {
                                "resource": {
                                    "service": {"@id": "https://archive.test/iiif/1"}
                                }
                            }
                        ],
                    }
                ]
            }
        ],
    }

    metadata, page_list = build_records(
        "test_codex", "https://archive.test/manifest", manifest
    )
    root = write_records("test_codex", metadata, page_list, library_root=tmp_path)

    assert root == tmp_path / "test_codex"
    assert read_json(root / "metadata.json")["source_catalog"]["shelfmark"] == "MS 1"
    assert read_json(root / "page_list.json")["pages"] == [
        {
            "page_id": "f001r",
            "canvas_id": "canvas-1",
            "url": "https://archive.test/iiif/1/full/max/0/default.jpg",
            "order": 1,
            "width": 1200,
            "height": 1600,
            "label": "1r",
        }
    ]


def test_v2_nested_metadata_values_are_normalized():
    manifest = {
        "label": {"@value": "Nested metadata"},
        "metadata": [
            {"label": "Date", "value": [{"@value": "0801-0900"}]},
            {"label": "Language", "value": [{"@value": "Chinese"}]},
        ],
        "sequences": [
            {
                "canvases": [
                    {"images": [{"resource": {"@id": "https://archive.test/page.jpg"}}]}
                ]
            }
        ],
    }

    metadata, _ = build_records("test_codex", "https://archive.test/manifest", manifest)

    assert metadata["source_catalog"]["label"] == "Nested metadata"
    assert metadata["source_catalog"]["date"] == "0801-0900"
    assert metadata["source_catalog"]["language"] == "Chinese"


def test_v3_manifest_uses_image_body_when_service_is_absent():
    manifest = {
        "type": "Manifest",
        "items": [
            {
                "id": "canvas-2",
                "type": "Canvas",
                "label": {"en": ["Page 2"]},
                "items": [
                    {
                        "items": [
                            {
                                "body": {
                                    "id": "https://archive.test/page-2.jpg",
                                    "type": "Image",
                                    "width": 800,
                                    "height": 1000,
                                }
                            }
                        ]
                    }
                ],
            }
        ],
    }

    _, page_list = build_records(
        "test_codex", "https://archive.test/manifest", manifest
    )

    assert page_list["pages"][0]["canvas_id"] == "canvas-2"
    assert page_list["pages"][0]["url"] == "https://archive.test/page-2.jpg"


def test_info_json_service_builds_image_request_and_preserves_query():
    manifest = {
        "type": "Manifest",
        "items": [
            {
                "id": "canvas-3",
                "type": "Canvas",
                "items": [
                    {
                        "items": [
                            {
                                "body": {
                                    "type": "Image",
                                    "service": [
                                        {
                                            "id": "https://archive.test/iiif/3/info.json?token=x"
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                ],
            }
        ],
    }

    _, page_list = build_records(
        "test_codex",
        "https://archive.test/manifest",
        manifest,
        image_size=1200,
    )

    assert (
        page_list["pages"][0]["url"]
        == "https://archive.test/iiif/3/full/1200,/0/default.jpg?token=x"
    )


def test_write_records_rejects_cross_document_payloads(tmp_path):
    manifest = {
        "sequences": [
            {
                "canvases": [
                    {
                        "images": [
                            {
                                "resource": {
                                    "@id": "https://archive.test/page.jpg",
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    metadata, page_list = build_records(
        "test_codex", "https://archive.test/manifest", manifest
    )
    page_list["doc_id"] = "other_codex"

    with pytest.raises(ValueError, match="page_list doc_id"):
        write_records("test_codex", metadata, page_list, library_root=tmp_path)
    assert not (tmp_path / "test_codex").exists()


def test_invalid_doc_id_is_rejected_before_manifest_parsing():
    with pytest.raises(ValueError, match="lowercase ASCII"):
        build_records("Bad/Id", "https://archive.test/manifest", {})


def test_atomic_write_failure_leaves_no_partial_source_record(tmp_path, monkeypatch):
    manifest = {
        "items": [
            {
                "type": "Canvas",
                "items": [
                    {"items": [{"body": {"id": "https://archive.test/page.jpg"}}]}
                ],
            }
        ]
    }
    metadata, page_list = build_records(
        "test_codex", "https://archive.test/manifest", manifest
    )

    def fail_replace(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr("palimpsest.factory.workspace.io.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        write_records("test_codex", metadata, page_list, library_root=tmp_path)

    doc_root = tmp_path / "test_codex"
    assert doc_root.exists()
    assert list(doc_root.iterdir()) == []


def test_recipe_rejects_unknown_station_options(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        """name: bad
language: la
line:
  - station: acquire
    misspelled_option: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown recipe keys.*misspelled_option"):
        load_recipe("bad", recipes_dir=tmp_path)


def test_factory_commands_are_the_only_top_level_surface():
    parser = build_parser()
    choices = next(
        action.choices for action in parser._actions if getattr(action, "choices", None)
    )

    assert {"intake", "adopt", "run", "status", "graph", "site"} <= set(choices)
    assert "factory" not in choices
    assert {"discovery", "library", "transcribe"}.isdisjoint(choices)


def test_run_parser_accepts_repeatable_pages_and_station_boundary():
    args = build_parser().parse_args(
        [
            "run",
            "--doc-id",
            "test_codex",
            "--page",
            "f001r",
            "--page",
            "f004v",
            "--through",
            "read",
        ]
    )

    assert args.page == ["f001r", "f004v"]
    assert args.through == "read"


_MANIFEST = {
    "type": "Manifest",
    "items": [
        {
            "id": "canvas-1",
            "type": "Canvas",
            "label": {"en": ["1r"]},
            "items": [
                {
                    "items": [
                        {
                            "body": {
                                "id": "https://archive.test/page-1.jpg",
                                "type": "Image",
                            }
                        }
                    ]
                }
            ],
        }
    ],
}


def _catalog_line(source_key, *, manifest_url=None, title="Catalog manuscript"):
    record = {
        "record_type": "manuscript",
        "titles": [title],
        "repository": "Test Repository",
        "access": "open",
    }
    if manifest_url is not None:
        record["manifest_url"] = manifest_url
    return json.dumps(
        {
            "source_key": source_key,
            "source_url": f"https://archive.test/{source_key}",
            "record": record,
            "raw": {"title": title},
        }
    )


class _ManifestHandler(BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, _format, *_args):
        pass


@contextmanager
def _manifest_server(manifest):
    _ManifestHandler.payload = json.dumps(manifest).encode("utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ManifestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/manifest.json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _seed_catalog(catalog_path, source_path, *lines) -> None:
    source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with CatalogDB(catalog_path) as catalog:
        catalog.add_source(
            "archive-test", "normalized-jsonl", {"path": str(source_path)}
        )
        sync_source(catalog, "archive-test")


def _intake_args(tmp_path, *, source_flag, source_value):
    return build_parser().parse_args(
        [
            "intake",
            "--db",
            str(tmp_path / "factory.db"),
            "--library-root",
            str(tmp_path),
            "--doc-id",
            "catalog_doc",
            "--recipe",
            "latin_manuscript",
            "--catalog-db",
            str(tmp_path / "catalog.db"),
            source_flag,
            source_value,
        ]
    )


def test_intake_source_selectors_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["intake", "--doc-id", "test_codex", "--recipe", "latin_manuscript"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "intake",
                "--doc-id",
                "test_codex",
                "--recipe",
                "latin_manuscript",
                "--manifest",
                "https://archive.test/manifest",
                "--catalog-record-id",
                "source-record:" + "ab" * 32,
            ]
        )
    direct = parser.parse_args(
        [
            "intake",
            "--doc-id",
            "test_codex",
            "--recipe",
            "latin_manuscript",
            "--manifest",
            "https://archive.test/manifest",
        ]
    )
    assert direct.manifest == "https://archive.test/manifest"
    assert direct.catalog_record_id is None
    catalog = parser.parse_args(
        [
            "intake",
            "--doc-id",
            "test_codex",
            "--recipe",
            "latin_manuscript",
            "--catalog-record-id",
            "source-record:" + "ab" * 32,
        ]
    )
    assert catalog.catalog_record_id == "source-record:" + "ab" * 32
    assert catalog.manifest is None


def test_intake_catalog_db_defaults_to_shared_catalog_path():
    args = build_parser().parse_args(
        [
            "intake",
            "--doc-id",
            "test_codex",
            "--recipe",
            "latin_manuscript",
            "--manifest",
            "https://archive.test/manifest",
        ]
    )
    assert args.catalog_db == CATALOG_DB_PATH


def test_direct_manifest_intake_writes_null_catalog_record_id():
    metadata, _ = build_records(
        "test_codex", "https://archive.test/manifest", _MANIFEST
    )
    assert metadata["catalog_record_id"] is None


def test_catalog_record_id_is_copied_unchanged_into_metadata():
    record_id = "source-record:" + "ab" * 32
    metadata, _ = build_records(
        "test_codex",
        "https://archive.test/manifest",
        _MANIFEST,
        catalog_record_id=record_id,
    )
    assert metadata["catalog_record_id"] == record_id


def test_build_records_rejects_malformed_catalog_record_id():
    with pytest.raises(ValueError, match="catalog_record_id"):
        build_records(
            "test_codex",
            "https://archive.test/manifest",
            _MANIFEST,
            catalog_record_id="not-a-record-id",
        )
    with pytest.raises(ValueError, match="catalog_record_id"):
        build_records(
            "test_codex",
            "https://archive.test/manifest",
            _MANIFEST,
            catalog_record_id="source-record:" + "ab" * 31,
        )
    with pytest.raises(ValueError, match="catalog_record_id"):
        build_records(
            "test_codex",
            "https://archive.test/manifest",
            _MANIFEST,
            catalog_record_id="source-record:ZZ" + "ab" * 31,
        )


def test_catalog_intake_derives_manifest_url_and_writes_exact_record_id(
    tmp_path, capsys
):
    with _manifest_server(_MANIFEST) as manifest_url:
        catalog_path = tmp_path / "catalog.db"
        _seed_catalog(
            catalog_path,
            tmp_path / "records.jsonl",
            _catalog_line("MS-1", manifest_url=manifest_url),
        )
        with CatalogDB(catalog_path) as catalog:
            record_id = catalog.records("archive-test")[0]["record_id"]

        args = _intake_args(
            tmp_path,
            source_flag="--catalog-record-id",
            source_value=record_id,
        )
        args.func(args)

        metadata = read_json(tmp_path / "catalog_doc" / "metadata.json")
        assert metadata["catalog_record_id"] == record_id
        assert metadata["source"]["manifest_url"] == manifest_url
        with Ledger(tmp_path / "factory.db") as ledger:
            assert ledger.item("catalog_doc")["recipe"] == "latin_manuscript"
        assert "catalog_doc is on the line" in capsys.readouterr().out


def test_direct_manifest_intake_remains_available(tmp_path, capsys):
    with _manifest_server(_MANIFEST) as manifest_url:
        args = _intake_args(
            tmp_path, source_flag="--manifest", source_value=manifest_url
        )
        args.func(args)

        metadata = read_json(tmp_path / "catalog_doc" / "metadata.json")
        assert metadata["catalog_record_id"] is None
        assert metadata["source"]["manifest_url"] == manifest_url
        with Ledger(tmp_path / "factory.db") as ledger:
            assert ledger.item("catalog_doc")["recipe"] == "latin_manuscript"
        assert "catalog_doc is on the line" in capsys.readouterr().out


def test_catalog_intake_rejects_unknown_record_before_workspace_creation(tmp_path):
    catalog_path = tmp_path / "catalog.db"
    with CatalogDB(catalog_path):
        pass
    args = _intake_args(
        tmp_path,
        source_flag="--catalog-record-id",
        source_value="source-record:" + "0" * 64,
    )

    with pytest.raises(KeyError, match="unknown catalog record"):
        args.func(args)

    assert not (tmp_path / "catalog_doc").exists()
    with Ledger(tmp_path / "factory.db") as ledger:
        assert ledger.item("catalog_doc") is None


def test_catalog_intake_rejects_tombstoned_record_before_workspace_creation(
    tmp_path,
):
    catalog_path = tmp_path / "catalog.db"
    source_path = tmp_path / "records.jsonl"
    _seed_catalog(
        catalog_path, source_path, _catalog_line("MS-1"), _catalog_line("MS-2")
    )
    with CatalogDB(catalog_path) as catalog:
        record_id = {
            row["source_key"]: row for row in catalog.records("archive-test")
        }["MS-2"]["record_id"]
    source_path.write_text(_catalog_line("MS-1") + "\n", encoding="utf-8")
    with CatalogDB(catalog_path) as catalog:
        sync_source(catalog, "archive-test")

    args = _intake_args(
        tmp_path, source_flag="--catalog-record-id", source_value=record_id
    )
    with pytest.raises(ValueError, match="tombstoned"):
        args.func(args)

    assert not (tmp_path / "catalog_doc").exists()
    with Ledger(tmp_path / "factory.db") as ledger:
        assert ledger.item("catalog_doc") is None


def test_catalog_intake_rejects_record_without_manifest_before_workspace_creation(
    tmp_path,
):
    catalog_path = tmp_path / "catalog.db"
    _seed_catalog(catalog_path, tmp_path / "records.jsonl", _catalog_line("MS-1"))
    with CatalogDB(catalog_path) as catalog:
        record_id = catalog.records("archive-test")[0]["record_id"]

    args = _intake_args(
        tmp_path, source_flag="--catalog-record-id", source_value=record_id
    )
    with pytest.raises(ValueError, match="no manifest URL"):
        args.func(args)

    assert not (tmp_path / "catalog_doc").exists()
    with Ledger(tmp_path / "factory.db") as ledger:
        assert ledger.item("catalog_doc") is None
