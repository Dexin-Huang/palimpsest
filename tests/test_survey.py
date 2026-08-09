"""Agent-checklist survey: cursor advancement, evidence, and the interest filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.sync import sync_source
from palimpsest.cli import build_parser
from palimpsest.factory import agent_cell
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


def _checklist(**overrides) -> dict:
    value = {
        "physical_form": "handwritten",
        "sustained_text": True,
        "language_script": "Chinese",
        "transcribable": True,
        "content_guess": "A household alchemical recipe collection.",
        "known_publicly": False,
        "web_check": "searched the title; found no edition outside the archive",
        "evidence": "Handwritten pages with sustained text.",
        "risks": ["Faded ink on page 2."],
    }
    value.update(overrides)
    return value


def _fake_agent(monkeypatch, checklists: dict[str, dict], cost_usd=0.1):
    """Fake the agent cell: write the record's checklist artifact and report usage."""
    produced = {"files": [], "resumes": 0}

    def fake_stage(root, *, skill, evidence, images):
        (root / "evidence").mkdir(parents=True)
        (root / "images").mkdir()
        (root / "out").mkdir()
        for name, payload in evidence.items():
            (root / "evidence" / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        for image in images:
            (root / "images" / image.name).write_bytes(b"fake-jpeg")
        return root

    def fake_run(workspace, task, model, executor, tool_names=None):
        key = json.loads(
            (workspace / "evidence" / "catalog.json").read_text(encoding="utf-8")
        )["titles"][0].split()[-1]
        checklist = checklists[key]
        (workspace / "out" / "checklist.json").write_text(
            json.dumps(checklist, ensure_ascii=False), encoding="utf-8"
        )
        produced["files"].append(key)
        return agent_cell.AgentRun(
            session_id="fake-session",
            tokens=100,
            log_path=workspace / "out" / "agent_run.log",
            cost_usd=cost_usd,
        )

    def fake_read_artifact(workspace, name):
        path = workspace / "out" / name
        return json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(survey_module.agent_cell, "stage_workspace", fake_stage)
    monkeypatch.setattr(survey_module.agent_cell, "run", fake_run)
    monkeypatch.setattr(survey_module.agent_cell, "resume", lambda *_a, **_k: None)
    monkeypatch.setattr(
        survey_module.agent_cell, "read_artifact", fake_read_artifact
    )
    return produced


def _run(
    tmp_path,
    *,
    keys=("MS-1",),
    checklists=None,
    limit=2,
    max_cost=10.0,
    monkeypatch=None,
    reset_cursor=False,
    after=None,
):
    database_path = _seed_catalog(tmp_path, *keys)
    if checklists is None:
        checklists = {key: _checklist() for key in keys}
    _fake_agent(monkeypatch, checklists)

    def fake_sample(_record, _count, samples_dir):
        samples_dir.mkdir(parents=True, exist_ok=True)
        (samples_dir / "page_01.jpg").write_bytes(b"fake-jpeg")
        return (
            [
                {
                    "page_id": "page_0001",
                    "order": 1,
                    "label": "1",
                    "url": "https://images.test/1.jpg",
                }
            ],
            [samples_dir / "page_01.jpg"],
        )

    monkeypatch.setattr(survey_module, "_sample_record", fake_sample)
    return survey_module.survey_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        survey_db=tmp_path / "survey.db",
        library_root=tmp_path,
        record_limit=limit,
        page_samples=3,
        after=after,
        reset_cursor=reset_cursor,
        max_cost_usd=max_cost,
        output=tmp_path / "survey.json",
    )


def test_survey_persists_checklist_and_advances_the_window(tmp_path, monkeypatch):
    report = _run(tmp_path, keys=("MS-1", "MS-2"), limit=1, monkeypatch=monkeypatch)

    assert report["schema_version"] == 3
    assert report["evaluations"][0]["checklist"]["content_guess"].startswith(
        "A household alchemical"
    )
    assert report["evaluations"][0]["session_id"] == "fake-session"

    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        stats = survey.stats("archive-a")
        assert stats["evaluated"] == 1
        assert survey.cursor_for("archive-a") == "MS-1"

    second = _run(tmp_path, keys=("MS-1", "MS-2"), limit=1, monkeypatch=monkeypatch)
    assert [e["source_key"] for e in second["evaluations"]] == ["MS-2"]
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-2"
        assert survey.stats("archive-a")["evaluated"] == 2


