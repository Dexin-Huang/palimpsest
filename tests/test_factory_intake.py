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


def test_recipe_rejects_unknown_station_options(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        """name: bad
language: la
line:
  page:
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
