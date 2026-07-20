"""Executor seam tests: cell specs are self-contained, workers are fungible.

The subprocess tests spawn REAL worker processes on non-model cells —
proving a cell runs identically outside the conductor's interpreter.
"""

from __future__ import annotations

import pytest

from palimpsest.factory.core.cell import CellOutcome, CellSpec, execute_cell
from palimpsest.factory.core.executors import (
    CellExecutionError,
    SubprocessExecutor,
    make,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path

DOC = "celldoc"


@pytest.fixture
def workspace(tmp_path):
    """A doc with transcription + translation + brief already on disk,
    ready for the (no-model) assemble_page cell."""
    doc_dir = tmp_path / DOC
    doc_dir.mkdir()
    atomic_write_json(
        doc_dir / "page_list.json",
        {"doc_id": DOC, "pages": [{"page_id": "f001r", "order": 1}]},
    )
    atomic_write_json(
        artifact_path(DOC, "page_transcription", "f001r", tmp_path),
        {
            "doc_id": DOC,
            "page_id": "f001r",
            "page_seq": 1,
            "canvas_id": "",
            "text": "Lorem",
            "route": "full_page",
            "regions": [],
        },
    )
    atomic_write_json(
        artifact_path(DOC, "page_translation", "f001r", tmp_path),
        {
            "doc_id": DOC,
            "page_id": "f001r",
            "translation": "Lorem EN",
            "notes": "",
            "flags": {},
        },
    )
    return tmp_path


def _assemble_spec(workspace) -> CellSpec:
    return CellSpec(
        doc_id=DOC,
        station="assemble_page",
        page_id="f001r",
        library_root=str(workspace),
        config_fingerprint="cfg",
        input_fingerprint="inp",
    )


def test_spec_roundtrips_through_json(workspace):
    spec = _assemble_spec(workspace)
    assert CellSpec.from_json(spec.to_json()) == spec


def test_execute_cell_inline(workspace):
    outcome = execute_cell(_assemble_spec(workspace))
    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", workspace))
    assert assembled["original"]["text"] == "Lorem"
    assert assembled["translation"]["text"] == "Lorem EN"
    assert assembled["provenance"]["config_fingerprint"] == "cfg"
    station_fingerprint = assembled["provenance"]["station_fingerprint"]
    assert len(station_fingerprint) == 16
    assert int(station_fingerprint, 16) >= 0
    assert outcome.output_path.endswith("f001r.json")


def test_subprocess_executor_runs_real_worker(workspace):
    outcome = SubprocessExecutor().execute(_assemble_spec(workspace))
    assert isinstance(outcome, CellOutcome)
    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", workspace))
    assert assembled["translation"]["text"] == "Lorem EN"
    assert assembled["provenance"]["station"] == "assemble_page"


def test_subprocess_executor_reports_structured_failure(workspace):
    bad = CellSpec(
        doc_id=DOC,
        station="assemble_page",
        page_id="f001r",
        library_root=str(workspace / "nonexistent"),
        config_fingerprint="cfg",
        input_fingerprint="inp",
    )
    with pytest.raises(CellExecutionError) as excinfo:
        SubprocessExecutor().execute(bad)
    assert excinfo.value.kind == "filenotfounderror"


def test_worker_refuses_mismatched_prompt_hash(workspace):
    spec = CellSpec(
        doc_id=DOC,
        station="read",
        page_id="f001r",
        library_root=str(workspace),
        config_fingerprint="cfg",
        input_fingerprint="inp",
        model="gemini-3.1-flash-lite-preview",
        prompt_name="read/la/diplomatic",
        prompt_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        execute_cell(spec)


def test_make_rejects_unknown_executor():
    with pytest.raises(ValueError, match="Unknown executor"):
        make("carrier_pigeon")
