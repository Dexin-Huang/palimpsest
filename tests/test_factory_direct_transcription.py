from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import palimpsest.factory.stations.acquire as acquire_module
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import GatewayError, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.audit_transcription import AuditTranscription
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.workspace.io import atomic_write_json
from palimpsest.factory.workspace.io import read_json


DOC_ID = "direct_read_test"
PAGE = {
    "page_id": "f001r",
    "order": 1,
    "canvas_id": "canvas-1",
    "url": "https://archive.test/f001r.jpg",
}


def _prompt(name: str, text: str) -> Prompt:
    return Prompt(
        name=name,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _job(
    library_root: Path,
    *,
    model: str,
    prompt: Prompt,
    params: dict | None = None,
) -> Job:
    return Job(
        doc_id=DOC_ID,
        pages=(PAGE,),
        page=PAGE,
        library_root=library_root,
        config=StationConfig(model=model, prompt=prompt, params=params or {}),
    )


def _write_direct_recipe(recipes_dir: Path) -> None:
    recipes_dir.mkdir()
    (recipes_dir / "direct_transcription.yaml").write_text(
        """name: direct_transcription
language: zh
line:
  - station: acquire
  - station: transcribe
    model: ${PALIMPSEST_MODEL_READING}
    prompt: transcribe/zh/full_image
    params:
      temperature: 0.1
      media_resolution: high
      max_output_tokens: 32768
      thinking_level: high
  - station: audit_transcription
    model: ${PALIMPSEST_MODEL_ADJUDICATOR}
    prompt: audit_transcription/zh/full_image
    params:
      temperature: 0.1
      media_resolution: high
      max_output_tokens: 32768
      thinking_level: high
""",
        encoding="utf-8",
    )


def test_direct_recipe_is_raw_transcribe_then_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PALIMPSEST_MODEL_READING", "token-plan/qwen3.8-max")
    monkeypatch.setenv("PALIMPSEST_MODEL_ADJUDICATOR", "openai-codex/gpt-5.6-sol")
    recipes_dir = tmp_path / "recipes"
    _write_direct_recipe(recipes_dir)

    recipe = load_recipe("direct_transcription", recipes_dir=recipes_dir)

    assert [spec.station.name for spec in recipe.steps] == [
        "acquire",
        "transcribe",
        "audit_transcription",
    ]
    assert recipe.steps[1].station.consumes == ("page_image",)
    assert recipe.steps[1].station.produces == "page_transcription_draft"
    assert recipe.steps[2].station.consumes == (
        "page_image",
        "page_transcription_draft",
    )
    assert recipe.steps[2].station.produces == "page_transcription_audit"


def test_transcribe_sends_raw_image_once_and_records_reader_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = Transcribe()
    job = _job(
        tmp_path,
        model="token-plan/qwen3.8-max",
        prompt=_prompt(
            "transcribe/zh/full_image", "Please provide the full transcription."
        ),
        params={
            "media_resolution": "high",
            "max_output_tokens": 32768,
            "thinking_level": "high",
        },
    )
    image_path = job.path_of("page_image")
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"raw archive image")
    requests = []

    def fake_generate(request):
        requests.append(request)
        return {"transcription": "  天地玄黃  "}, ModelResponse(
            text='{"transcription":"天地玄黃"}',
            model="qwen3.8-max-2026-07-01",
            finish_reason="STOP",
            prompt_tokens=11,
            output_tokens=7,
            thought_tokens=3,
            cost_usd=0.004,
        )

    monkeypatch.setattr(
        "palimpsest.factory.stations.transcribe.generate_json", fake_generate
    )

    result = station.run(job)

    assert len(requests) == 1
    request = requests[0]
    assert request.images == (image_path,)
    assert request.prompt == "Please provide the full transcription."
    assert request.system is None
    assert request.thinking_level == "high"
    assert result.payload == {
        "doc_id": DOC_ID,
        "page_id": "f001r",
        "page_seq": 1,
        "canvas_id": "canvas-1",
        "text": "天地玄黃",
        "requested_model": "token-plan/qwen3.8-max",
        "model": "qwen3.8-max-2026-07-01",
        "finish_reason": "STOP",
    }
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (11, 10, 0.004)


def test_transcribe_rejects_incomplete_full_image_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = Transcribe()
    job = _job(
        tmp_path,
        model="token-plan/qwen3.8-max",
        prompt=_prompt("transcribe/zh/full_image", "Transcribe."),
    )
    image_path = job.path_of("page_image")
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"raw archive image")

    monkeypatch.setattr(
        "palimpsest.factory.stations.transcribe.generate_json",
        lambda request: (
            {"transcription": "partial"},
            ModelResponse(
                text='{"transcription":"partial"}',
                model="qwen3.8-max",
                finish_reason="MAX_TOKENS",
                prompt_tokens=5,
                output_tokens=9,
                cost_usd=0.002,
            ),
        ),
    )

    with pytest.raises(GatewayError, match="direct transcription was truncated") as exc:
        station.run(job)

    assert exc.value.tokens_in == 5
    assert exc.value.tokens_out == 9
    assert exc.value.cost_usd == 0.002
    assert exc.value.finish_reason == "MAX_TOKENS"


