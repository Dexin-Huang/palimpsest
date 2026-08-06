from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from palimpsest.factory import agent_cell
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.evaluation.probes import trusted_probes
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.read_extension import (
    OMP_EXTENSION_MEDIA_TYPE,
    render_candidate,
)
from palimpsest.factory.evaluation.metrics import MetricRegistry
from palimpsest.factory.evaluation.station_metrics.read import register_read_metrics
from palimpsest.factory.evaluation.suite import load_suite, validate_candidate_suite
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.read_omp import (
    MAX_TRANSCRIPTION_BYTES,
    OmpExtensionRead,
    TRANSCRIPTION_TIMEOUT_SECONDS,
    _DRAFT_NAMES,
    _DRAFT_SUBMISSION_EXTENSION_BYTES,
    _DRAFT_TIMEOUT_SECONDS,
    _SUBMISSION_EXTENSION_BYTES,
    _read_submission,
)

_MODEL = "openai-codex/gpt-5.6-luna"
_SOURCE = b"""import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
const exactBytes = `${1} %UNCHANGED%`;
export default function seed(_pi: ExtensionAPI) { void exactBytes; }
"""
_DRAFT_BINDING = {
    "id": "qwen3_8_max_draft_v1",
    "kind": "draft_model",
    "model": "token-plan/qwen3.8-max",
}
_PAGE = {
    "page_id": "f004r",
    "order": 4,
    "canvas_id": "canvas-4",
    "url": "https://archive.test/f004r.jpg",
}
_SUITE_PATH = (
    Path(__file__).resolve().parents[1]
    / "palimpsest/factory/evaluation/suites/read/omp-extension-development-v1.yaml"
)


