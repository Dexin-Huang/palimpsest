"""Durable survey store: hit checks, cursor advancement, evidence, and the queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.sync import sync_source
from palimpsest.cli import build_parser
from palimpsest.factory.gateway.protocol import ModelResponse
from palimpsest import survey as survey_module


def _catalog_line(source_key: str) -> str:
    return json.dumps(
        {
            "source_key": source_key,
            "source_url": f"https://archive.test/{source_key}",
            "record": {
                "record_type": "manuscript",
                "manifest_url": f"https://archive.test/{source_key}/manifest.json",
                "titles": [f"Manuscript {source_key}"],
                "repository": "Test Repository",
                "access": "open",
            },
            "raw": {"title": f"Manuscript {source_key}"},
        }
    )


def _seed_catalog(root: Path, *keys: str) -> Path:
    source_path = root / "records.jsonl"
    source_path.write_text(
        "\n".join(_catalog_line(key) for key in keys) + "\n", encoding="utf-8"
    )
    database_path = root / "catalog.db"
    with CatalogDB(database_path) as database:
        database.add_source("archive-a", "normalized-jsonl", {"path": str(source_path)})
        sync_source(database, "archive-a")
    return database_path


def _noop_sample(_record, _count):
    return (
        [
            {
                "page_id": "page_0001",
                "order": 1,
                "label": "1",
                "url": "https://images.test/1.jpg",
            }
        ],
        (survey_module.ImageContent(b"jpeg", mime="image/jpeg"),),
    )


def _result(checks: dict[str, bool], summary: str = "Manuscript.") -> dict:
    full = {name: False for name in survey_module._CHECK_NAMES}
    full.update(checks)
    return {
        "what_was_read": "Sampled pages show handwritten text in an unidentified script.",
        "content_guess": "A manuscript whose content could not be determined from the samples.",
        "summary": summary,
        "checks": full,
        "risks": ["Page 1 has faded ink."],
    }


def _fake_generator(by_key: dict[str, dict], cost_usd: float = 0.01):
    def generate(request):
        key = json.loads(request.prompt.split("Evidence:\n", 1)[1])["source_key"]
        return (
            by_key[key],
            ModelResponse(
                text="{}",
                model=request.model,
                prompt_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cost_usd=cost_usd,
            ),
        )

    return generate


def _run(
    tmp_path,
    *,
    keys=("MS-1",),
    checks=None,
    limit=2,
    max_cost=1.0,
    monkeypatch=None,
    reset_cursor=False,
    after=None,
    generator=None,
):
    database_path = _seed_catalog(tmp_path, *keys)
    if generator is None:
        if checks is None:
            checks = {"sustained_text": True, "handwritten": True}
        generator = _fake_generator({key: _result(checks) for key in keys})
    assert monkeypatch is not None
    monkeypatch.setattr(survey_module, "_sample_record", _noop_sample)
    monkeypatch.setattr(survey_module, "generate_json", generator)
    return survey_module.survey_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        survey_db=tmp_path / "survey.db",
        library_root=tmp_path,
        record_limit=limit,
        page_samples=3,
        recommendation_limit=5,
        after=after,
        reset_cursor=reset_cursor,
        max_cost_usd=max_cost,
        output=tmp_path / "survey.json",
    )


def test_hits_are_the_sum_of_true_checks():
    assert survey_module._hits(_result({"sustained_text": True})) == 1
    assert (
        survey_module._hits(
            _result(
                {
                    "sustained_text": True,
                    "handwritten": True,
                    "language_identified": True,
                    "transcribable": True,
                }
            )
        )
        == 4
    )
    assert survey_module._hits(_result({})) == 0


def test_survey_persists_checks_and_advances_the_window(tmp_path, monkeypatch):
    report = _run(tmp_path, keys=("MS-1", "MS-2"), limit=1, monkeypatch=monkeypatch)

    assert report["evaluations"][0]["result"]["checks"]["handwritten"] is True
    assert report["recommendations"][0]["hits"] == 2
    assert report["schema_version"] == 2

    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        stats = survey.stats("archive-a")
        assert stats["evaluated"] == 1
        assert stats["hits"] == 1
        assert survey.cursor_for("archive-a") == "MS-1"

    # Second run resumes after the cursor and never re-pays for MS-1.
    second = _run(tmp_path, keys=("MS-1", "MS-2"), limit=1, monkeypatch=monkeypatch)
    assert [e["source_key"] for e in second["evaluations"]] == ["MS-2"]
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-2"
        assert survey.stats("archive-a")["evaluated"] == 2


def test_survey_stops_at_the_cost_ceiling(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        keys=("MS-1", "MS-2", "MS-3"),
        limit=3,
        max_cost=0.015,
        monkeypatch=monkeypatch,
    )
    assert report["cost_usd"] == pytest.approx(0.02)
    assert report["stop_reason"] == "the $0.0150 cost ceiling was reached"
    assert [e["source_key"] for e in report["evaluations"]] == ["MS-1", "MS-2"]
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-2"


def test_survey_records_failures_and_does_not_advance(tmp_path, monkeypatch):
    def generate(_request):
        raise survey_module.GatewayError("provider down")

    report = _run(
        tmp_path,
        keys=("MS-1", "MS-2"),
        limit=2,
        max_cost=1.0,
        monkeypatch=monkeypatch,
        generator=generate,
    )
    assert report["cost_usd"] == 0.0
    assert len(report["failures"]) == 2
    assert report["evaluations"] == []
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") is None


def test_survey_queue_lists_hits_and_excludes_adopted(tmp_path, monkeypatch):
    _run(tmp_path, keys=("MS-1", "MS-2"), monkeypatch=monkeypatch)

    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        evaluations = survey.latest_evaluations("archive-a")
    assert len(evaluations) == 2
    assert all(entry["hits"] == 2 for entry in evaluations)

    # Adopt MS-1 through a workspace metadata pointer, as intake would.
    workspace = tmp_path / "library" / "doc_ms1"
    workspace.mkdir(parents=True)
    (workspace / "metadata.json").write_text(
        json.dumps(
            {
                "doc_id": "doc_ms1",
                "catalog_record_id": evaluations[1]["record_id"],
                "source": {"kind": "iiif", "manifest_url": "https://archive.test/MS-1/manifest.json"},
            }
        ),
        encoding="utf-8",
    )
    adopted = survey_module._adopted_record_ids(tmp_path / "library")
    assert adopted == {evaluations[1]["record_id"]}


def test_survey_skips_records_already_in_the_library(tmp_path, monkeypatch):
    workspace = tmp_path / "doc_ms1"
    workspace.mkdir(parents=True)
    (workspace / "metadata.json").write_text(
        json.dumps(
            {
                "doc_id": "doc_ms1",
                "catalog_record_id": None,
                "source": {
                    "kind": "iiif",
                    "manifest_url": "https://archive.test/MS-1/manifest.json",
                },
            }
        ),
        encoding="utf-8",
    )
    report = _run(
        tmp_path,
        keys=("MS-1", "MS-2"),
        limit=2,
        monkeypatch=monkeypatch,
    )
    assert [e["source_key"] for e in report["evaluations"]] == ["MS-2"]


def test_survey_cursor_override_and_reset(tmp_path, monkeypatch):
    _run(tmp_path, keys=("MS-1", "MS-2"), monkeypatch=monkeypatch)
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-2"

    report = _run(
        tmp_path,
        keys=("MS-1", "MS-2", "MS-3"),
        limit=1,
        after="MS-2",
        monkeypatch=monkeypatch,
    )
    assert [e["source_key"] for e in report["evaluations"]] == ["MS-3"]
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-3"


def test_survey_schema_version_guard_rejects_stale_store(tmp_path, monkeypatch):
    store = tmp_path / "survey.db"
    with survey_module.SurveyDB(store):
        pass
    import sqlite3

    with sqlite3.connect(store) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="schema 99 is not supported"):
        with survey_module.SurveyDB(store):
            pass


def test_survey_cli_surface_replaces_select(tmp_path):
    parser = build_parser()
    choices = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None) and "survey" in action.choices
    )
    assert "survey" in choices
    assert "select" not in choices

    run = parser.parse_args(
        ["survey", "run", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert run.source_id == "archive-a"
    assert run.limit == 12
    assert run.max_cost == 1.0
    assert run.reset_cursor is False
    status = parser.parse_args(
        ["survey", "status", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert status.source_id == "archive-a"
    queue = parser.parse_args(
        ["survey", "queue", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert queue.source_id == "archive-a"