def test_auditor_inspects_raw_image_and_preserves_reader_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    station = AuditTranscription()
    job = _job(
        tmp_path,
        model="openai-codex/gpt-5.6-sol",
        prompt=_prompt(
            "audit_transcription/zh/full_image", "Audit this transcription."
        ),
        params={"thinking_level": "high", "media_resolution": "high"},
    )
    image_path = job.path_of("page_image")
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"raw archive image")
    atomic_write_json(
        job.path_of("page_transcription_draft"),
        {
            "doc_id": DOC_ID,
            "page_id": "f001r",
            "page_seq": 1,
            "canvas_id": "canvas-1",
            "text": "天地元黃",
            "requested_model": "token-plan/qwen3.8-max",
            "model": "qwen3.8-max-2026-07-01",
            "finish_reason": "STOP",
        },
    )
    requests = []

    def fake_generate(request):
        requests.append(request)
        return {
            "transcription": "天地玄黃",
            "reasoning": "Corrected 元 to visible 玄.",
            "unresolved": ["  final damaged glyph  ", ""],
        }, ModelResponse(
            text="audited",
            model="gpt-5.6-sol-2026-07-15",
            finish_reason="STOP",
            prompt_tokens=19,
            output_tokens=12,
            thought_tokens=8,
            cost_usd=0.02,
        )

    monkeypatch.setattr(
        "palimpsest.factory.stations.audit_transcription.generate_json", fake_generate
    )

    result = station.run(job)

    assert len(requests) == 1
    request = requests[0]
    assert request.images == (image_path,)
    assert "天地元黃" in request.prompt
    assert "untrusted quoted data" in request.prompt
    assert result.payload["text"] == "天地玄黃"
    assert result.payload["route"] == "raw_full_image"
    assert result.payload["regions"] == []
    assert result.payload["candidate_readings"] == [
        {
            "role": "reader",
            "requested_model": "token-plan/qwen3.8-max",
            "model": "qwen3.8-max-2026-07-01",
            "raw_text": "天地元黃",
            "text": "天地元黃",
        }
    ]
    assert result.payload["adjudication_status"] == "adjudicated"
    assert result.payload["adjudication_requested_model"] == (
        "openai-codex/gpt-5.6-sol"
    )
    assert result.payload["adjudication_model"] == "gpt-5.6-sol-2026-07-15"
    assert result.payload["adjudication_reasoning"] == "Corrected 元 to visible 玄."
    assert result.payload["unresolved"] == ["final damaged glyph"]
    assert result.payload["adjudication_error"] is None
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (19, 20, 0.02)


def test_conductor_runs_raw_image_reader_and_auditor_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library_root = tmp_path / "library"
    doc_root = library_root / DOC_ID
    doc_root.mkdir(parents=True)
    atomic_write_json(doc_root / "page_list.json", {"doc_id": DOC_ID, "pages": [PAGE]})
    atomic_write_json(
        doc_root / "metadata.json",
        {"doc_id": DOC_ID, "source_catalog": {"title": "Direct read test"}},
    )
    monkeypatch.setenv("PALIMPSEST_MODEL_READING", "token-plan/qwen3.8-max")
    monkeypatch.setenv("PALIMPSEST_MODEL_ADJUDICATOR", "openai-codex/gpt-5.6-sol")

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def iter_content(self, chunk_size):
            yield b"raw archive image"

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

    monkeypatch.setattr(
        acquire_module.requests, "get", lambda url, **kwargs: FakeResponse()
    )

    def fake_generate(request):
        if request.model == "token-plan/qwen3.8-max":
            return {"transcription": "天地元黃"}, ModelResponse(
                text="draft",
                model="qwen3.8-max-2026-07-01",
                finish_reason="STOP",
                prompt_tokens=10,
                output_tokens=6,
                cost_usd=0.003,
            )
        assert request.model == "openai-codex/gpt-5.6-sol"
        assert "天地元黃" in request.prompt
        return {
            "transcription": "天地玄黃",
            "reasoning": "Corrected one visible character.",
            "unresolved": [],
        }, ModelResponse(
            text="audit",
            model="gpt-5.6-sol-2026-07-15",
            finish_reason="STOP",
            prompt_tokens=20,
            output_tokens=10,
            cost_usd=0.02,
        )

    monkeypatch.setattr(
        "palimpsest.factory.stations.transcribe.generate_json", fake_generate
    )
    monkeypatch.setattr(
        "palimpsest.factory.stations.audit_transcription.generate_json", fake_generate
    )

    recipes_dir = tmp_path / "recipes"
    _write_direct_recipe(recipes_dir)
    with Ledger(library_root / "factory.db") as ledger:
        ledger.adopt(DOC_ID, recipe="direct_transcription")
        report = Conductor(
            ledger,
            library_root=library_root,
            workers=1,
            recipe_loader=lambda name: load_recipe(name, recipes_dir=recipes_dir),
        ).run(DOC_ID)
        assert ledger.item(DOC_ID)["status"] == "complete"

    assert [(cell.station, cell.action) for cell in report.cells] == [
        ("acquire", "ran"),
        ("transcribe", "ran"),
        ("audit_transcription", "ran"),
    ]
    draft = read_json(doc_root / "page_transcription_draft" / "f001r.json")
    audit = read_json(doc_root / "page_transcription_audit" / "f001r.json")
    assert draft["text"] == "天地元黃"
    assert audit["text"] == "天地玄黃"
    assert audit["candidate_readings"][0]["text"] == draft["text"]
