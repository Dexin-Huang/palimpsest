"""Cutover contracts: IIIF intake, top-level CLI, and strict recipes."""

from __future__ import annotations

from palimpsest.cli import build_parser
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
