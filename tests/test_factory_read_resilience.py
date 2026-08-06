"""Deterministic coverage for the read transient-single-reader challenger."""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.gateway import GatewayError, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.read import Read, TransientSingleReaderFallbackRead
from palimpsest.factory.workspace.io import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_MODEL = "fixture/primary-reader"
SECONDARY_MODEL = "fixture/secondary-reader"
ADJUDICATOR_MODEL = "fixture/adjudicator"
DUAL_PARAMS = {
    "secondary_model": SECONDARY_MODEL,
    "secondary_thinking_level": None,
    "adjudicator_model": ADJUDICATOR_MODEL,
    "adjudicator_thinking_level": "high",
}
PROMPT = Prompt(name="read/la/diplomatic", text="Transcribe.", sha256="x" * 64)


class ReaderGateway:
    """Route outcomes by model so dual-reader scheduling cannot reorder a script."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, request, **_kwargs):
        with self.lock:
            self.calls.append(request)
        outcome = self.outcomes[request.model]
        if isinstance(outcome, Exception):
            raise outcome
        return {"transcription": outcome["text"]}, ModelResponse(
            text="",
            model=outcome.get("model", request.model),
            finish_reason=outcome.get("finish_reason"),
            prompt_tokens=outcome.get("tokens_in", 10),
            output_tokens=outcome.get("tokens_out", 5),
            cost_usd=outcome.get("cost_usd", 0.001),
        )


def _job(tmp_path, *, regions=()):
    page = {"page_id": "f001r", "order": 1}
    job = Job(
        doc_id="fixture",
        pages=(page,),
        page=page,
        library_root=tmp_path,
        config=StationConfig(
            model=PRIMARY_MODEL,
            prompt=PROMPT,
            params=DUAL_PARAMS,
        ),
    )
    image_path = job.path_of("page_image_clean")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    encoded, image = cv2.imencode(".jpg", np.full((32, 32, 3), 235, np.uint8))
    assert encoded
    image_path.write_bytes(image.tobytes())
    atomic_write_json(
        job.path_of("page_regions"),
        {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "route": "full_page",
            "image": {"width": 32, "height": 32},
            "glyph_height_px": 8,
            "regions": list(regions),
        },
    )
    return job


def _success(text, **usage):
    return {"text": text, **usage}


def _transient(message="provider temporarily unavailable", **usage):
    return GatewayError(message, transient=True, **usage)


@pytest.mark.parametrize(
    ("successful_role", "successful_model", "failed_model"),
    [
        ("primary", PRIMARY_MODEL, SECONDARY_MODEL),
        ("secondary", SECONDARY_MODEL, PRIMARY_MODEL),
    ],
)
def test_challenger_commits_exactly_one_valid_reader_after_transient_peer_failure(
    tmp_path,
    monkeypatch,
    successful_role,
    successful_model,
    failed_model,
):
    gateway = ReaderGateway(
        {
            successful_model: _success(
                "lectio", tokens_in=10, tokens_out=5, cost_usd=0.001
            ),
            failed_model: _transient(tokens_in=7, tokens_out=3, cost_usd=0.004),
        }
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    result = TransientSingleReaderFallbackRead().run(_job(tmp_path))

    assert {call.model for call in gateway.calls} == {PRIMARY_MODEL, SECONDARY_MODEL}
    assert result.payload["text"] == "lectio"
    assert result.payload["adjudication_status"] == "single_reader_fallback"
    assert result.payload["adjudication_model"] is None
    assert result.payload["adjudication_requested_model"] == ADJUDICATOR_MODEL
    assert result.payload["candidate_readings"] == [
        {
            "role": successful_role,
            "requested_model": successful_model,
            "model": successful_model,
            "raw_text": "lectio",
            "text": "lectio",
        }
    ]
    assert successful_role in result.payload["adjudication_reasoning"]
    assert "explicitly classified transient" in result.payload["adjudication_reasoning"]
    assert (
        "transient failure (single-reader fallback;"
        in result.payload["adjudication_error"]
    )
    assert failed_model in result.payload["adjudication_error"]
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (17, 8, 0.005)


def test_default_still_rejects_transient_single_reader_failure(tmp_path, monkeypatch):
    gateway = ReaderGateway(
        {
            PRIMARY_MODEL: _success("lectio"),
            SECONDARY_MODEL: _transient(tokens_in=7, tokens_out=3, cost_usd=0.004),
        }
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    with pytest.raises(GatewayError, match="secondary reader failed") as caught:
        Read().run(_job(tmp_path))

    assert (caught.value.tokens_in, caught.value.tokens_out, caught.value.cost_usd) == (
        17,
        8,
        0.005,
    )


@pytest.mark.parametrize(
    "failure",
    [
        GatewayError(
            "request is permanently invalid",
            transient=False,
            tokens_in=7,
            tokens_out=3,
            cost_usd=0.004,
        ),
        GatewayError(
            "Model returned unparseable JSON after 3 attempts",
            transient=False,
            tokens_in=7,
            tokens_out=3,
            cost_usd=0.004,
        ),
    ],
    ids=("permanent", "malformed"),
)
def test_challenger_rejects_permanent_and_malformed_reader_failures(
    tmp_path, monkeypatch, failure
):
    gateway = ReaderGateway(
        {PRIMARY_MODEL: _success("lectio"), SECONDARY_MODEL: failure}
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    with pytest.raises(GatewayError, match="secondary reader failed") as caught:
        TransientSingleReaderFallbackRead().run(_job(tmp_path))

    assert caught.value.transient is False
    assert (caught.value.tokens_in, caught.value.tokens_out, caught.value.cost_usd) == (
        17,
        8,
        0.005,
    )


def test_challenger_rejects_empty_surviving_candidate(tmp_path, monkeypatch):
    gateway = ReaderGateway(
        {PRIMARY_MODEL: _success(""), SECONDARY_MODEL: _transient()}
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    with pytest.raises(GatewayError, match="secondary reader failed"):
        TransientSingleReaderFallbackRead().run(_job(tmp_path))


def test_challenger_preserves_truncation_escalation_without_fallback(
    tmp_path, monkeypatch
):
    region = {
        "region_id": "r00",
        "kind": "main_text",
        "bbox": [0, 0, 32, 32],
        "est_lines": 1,
        "reading_order": 0,
    }
    gateway = ReaderGateway(
        {
            PRIMARY_MODEL: _success("incomplete"),
            SECONDARY_MODEL: _transient(
                "reader output truncated",
                tokens_in=7,
                tokens_out=3,
                cost_usd=0.004,
                finish_reason="MAX_TOKENS",
            ),
        }
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    result = TransientSingleReaderFallbackRead().run(_job(tmp_path, regions=(region,)))

    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == ""
    assert result.payload["adjudication_status"] == "failed"
    assert result.payload["regions"][0]["adjudication_status"] == "failed"
    assert "single-reader fallback" not in result.payload["adjudication_error"]
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (34, 16, 0.01)


def test_challenger_rejects_both_reader_failures(tmp_path, monkeypatch):
    gateway = ReaderGateway(
        {
            PRIMARY_MODEL: _transient("primary unavailable"),
            SECONDARY_MODEL: _transient("secondary unavailable"),
        }
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", gateway)

    with pytest.raises(GatewayError) as caught:
        TransientSingleReaderFallbackRead().run(_job(tmp_path))

    assert "primary reader failed" in str(caught.value)
    assert "secondary reader failed" in str(caught.value)


def test_challenger_registers_same_socket_with_distinct_fingerprints():
    default = registry.get("transcribe", "default")
    challenger_station = registry.get("transcribe", "omp_instrumented")
    challenger = load_candidate(
        ROOT / "palimpsest/factory/candidates/transcribe/"
        "zh-qwen-instrumented-foreman-v1.yaml"
    )
    baseline = load_candidate(
        ROOT / "palimpsest/factory/candidates/transcribe/"
        "zh-qwen-full-image-development-v1.yaml"
    )

    assert challenger_station.socket == default.socket
    assert (
        challenger_station.implementation_fingerprint
        != default.implementation_fingerprint
    )
    assert challenger.variant == challenger_station.variant
    assert (
        challenger.implementation_fingerprint
        == challenger_station.implementation_fingerprint
    )
    assert challenger.fingerprint != baseline.fingerprint