def _prompt() -> Prompt:
    text = "Transcribe the staged page faithfully."
    return Prompt(
        name="read/zh/full_image",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _job(library_root: Path, source: str = _SOURCE.decode("utf-8")) -> Job:
    return Job(
        doc_id="omp_transcription_test",
        pages=(_PAGE,),
        page=_PAGE,
        library_root=library_root,
        config=StationConfig(
            model=_MODEL,
            prompt=_prompt(),
            options={"extension_source": source},
        ),
    )


def _write_page_image(path: Path) -> None:
    path.parent.mkdir(parents=True)
    encoded_ok, encoded = cv2.imencode(
        ".jpg", np.full((120, 180, 3), 255, dtype=np.uint8)
    )
    assert encoded_ok
    path.write_bytes(encoded.tobytes())


def _write_submission(
    workspace: Path,
    text: str,
    *,
    journal_count: int = 1,
    seal_count: int | None = None,
    seal_digest: str | None = None,
    layers: list[dict[str, str]] | None = None,
) -> None:
    out = workspace / "out"
    out.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {"transcription": text}
    if layers is not None:
        body["layers"] = layers
    artifact = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (out / "transcription.json").write_bytes(artifact)
    (out / ".transcription-submissions.jsonl").write_bytes(artifact * journal_count)
    seal = {
        "submission_count": journal_count if seal_count is None else seal_count,
        "artifact_sha256": seal_digest or hashlib.sha256(artifact).hexdigest(),
    }
    (out / ".transcription-submission-seal.json").write_text(
        json.dumps(seal, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def test_renderer_preserves_source_bytes_and_changes_candidate_fingerprint(
    tmp_path: Path,
) -> None:
    first = render_candidate(
        _SOURCE,
        role="challenger",
        model=_MODEL,
        output_dir=tmp_path,
    )
    changed_source = _SOURCE + b"// one causal change\n"
    second = render_candidate(
        changed_source,
        role="challenger",
        model=_MODEL,
        output_dir=tmp_path,
    )

    assert first.extension_path.read_bytes() == _SOURCE
    assert second.extension_path.read_bytes() == changed_source
    metadata = json.loads(first.metadata_path.read_text(encoding="utf-8"))
    assert metadata["source"] == {
        "file": first.extension_path.name,
        "media_type": OMP_EXTENSION_MEDIA_TYPE,
        "sha256": first.source_sha256,
        "size_bytes": len(_SOURCE),
    }
    assert first.source_sha256 != second.source_sha256
    assert first.fingerprint != second.fingerprint
    candidate = load_candidate(first.candidate_path)
    assert candidate.options["extension_source"].encode("utf-8") == _SOURCE
    metrics = MetricRegistry()
    register_read_metrics(metrics)
    suite = load_suite(
        _SUITE_PATH,
        metric_resolver=metrics,
        probe_resolver=trusted_probes(),
        judge_resolver={},
        verify_local=True,
    )
    validate_candidate_suite(candidate, suite)
    assert suite.station == candidate.station == "read"
    assert len(suite.cases) == 1
    assert set(suite.cases[0].inputs) == {"page_image_clean", "page_regions"}


def test_station_stages_page_details_and_candidate_source_and_uses_omp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = OmpExtensionRead()
    job = _job(tmp_path)
    image_path = job.path_of("page_image_clean")
    _write_page_image(image_path)
    observed: dict[str, object] = {}

    def fake_run(
        workspace: Path,
        task: str,
        model: str,
        timeout_s: int = agent_cell.DEFAULT_TIMEOUT_S,
        executor: str = "codex",
        tool_names: tuple[str, ...] | None = None,
    ) -> agent_cell.AgentRun:
        observed.update(
            workspace=workspace,
            task=task,
            model=model,
            timeout_s=timeout_s,
            executor=executor,
            tool_names=tool_names,
        )
        _write_submission(workspace, "  天地玄黃  ")
        return agent_cell.AgentRun(
            "session",
            321,
            workspace / "out" / "agent.log",
            0.01,
            process_stats={
                "assistant_turns": 3,
                "tool_calls": 2,
                "output_tokens": 57,
            },
        )

    monkeypatch.setattr(agent_cell, "run", fake_run)
    result = station.run(job)

    workspace = observed["workspace"]
    assert isinstance(workspace, Path)
    assert observed["model"] == _MODEL
    assert observed["timeout_s"] == TRANSCRIPTION_TIMEOUT_SECONDS
    assert observed["executor"] == "omp"
    assert observed["tool_names"] == ("read",)
    assert (workspace / ".omp/extensions/transcription.ts").read_bytes() == _SOURCE
    assert sorted(path.name for path in (workspace / "images").iterdir()) == [
        "details",
        image_path.name,
    ]
    detail_paths = sorted((workspace / "images" / "details").iterdir())
    assert [path.name for path in detail_paths] == [
        "detail-r1-c1.jpg",
        "detail-r1-c2.jpg",
        "detail-r1-c3.jpg",
        "detail-r2-c1.jpg",
        "detail-r2-c2.jpg",
        "detail-r2-c3.jpg",
    ]
    assert all(
        cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        is not None
        for path in detail_paths
    )
    assert list((workspace / "evidence").iterdir()) == []
    assert result.payload["text"] == "天地玄黃"
    assert (result.tokens_in, result.cost_usd) == (321, 0.01)
    assert result.process_stats == {
        "assistant_turns": 3,
        "tool_calls": 2,
        "output_tokens": 57,
    }


def test_submission_rejects_missing_artifact(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    with pytest.raises(agent_cell.AgentCellError, match="did not submit"):
        _read_submission(tmp_path)


def test_submission_rejects_malformed_artifact(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "transcription.json").write_text("[]", encoding="utf-8")
    with pytest.raises(agent_cell.AgentCellError, match="JSON object"):
        _read_submission(tmp_path)

    duplicate = tmp_path / "duplicate" / "out"
    duplicate.mkdir(parents=True)
    (duplicate / "transcription.json").write_text(
        '{"transcription":"first","transcription":"second"}', encoding="utf-8"
    )
    with pytest.raises(agent_cell.AgentCellError, match="strict UTF-8 JSON"):
        _read_submission(duplicate.parent)


def test_submission_rejects_empty_or_oversized_text(tmp_path: Path) -> None:
    _write_submission(tmp_path, "   ")
    with pytest.raises(agent_cell.AgentCellError, match="must not be empty"):
        _read_submission(tmp_path)

    oversized = tmp_path / "oversized"
    _write_submission(oversized, "x" * (MAX_TRANSCRIPTION_BYTES + 1))
    with pytest.raises(agent_cell.AgentCellError, match="exceeds"):
        _read_submission(oversized)


def test_submission_rejects_duplicate_or_changed_writes(tmp_path: Path) -> None:
    _write_submission(tmp_path, "天地玄黃", journal_count=2)
    with pytest.raises(agent_cell.AgentCellError, match="exactly one"):
        _read_submission(tmp_path)

    changed = tmp_path / "changed"
    _write_submission(changed, "天地玄黃", seal_digest="0" * 64)
    with pytest.raises(agent_cell.AgentCellError, match="changed after"):
        _read_submission(changed)


def test_submission_accepts_and_validates_layers(tmp_path: Path) -> None:
    layers = [
        {"kind": "primary", "text": "天地玄黃"},
        {"kind": "commentary", "text": "宇宙洪荒"},
    ]
    _write_submission(tmp_path, "天地玄黃\n宇宙洪荒", layers=layers)
    text, observed = _read_submission(tmp_path)
    assert text == "天地玄黃\n宇宙洪荒"
    assert observed == layers

    flat = tmp_path / "flat"
    _write_submission(flat, "天地玄黃")
    flat_text, flat_layers = _read_submission(flat)
    assert flat_text == "天地玄黃"
    assert flat_layers is None


def test_submission_rejects_malformed_layers(tmp_path: Path) -> None:
    mismatched = tmp_path / "mismatched"
    _write_submission(
        mismatched,
        "天地玄黃",
        layers=[{"kind": "primary", "text": "宇宙洪荒"}],
    )
    with pytest.raises(agent_cell.AgentCellError, match="assemble"):
        _read_submission(mismatched)

    no_primary = tmp_path / "no-primary"
    _write_submission(
        no_primary,
        "宇宙洪荒",
        layers=[{"kind": "commentary", "text": "宇宙洪荒"}],
    )
    with pytest.raises(agent_cell.AgentCellError, match="primary layer"):
        _read_submission(no_primary)

    unknown_kind = tmp_path / "unknown-kind"
    _write_submission(
        unknown_kind,
        "天地玄黃",
        layers=[{"kind": "footnote", "text": "天地玄黃"}],
    )
    with pytest.raises(agent_cell.AgentCellError, match="unknown layer kind"):
        _read_submission(unknown_kind)


def test_station_rejects_unknown_or_duplicate_tool_bindings(tmp_path: Path) -> None:
    station = OmpExtensionRead()
    with pytest.raises(ValueError, match="not stageable"):
        station.validate_options(
            {
                "extension_source": _SOURCE.decode("utf-8"),
                "tool_bindings": [
                    {
                        "id": "unknown",
                        "kind": "layout",
                        "model": "none",
                    }
                ],
            }
        )
    with pytest.raises(ValueError, match="sorted and unique"):
        station.validate_options(
            {
                "extension_source": _SOURCE.decode("utf-8"),
                "tool_bindings": [_DRAFT_BINDING, _DRAFT_BINDING],
            }
        )




def test_station_stages_draft_and_charges_its_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = OmpExtensionRead()
    job = Job(
        doc_id="omp_transcription_test",
        pages=(_PAGE,),
        page=_PAGE,
        library_root=tmp_path,
        config=StationConfig(
            model=_MODEL,
            prompt=_prompt(),
            options={
                "extension_source": _SOURCE.decode("utf-8"),
                "tool_bindings": [_DRAFT_BINDING],
            },
        ),
    )
    image_path = job.path_of("page_image_clean")
    _write_page_image(image_path)
    calls: list[dict[str, object]] = []

    def fake_run(
        workspace: Path,
        task: str,
        model: str,
        timeout_s: int = agent_cell.DEFAULT_TIMEOUT_S,
        executor: str = "codex",
        tool_names: tuple[str, ...] | None = None,
    ) -> agent_cell.AgentRun:
        calls.append(
            {
                "workspace": workspace,
                "task": task,
                "model": model,
                "timeout_s": timeout_s,
                "tool_names": tool_names,
            }
        )
        if model == "token-plan/qwen3.8-max":
            _write_submission(workspace, "天地玄黃")
            return agent_cell.AgentRun(
                "draft-session", 100, workspace / "out" / "draft.log", 0.002
            )
        assert "draft-N.txt" in task
        for draft_name in _DRAFT_NAMES:
            staged = workspace / "tools" / draft_name
            assert staged.read_text(encoding="utf-8") == "天地玄黃"
        _write_submission(workspace, "天地玄黃")
        return agent_cell.AgentRun(
            "scholar-session", 321, workspace / "out" / "agent.log", 0.01
        )

    monkeypatch.setattr(agent_cell, "run", fake_run)
    result = station.run(job)

    assert len(calls) == 4
    for call in calls[:3]:
        assert call["model"] == "token-plan/qwen3.8-max"
        assert call["tool_names"] == ("read",)
        assert call["timeout_s"] == _DRAFT_TIMEOUT_SECONDS
        assert "images/" in call["task"]
    assert calls[3]["model"] == _MODEL
    assert calls[3]["tool_names"] == ("read",)
    assert calls[3]["timeout_s"] == 2 * TRANSCRIPTION_TIMEOUT_SECONDS
    draft_workspaces = [call["workspace"] for call in calls[:3]]
    scholar_workspace = calls[3]["workspace"]
    assert isinstance(scholar_workspace, Path)
    for index, draft_workspace in enumerate(draft_workspaces, start=1):
        assert isinstance(draft_workspace, Path)
        assert draft_workspace.name.endswith(f"-draft-{index}")
        assert (
            draft_workspace / ".omp" / "extensions" / "00-submit-transcription.ts"
        ).read_bytes() == _DRAFT_SUBMISSION_EXTENSION_BYTES
        assert sorted(path.name for path in (draft_workspace / "images").iterdir()) == [
            image_path.name
        ]
        assert draft_workspace.parent == scholar_workspace.parent
    assert (
        scholar_workspace / ".omp" / "extensions" / "00-submit-transcription.ts"
    ).read_bytes() == _SUBMISSION_EXTENSION_BYTES
    assert result.payload["text"] == "天地玄黃"
    assert "layers" not in result.payload
    assert result.tokens_in == 621
    assert result.cost_usd == pytest.approx(0.016)


def test_station_degrades_to_draftless_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    station = OmpExtensionRead()
    job = Job(
        doc_id="omp_transcription_test",
        pages=(_PAGE,),
        page=_PAGE,
        library_root=tmp_path,
        config=StationConfig(
            model=_MODEL,
            prompt=_prompt(),
            options={
                "extension_source": _SOURCE.decode("utf-8"),
                "tool_bindings": [_DRAFT_BINDING],
            },
        ),
    )
    image_path = job.path_of("page_image_clean")
    _write_page_image(image_path)
    calls: list[dict[str, object]] = []

    def fake_run(
        workspace: Path,
        task: str,
        model: str,
        timeout_s: int = agent_cell.DEFAULT_TIMEOUT_S,
        executor: str = "codex",
        tool_names: tuple[str, ...] | None = None,
    ) -> agent_cell.AgentRun:
        calls.append({"task": task, "model": model, "timeout_s": timeout_s})
        if model == "token-plan/qwen3.8-max":
            # The draft session ends without a structured submission.
            return agent_cell.AgentRun(
                "draft-session", 55, workspace / "out" / "draft.log", 0.001
            )
        assert "draft-N.txt" not in task
        assert not (workspace / "tools").exists()
        _write_submission(workspace, "天地玄黃")
        return agent_cell.AgentRun(
            "scholar-session", 321, workspace / "out" / "agent.log", 0.01
        )

    monkeypatch.setattr(agent_cell, "run", fake_run)
    result = station.run(job)

    assert [call["model"] for call in calls] == [
        "token-plan/qwen3.8-max",
        "token-plan/qwen3.8-max",
        "token-plan/qwen3.8-max",
        _MODEL,
    ]
    assert calls[3]["timeout_s"] == TRANSCRIPTION_TIMEOUT_SECONDS
    assert result.payload["text"] == "天地玄黃"
    assert result.tokens_in == 321
    assert result.cost_usd is None
    assert capsys.readouterr().err == ""


def test_station_uses_surviving_drafts_when_one_staging_call_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = OmpExtensionRead()
    job = Job(
        doc_id="omp_transcription_test",
        pages=(_PAGE,),
        page=_PAGE,
        library_root=tmp_path,
        config=StationConfig(
            model=_MODEL,
            prompt=_prompt(),
            options={
                "extension_source": _SOURCE.decode("utf-8"),
                "tool_bindings": [_DRAFT_BINDING],
            },
        ),
    )
    image_path = job.path_of("page_image_clean")
    _write_page_image(image_path)
    draft_calls = 0

    def fake_run(
        workspace: Path,
        task: str,
        model: str,
        timeout_s: int = agent_cell.DEFAULT_TIMEOUT_S,
        executor: str = "codex",
        tool_names: tuple[str, ...] | None = None,
    ) -> agent_cell.AgentRun:
        nonlocal draft_calls
        if model == "token-plan/qwen3.8-max":
            draft_calls += 1
            if draft_calls > 1:
                _write_submission(workspace, f"草稿{draft_calls}")
            return agent_cell.AgentRun(
                f"draft-session-{draft_calls}",
                100,
                workspace / "out" / "draft.log",
                0.002,
            )
        assert "draft-N.txt" in task
        tools_dir = workspace / "tools"
        assert not (tools_dir / _DRAFT_NAMES[0]).exists()
        assert (tools_dir / _DRAFT_NAMES[1]).read_text(encoding="utf-8") == "草稿2"
        assert (tools_dir / _DRAFT_NAMES[2]).read_text(encoding="utf-8") == "草稿3"
        _write_submission(workspace, "天地玄黃")
        return agent_cell.AgentRun(
            "scholar-session", 321, workspace / "out" / "agent.log", 0.01
        )

    monkeypatch.setattr(agent_cell, "run", fake_run)
    result = station.run(job)

    assert draft_calls == 3
    assert result.payload["text"] == "天地玄黃"
    assert result.tokens_in == 521
    assert result.cost_usd is None


def test_renderer_carries_sorted_tool_bindings_into_candidate(
    tmp_path: Path,
) -> None:
    with_tools = render_candidate(
        _SOURCE,
        role="challenger",
        model=_MODEL,
        output_dir=tmp_path / "with-tools",
        tool_bindings=(_DRAFT_BINDING,),
    )
    candidate = load_candidate(with_tools.candidate_path)
    assert [dict(binding) for binding in candidate.options["tool_bindings"]] == [
        _DRAFT_BINDING
    ]
    assert candidate.options["extension_source"].encode("utf-8") == _SOURCE

    without_tools = render_candidate(
        _SOURCE,
        role="challenger",
        model=_MODEL,
        output_dir=tmp_path / "without-tools",
    )
    toolless = load_candidate(without_tools.candidate_path)
    assert "tool_bindings" not in toolless.options
    assert toolless.fingerprint != candidate.fingerprint

    with pytest.raises(ValueError, match="sorted and unique"):
        render_candidate(
            _SOURCE,
            role="challenger",
            model=_MODEL,
            output_dir=tmp_path / "bad-tools",
            tool_bindings=(_DRAFT_BINDING, _DRAFT_BINDING),
        )
