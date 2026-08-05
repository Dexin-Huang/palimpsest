"""Deterministic coverage for the ordered map/reduce survey challenger."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import GatewayError, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.survey import OrderedMapReduceSurvey
from palimpsest.factory.workspace.io import atomic_write_json


PROMPT = Prompt(name="survey/generic/brief", text="Build the brief.", sha256="x" * 64)
EMPTY_MAP = {
    "persons": [],
    "places": [],
    "dates": [],
    "terminology": [],
    "uncertainties": [],
    "sections": [],
    "abbreviations": [],
    "style_notes": [],
}
EMPTY_BRIEF = {
    "glossary": [],
    "outline": [],
    "abbreviations": [],
    "entities": [],
    "difficulty_flags": [],
    "style_notes": [],
}


def _response(*, tokens_in: int, tokens_out: int, cost: float) -> ModelResponse:
    return ModelResponse(
        text="",
        model="fixture/model",
        prompt_tokens=tokens_in,
        output_tokens=tokens_out,
        cost_usd=cost,
    )


def _job(tmp_path: Path, *, map_workers: int = 2) -> Job:
    pages = (
        {"page_id": "p001", "order": 1},
        {"page_id": "p002", "order": 2},
    )
    job = Job(
        doc_id="fixture",
        pages=pages,
        page=None,
        library_root=tmp_path,
        config=StationConfig(
            model="fixture/model",
            prompt=PROMPT,
            params={"temperature": 0.1, "max_output_tokens": 1000},
            options={"max_tokens_per_chunk": 8, "map_workers": map_workers},
        ),
    )
    for page in pages:
        atomic_write_json(
            job.path_of("page_transcription", page["page_id"]),
            {
                "doc_id": job.doc_id,
                "page_id": page["page_id"],
                "text": page["page_id"] * 4,
            },
        )
    return job


def test_map_passes_overlap_then_reduce_in_manuscript_order(tmp_path, monkeypatch):
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    second_started = threading.Event()
    calls: list[str] = []

    def gateway(request, **_kwargs):
        nonlocal active, maximum_active
        calls.append(request.prompt.splitlines()[0])
        if request.prompt.startswith("SURVEY_REDUCE_PASS"):
            assert active == 0
            assert request.prompt.index('"style_notes":["first"]') < (
                request.prompt.index('"style_notes":["second"]')
            )
            return EMPTY_BRIEF, _response(tokens_in=5, tokens_out=2, cost=0.005)

        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            if "page_id=p001" in request.prompt:
                assert second_started.wait(timeout=2)
                value = {**EMPTY_MAP, "style_notes": ["first"]}
                usage = _response(tokens_in=10, tokens_out=3, cost=0.01)
            else:
                second_started.set()
                value = {**EMPTY_MAP, "style_notes": ["second"]}
                usage = _response(tokens_in=20, tokens_out=4, cost=0.02)
            return value, usage
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("palimpsest.factory.stations.survey.generate_json", gateway)

    result = OrderedMapReduceSurvey().run(_job(tmp_path))

    assert maximum_active == 2
    assert calls.count("SURVEY_MAP_PASS chunk=1/2") == 1
    assert calls.count("SURVEY_MAP_PASS chunk=2/2") == 1
    assert calls[-1] == "SURVEY_REDUCE_PASS"
    assert result.payload == {
        "document": {"doc_id": "fixture", "total_pages": 2},
        **EMPTY_BRIEF,
    }
    assert (result.tokens_in, result.tokens_out) == (35, 9)
    assert result.cost_usd == pytest.approx(0.035)


def test_map_failure_blocks_reduce_and_preserves_all_observed_usage(
    tmp_path, monkeypatch
):
    calls: list[str] = []

    def gateway(request, **_kwargs):
        calls.append(request.prompt.splitlines()[0])
        if "page_id=p001" in request.prompt:
            raise GatewayError(
                "map failed",
                transient=True,
                tokens_in=7,
                tokens_out=2,
                cost_usd=0.007,
            )
        if request.prompt.startswith("SURVEY_REDUCE_PASS"):
            pytest.fail("reducer must not run after a failed map")
        return EMPTY_MAP, _response(tokens_in=11, tokens_out=3, cost=0.011)

    monkeypatch.setattr("palimpsest.factory.stations.survey.generate_json", gateway)

    with pytest.raises(GatewayError, match="map failed") as caught:
        OrderedMapReduceSurvey().run(_job(tmp_path))

    assert calls == [
        "SURVEY_MAP_PASS chunk=1/2",
        "SURVEY_MAP_PASS chunk=2/2",
    ]
    assert caught.value.transient is True
    assert (caught.value.tokens_in, caught.value.tokens_out) == (18, 5)
    assert caught.value.cost_usd == pytest.approx(0.018)


@pytest.mark.parametrize("map_workers", [0, -1, True, 1.5, "2"])
def test_map_workers_must_be_an_explicit_positive_integer(tmp_path, map_workers):
    with pytest.raises(ValueError, match="map_workers"):
        OrderedMapReduceSurvey().run(_job(tmp_path, map_workers=map_workers))
