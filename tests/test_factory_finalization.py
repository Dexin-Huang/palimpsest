from __future__ import annotations

import json

import pytest

from palimpsest.factory import agent_cell
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.prompt_store import load
from palimpsest.factory.stations.finalize_edition import FinalizeEdition
from palimpsest.factory.workspace.io import atomic_write_json


DOC = "test_final_edition"


def _job(tmp_path, *, max_repairs: int = 0) -> Job:
    job = Job(
        doc_id=DOC,
        pages=({"page_id": "p1", "order": 1},),
        page=None,
        library_root=tmp_path / "library",
        config=StationConfig(
            model="gpt-5.6-sol",
            prompt=load("finalize/generic/edition"),
            options={"max_repairs": max_repairs},
        ),
    )
    atomic_write_json(
        job.path_of("page_transcription", "p1"),
        {
            "doc_id": DOC,
            "page_id": "p1",
            "text": "廣僧政",
            "route": "segmented",
            "regions": [],
            "candidate_readings": [],
            "adjudication_status": "adjudicated",
            "adjudication_reasoning": "The first graph remains ambiguous.",
            "unresolved": ["First graph: 廣 or 唐; ink alone is inconclusive."],
            "adjudication_error": None,
            "provenance": {"station": "read"},
        },
    )
    atomic_write_json(
        job.path_of("manuscript"),
        {
            "doc_id": DOC,
            "readers_note": "Draft note.",
            "joins": [],
            "sections": [
                {
                    "heading": "Draft heading",
                    "pages": {"from": "p1", "to": "p1"},
                    "original": "廣僧政",
                    "translation": "Guang, the Monastic Administrator",
                }
            ],
            "provenance": {"station": "reconstruct"},
        },
    )
    atomic_write_json(
        job.path_of("reference"),
        {
            "doc_id": DOC,
            "identification": {"work": "Account"},
            "reference_points": [],
            "provenance": {"station": "reference"},
        },
    )
    atomic_write_json(
        job.path_of("emendations"),
        {
            "doc_id": DOC,
            "sections": [{"heading": "Draft heading", "reading": "唐僧政"}],
            "apparatus": [
                {
                    "section": "Draft heading",
                    "original": "廣僧政",
                    "emended": "唐僧政",
                    "reason": "The name is corrected by the ink and dossier.",
                    "evidence": "reference point 1",
                }
            ],
            "provenance": {"station": "emend"},
        },
    )
    return job


def test_final_editor_reconciles_book_prose_without_mutating_evidence(
    tmp_path, monkeypatch
):
    job = _job(tmp_path)

    def fake_run(workspace, task, model, timeout_s, executor):
        assert model == "gpt-5.6-sol"
        manuscript = json.loads(
            (workspace / "evidence" / "manuscript.json").read_text(encoding="utf-8")
        )
        emendations = json.loads(
            (workspace / "evidence" / "emendations.json").read_text(encoding="utf-8")
        )
        assert "provenance" not in manuscript
        assert emendations["sections"][0]["reading"] == "唐僧政"
        audits = json.loads(
            (workspace / "evidence" / "transcription_audits.json").read_text(
                encoding="utf-8"
            )
        )
        assert "transcription_audits.json" in task
        assert audits["pages"][0]["page_id"] == "p1"
        assert audits["pages"][0]["unresolved"] == [
            "First graph: 廣 or 唐; ink alone is inconclusive."
        ]
        assert "provenance" not in audits["pages"][0]
        (workspace / "out" / "edition.json").write_text(
            json.dumps(
                {
                    "readers_note": "A corrected monastic account.",
                    "sections": [
                        {
                            "section_index": 0,
                            "heading": "Monastic Account",
                            "translation": "Tang, the Monastic Administrator",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return agent_cell.AgentRun("session", 321, workspace / "agent_run.log")

    monkeypatch.setattr(agent_cell, "run", fake_run)

    result = FinalizeEdition().run(job)

    assert result.tokens_in == 321
    assert result.payload == {
        "doc_id": DOC,
        "readers_note": "A corrected monastic account.",
        "sections": [
            {
                "section_index": 0,
                "heading": "Monastic Account",
                "translation": "Tang, the Monastic Administrator",
            }
        ],
    }
    assert "廣僧政" in job.path_of("manuscript").read_text(encoding="utf-8")


def test_final_editor_rejects_missing_sections(tmp_path, monkeypatch):
    job = _job(tmp_path)

    def fake_run(workspace, task, model, timeout_s, executor):
        (workspace / "out" / "edition.json").write_text(
            json.dumps({"readers_note": "A note.", "sections": []}),
            encoding="utf-8",
        )
        return agent_cell.AgentRun("session", 1, workspace / "agent_run.log")

    monkeypatch.setattr(agent_cell, "run", fake_run)

    with pytest.raises(ValueError, match="sections has 0 entries; expected 1"):
        FinalizeEdition().run(job)
