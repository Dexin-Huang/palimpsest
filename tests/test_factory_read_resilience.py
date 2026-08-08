"""Reader failure and production variant boundaries."""

from __future__ import annotations

import threading

import cv2
import numpy as np
import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import GatewayError, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.read import Read
from palimpsest.factory.workspace.io import atomic_write_json


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


def test_instrumented_reader_registers_same_socket_with_distinct_fingerprint():
    default = registry.get("read", "default")
    instrumented = registry.get("read", "omp_instrumented")

    assert instrumented.socket == default.socket
    assert instrumented.implementation_fingerprint != default.implementation_fingerprint
