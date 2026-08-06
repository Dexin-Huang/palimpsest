from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations import read_omp
from palimpsest.factory.stations.read_omp import OmpInstrumentedRead

_MODEL = "openai-codex/gpt-5.6-luna"
_DRAFT_MODEL = "token-plan/qwen3.8-max"
_SOURCE = (
    'import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";\n'
    "export default function policy(_pi: ExtensionAPI) {}\n"
)
_PAGE = {"page_id": "page_0001", "order": 1, "canvas_id": "canvas-1"}
_DOC = "read_omp_test"


def _stage_object(library_root: Path, rows: list[dict]) -> str:
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    objects = library_root / "evaluations" / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    (objects / digest).write_text(body, encoding="utf-8", newline="\n")
    return digest


def _job(
    library_root: Path,
    *,
    quiet_max: int = 5,
    characters: list[dict] | None = None,
) -> Job:
    case_id = f"{_DOC}__{_PAGE['page_id']}"
    if characters is None:
        characters = [
            {"bbox": [10.0, 10.0, 20.0, 20.0], "score": 0.9},
            {"bbox": [10.0, 40.0, 20.0, 20.0], "score": 0.9},
        ]
    detections = _stage_object(
        library_root,
        [{"case_id": case_id, "characters": characters}],
    )
    verdicts = _stage_object(library_root, [{"case_id": case_id, "columns": []}])
    prompt_text = "Audit and correct the staged base transcription."
    return Job(
        doc_id=_DOC,
        pages=(_PAGE,),
        page=_PAGE,
        library_root=library_root,
        config=StationConfig(
            model=_MODEL,
            prompt=Prompt(
                name="transcribe/zh/foreman_v12",
                text=prompt_text,
                sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            ),
            options={
                "extension_source": _SOURCE,
                "tool_bindings": [
                    {
                        "id": "qwen3_8_max_draft_v1",
                        "kind": "draft_model",
                        "model": _DRAFT_MODEL,
                    }
                ],
                "sensors": {
                    "detections_sha256": detections,
                    "classifier_verdicts_sha256": verdicts,
                },
                "quiet_max_disagreements": quiet_max,
            },
        ),
    )


def _write_inputs(library_root: Path, route: str) -> None:
    doc = library_root / _DOC
    image_dir = doc / "page_image_clean"
    image_dir.mkdir(parents=True, exist_ok=True)
    encoded_ok, encoded = cv2.imencode(
        ".jpg", np.full((120, 180, 3), 255, dtype=np.uint8)
    )
    assert encoded_ok
    (image_dir / f"{_PAGE['page_id']}.jpg").write_bytes(encoded.tobytes())
    regions_dir = doc / "page_regions"
    regions_dir.mkdir(parents=True, exist_ok=True)
    (regions_dir / f"{_PAGE['page_id']}.json").write_text(
        json.dumps(
            {
                "doc_id": _DOC,
                "page_id": _PAGE["page_id"],
                "route": route,
                "image": "page_image_clean",
                "regions": [],
            }
        ),
        encoding="utf-8",
    )


def _fake_run(cost: float | None = 0.01) -> SimpleNamespace:
    return SimpleNamespace(tokens=100, cost_usd=cost, process_stats=None)


def _write_submission(workspace: Path, text: str) -> None:
    out = workspace / "out"
    out.mkdir(parents=True, exist_ok=True)
    artifact = (
        json.dumps({"transcription": text}, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    (out / "transcription.json").write_bytes(artifact)
    (out / ".transcription-submissions.jsonl").write_bytes(artifact)
    seal = {
        "submission_count": 1,
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
    }
    (out / ".transcription-submission-seal.json").write_text(
        json.dumps(seal, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def test_blank_route_costs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_inputs(tmp_path, "blank")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("blank pages must not stage drafts or run agents")

    monkeypatch.setattr(read_omp, "_stage_draft", forbidden)
    monkeypatch.setattr(read_omp.agent_cell, "run", forbidden)

    result = OmpInstrumentedRead().run(_job(tmp_path))
    validate_payload("page_transcription", result.payload)
    assert result.payload["text"] == ""
    assert result.payload["route"] == "blank"
    assert result.payload["adjudication_status"] == "not_needed"
    assert result.cost_usd is None


def test_empty_pinned_detections_gate_blank_without_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_inputs(tmp_path, "full_page")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("empty pinned detections must not reach any model")

    monkeypatch.setattr(read_omp, "_stage_draft", forbidden)
    monkeypatch.setattr(read_omp.agent_cell, "run", forbidden)

    result = OmpInstrumentedRead().run(_job(tmp_path, characters=[]))
    validate_payload("page_transcription", result.payload)
    assert result.payload["text"] == ""
    assert result.payload["route"] == "blank"
    assert result.payload["adjudication_status"] == "not_needed"
    assert result.cost_usd is None


def test_quiet_page_adopts_base_without_foreman(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_inputs(tmp_path, "full_page")
    base = "line one\nline two"

    def fake_stage(_root, *, image, model):
        assert model == _DRAFT_MODEL
        assert image.name.endswith(".jpg")
        return base, _fake_run()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("quiet pages must not run the foreman")

    monkeypatch.setattr(read_omp, "_stage_draft", fake_stage)
    monkeypatch.setattr(
        read_omp.instrumented_sensors, "compute_sensors", lambda *_a: ({}, {"count_mismatch_lines": 0, "classifier_dispute_lines": 0, "disagreement_lines": 0})
    )
    monkeypatch.setattr(read_omp.agent_cell, "run", forbidden)

    result = OmpInstrumentedRead().run(_job(tmp_path))
    validate_payload("page_transcription", result.payload)
    assert result.payload["text"] == base
    assert result.payload["adjudication_status"] == "not_needed"
    assert [entry["role"] for entry in result.payload["candidate_readings"]] == [
        "base",
        "shadow",
    ]
    assert result.payload["adjudication_model"] is None
    assert result.payload["unresolved"] == []
    assert result.cost_usd == pytest.approx(0.02)


def test_flagged_page_runs_foreman_and_reports_adjudication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_inputs(tmp_path, "full_page")
    final = "corrected line\nline two"
    drafts = iter(("base line\nline two", "shadow line\nline two"))

    def fake_stage(_root, *, image, model):
        assert model == _DRAFT_MODEL
        return next(drafts), _fake_run()

    def fake_agent_run(workspace, _task, *, model, **_kwargs):
        assert model == _MODEL
        _write_submission(workspace, final)
        return _fake_run()

    monkeypatch.setattr(read_omp, "_stage_draft", fake_stage)
    monkeypatch.setattr(
        read_omp.instrumented_sensors,
        "compute_sensors",
        lambda *_a: ({}, {"count_mismatch_lines": 0, "classifier_dispute_lines": 0, "disagreement_lines": 1}),
    )
    monkeypatch.setattr(
        read_omp.instrumented_sensors, "write_dossier", lambda *_a, **_k: None
    )
    monkeypatch.setattr(read_omp.agent_cell, "run", fake_agent_run)

    result = OmpInstrumentedRead().run(_job(tmp_path, quiet_max=0))
    validate_payload("page_transcription", result.payload)
    assert result.payload["text"] == final
    assert result.payload["adjudication_status"] == "completed"
    assert result.payload["adjudication_model"] == _MODEL
    assert "changed_lines=1" in result.payload["adjudication_reasoning"]
    assert result.payload["unresolved"] == ["disagreement_lines=1"]
    assert result.cost_usd == pytest.approx(0.03)
