"""Cutover contracts: IIIF intake, top-level CLI, and strict recipes."""

from __future__ import annotations

from palimpsest.cli import build_parser
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.intake import build_records, write_records
from palimpsest.factory.workspace.io import read_json

import pytest


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