def test_survey_repairs_an_invalid_checklist_once(tmp_path, monkeypatch):
    invalid = _checklist()
    del invalid["risks"]

    def fake_run(workspace, task, model, executor, tool_names=None):
        first = not (workspace / "out" / "checklist.json").exists()
        payload = _checklist() if not first else invalid
        (workspace / "out" / "checklist.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return agent_cell.AgentRun(
            session_id="fake-session",
            tokens=100,
            log_path=workspace / "out" / "agent_run.log",
            cost_usd=0.1,
        )

    def fake_resume(workspace, session_id, message, executor):
        (workspace / "out" / "checklist.json").write_text(
            json.dumps(_checklist(), ensure_ascii=False), encoding="utf-8"
        )
        return agent_cell.AgentRun(
            session_id=session_id,
            tokens=50,
            log_path=workspace / "out" / "agent_resume.log",
            cost_usd=0.05,
        )

    database_path = _seed_catalog(tmp_path, "MS-1")

    def fake_sample(_record, _count, samples_dir):
        samples_dir.mkdir(parents=True, exist_ok=True)
        (samples_dir / "page_01.jpg").write_bytes(b"fake-jpeg")
        return (
            [
                {
                    "page_id": "page_0001",
                    "order": 1,
                    "label": "1",
                    "url": "https://images.test/1.jpg",
                }
            ],
            [samples_dir / "page_01.jpg"],
        )

    monkeypatch.setattr(survey_module, "_sample_record", fake_sample)
    def _fake_stage_dirs(root, **kw):
        (root / "out").mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(survey_module.agent_cell, "stage_workspace", _fake_stage_dirs)
    monkeypatch.setattr(survey_module.agent_cell, "run", fake_run)
    monkeypatch.setattr(survey_module.agent_cell, "resume", fake_resume)
    monkeypatch.setattr(
        survey_module.agent_cell,
        "read_artifact",
        lambda workspace, name: json.loads(
            (workspace / "out" / name).read_text(encoding="utf-8")
        ),
    )

    report = survey_module.survey_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        survey_db=tmp_path / "survey.db",
        library_root=tmp_path,
        record_limit=1,
        page_samples=3,
        after=None,
        reset_cursor=False,
        max_cost_usd=10.0,
        output=tmp_path / "survey.json",
    )
    assert report["evaluations"][0]["checklist"]["risks"] == ["Faded ink on page 2."]
    assert report["evaluations"][0]["tokens"] == 150  # run + repair folded


def test_survey_rejects_after_exhausted_repair(tmp_path, monkeypatch):
    database_path = _seed_catalog(tmp_path, "MS-1")
    invalid = _checklist()
    del invalid["sustained_text"]

    def fake_run(workspace, task, model, executor, tool_names=None):
        (workspace / "out" / "checklist.json").write_text(
            json.dumps(invalid, ensure_ascii=False), encoding="utf-8"
        )
        return agent_cell.AgentRun(
            session_id="s", tokens=100, log_path=workspace / "out" / "run.log"
        )

    def fake_resume(workspace, session_id, message, executor):
        (workspace / "out" / "checklist.json").write_text(
            json.dumps(invalid, ensure_ascii=False), encoding="utf-8"
        )
        return agent_cell.AgentRun(
            session_id=session_id, tokens=50, log_path=workspace / "out" / "r.log"
        )

    def fake_sample(_record, _count, samples_dir):
        samples_dir.mkdir(parents=True, exist_ok=True)
        (samples_dir / "page_01.jpg").write_bytes(b"fake-jpeg")
        return (
            [
                {
                    "page_id": "page_0001",
                    "order": 1,
                    "label": "1",
                    "url": "https://images.test/1.jpg",
                }
            ],
            [samples_dir / "page_01.jpg"],
        )

    monkeypatch.setattr(survey_module, "_sample_record", fake_sample)
    def _fake_stage_dirs(root, **kw):
        (root / "out").mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(survey_module.agent_cell, "stage_workspace", _fake_stage_dirs)
    monkeypatch.setattr(survey_module.agent_cell, "run", fake_run)
    monkeypatch.setattr(survey_module.agent_cell, "resume", fake_resume)
    monkeypatch.setattr(
        survey_module.agent_cell,
        "read_artifact",
        lambda workspace, name: json.loads(
            (workspace / "out" / name).read_text(encoding="utf-8")
        ),
    )

    report = survey_module.survey_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        survey_db=tmp_path / "survey.db",
        library_root=tmp_path,
        record_limit=1,
        page_samples=3,
        after=None,
        reset_cursor=False,
        max_cost_usd=10.0,
        output=tmp_path / "survey.json",
    )
    assert len(report["failures"]) == 1
    assert "missing field sustained_text" in report["failures"][0]["error"]
    assert report["evaluations"] == []


