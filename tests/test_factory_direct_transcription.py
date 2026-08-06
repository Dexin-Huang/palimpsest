from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import GatewayError, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.transcribe import Transcribe


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