def test_survey_stops_at_the_cost_ceiling(tmp_path, monkeypatch):
    report = _run(
        tmp_path,
        keys=("MS-1", "MS-2", "MS-3"),
        limit=3,
        max_cost=0.15,
        monkeypatch=monkeypatch,
    )
    assert report["cost_usd"] == pytest.approx(0.2)
    assert report["stop_reason"] == "the $0.1500 cost ceiling was reached"
    assert [e["source_key"] for e in report["evaluations"]] == ["MS-1", "MS-2"]
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") == "MS-2"


def test_survey_records_failures_and_does_not_advance(tmp_path, monkeypatch):
    def fail_run(workspace, task, model, executor, tool_names=None):
        raise agent_cell.AgentCellError("omp failed")

    database_path = _seed_catalog(tmp_path, "MS-1", "MS-2")
    monkeypatch.setattr(
        survey_module.agent_cell, "stage_workspace", lambda root, **kw: root
    )
    monkeypatch.setattr(survey_module.agent_cell, "run", fail_run)

    def fake_sample(_record, _count, samples_dir):
        samples_dir.mkdir(parents=True, exist_ok=True)
        (samples_dir / "page_01.jpg").write_bytes(b"fake-jpeg")
        return (
            [{"page_id": "p", "order": 1, "label": "1", "url": "https://x/1.jpg"}],
            [samples_dir / "page_01.jpg"],
        )

    monkeypatch.setattr(survey_module, "_sample_record", fake_sample)
    report = survey_module.survey_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        survey_db=tmp_path / "survey.db",
        library_root=tmp_path,
        record_limit=2,
        page_samples=3,
        after=None,
        reset_cursor=False,
        max_cost_usd=10.0,
        output=tmp_path / "survey.json",
    )
    assert len(report["failures"]) == 2
    assert report["evaluations"] == []
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        assert survey.cursor_for("archive-a") is None


def test_filter_scores_stored_checklists_and_excludes_adopted(tmp_path, monkeypatch):
    _run(tmp_path, keys=("MS-1", "MS-2"), monkeypatch=monkeypatch)
    rules = {
        "require": {"transcribable": True, "sustained_text": True},
        "languages": ["Chinese", "Latin"],
        "forgotten_only": True,
        "keywords": ["alchemy", "recipe"],
    }
    with survey_module.SurveyDB(tmp_path / "survey.db") as survey:
        evaluations = survey.latest_evaluations("archive-a")
    assert len(evaluations) == 2
    for entry in evaluations:
        assert survey_module._filter_score(entry, rules) == 3
        assert "not-known (web check)" in survey_module._filter_reasons(entry, rules)

    # A record that is known_publicly fails the forgotten criterion.
    entries = survey_module.SurveyDB
    with entries(tmp_path / "survey.db") as survey:
        evaluations = survey.latest_evaluations("archive-a")
    evaluations[0]["known_publicly"] = 1
    assert survey_module._filter_score(evaluations[0], rules) == 2

    # Adopted records are excluded from the queue.
    workspace = tmp_path / "library" / "doc_ms1"
    workspace.mkdir(parents=True)
    (workspace / "metadata.json").write_text(
        json.dumps(
            {
                "doc_id": "doc_ms1",
                "catalog_record_id": evaluations[1]["record_id"],
                "source": {
                    "kind": "iiif",
                    "manifest_url": "https://archive.test/MS-1/manifest.json",
                },
            }
        ),
        encoding="utf-8",
    )
    adopted = survey_module._adopted_record_ids(tmp_path / "library")
    assert adopted == {evaluations[1]["record_id"]}


def test_filter_rules_file_rejects_unknown_fields(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"bogus": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown filter rule fields"):
        survey_module._load_rules(rules)


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


def test_survey_cli_surface(tmp_path):
    parser = build_parser()
    choices = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None) and "survey" in action.choices
    )
    assert "survey" in choices

    run = parser.parse_args(
        ["survey", "run", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert run.source_id == "archive-a"
    assert run.limit == 12
    assert run.max_cost == 10.0
    status = parser.parse_args(
        ["survey", "status", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert status.source_id == "archive-a"
    queue = parser.parse_args(
        ["survey", "filter", "archive-a", "--survey-db", str(tmp_path / "s.db")]
    )
    assert queue.source_id == "archive-a"
    assert queue.rules is None
