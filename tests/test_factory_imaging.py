"""Computer-vision primitives, segmentation, and read routing."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import cv2
import numpy as np
import httpx
import pytest

from palimpsest.factory import imaging
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import GatewayError, ImageContent, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.read import Read
from palimpsest.factory.stations.segment import Segment
from palimpsest.factory.workspace.io import atomic_write_json


def _page(height=800, width=600, bg=235):
    return np.full((height, width, 3), bg, np.uint8)


def _text_block(page, x, y, lines, line_w=250, glyph_h=8, gap=12, shade=30):
    for row in range(lines):
        y0 = y + row * (glyph_h + gap)
        cv2.rectangle(page, (x, y0), (x + line_w, y0 + glyph_h), (shade,) * 3, -1)
    return page


def _job(tmp_path, page_id="f001r", options=None, prompt=None, params=None):
    doc_dir = tmp_path / "doc1"
    doc_dir.mkdir(exist_ok=True)
    pages = ({"page_id": page_id, "order": 1},)
    return Job(
        doc_id="doc1",
        pages=pages,
        page=pages[0],
        library_root=tmp_path,
        config=StationConfig(
            model="fake-model",
            prompt=prompt,
            params=params or {},
            options=options or {},
        ),
    )


def _write_clean_image(job, image):
    path = job.path_of("page_image_clean")
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    path.write_bytes(buffer.tobytes())


# --- imaging primitives -------------------------------------------------------


def test_watermark_removed_faint_marks_kept():
    """Real watermarks are LARGE thin-stroke letterforms in a light band;
    faint pencil marks are SMALL thin strokes in the same band."""
    page = _page()
    # watermark: a big light-gray ring (thin strokes, large component)
    cv2.ellipse(page, (300, 400), (180, 180), 0, 0, 360, (205,) * 3, 4)
    # faint pencil note: short thin light-gray stroke
    cv2.line(page, (80, 60), (105, 60), (205,) * 3, 2)
    # dark ink line
    cv2.rectangle(page, (150, 700), (400, 712), (30,) * 3, -1)

    cleaned = imaging.remove_overlay_marks(page)
    gray = imaging.to_gray(cleaned)
    assert gray[220, 300] > 225  # top of watermark ring painted away
    assert gray[400, 120] > 225  # left of ring painted away
    assert gray[60, 92] < 220  # faint small mark survives
    assert gray[706, 200] < 60  # dark ink untouched


def test_ink_masks_split_dark_and_faint():
    page = _page()
    cv2.rectangle(page, (100, 100), (300, 114), (30,) * 3, -1)  # dark line
    cv2.line(page, (100, 200), (125, 200), (205,) * 3, 2)  # faint small stroke
    cv2.ellipse(page, (300, 400), (180, 180), 0, 0, 360, (205,) * 3, 4)
    dark, faint = imaging.ink_masks(imaging.to_gray(page))
    assert dark[107, 200] == 255 and faint[107, 200] == 0
    assert faint[200, 110] == 255 and dark[200, 110] == 0
    assert faint[220, 300] == 0  # large light overlay is not a faint annotation


# --- segment station ----------------------------------------------------------


def test_segment_blank_page(tmp_path):
    job = _job(tmp_path)
    _write_clean_image(job, _page())
    payload = Segment().run(job).payload
    assert payload["route"] == "blank"
    assert payload["regions"] == []


def test_segment_light_page_routes_full_page(tmp_path):
    job = _job(tmp_path)
    # enough ink to clear the full-page floor (hallucination guard)
    _write_clean_image(job, _text_block(_page(), 150, 200, lines=8, line_w=350))
    payload = Segment().run(job).payload
    assert payload["route"] == "full_page"
    assert 1 <= len(payload["regions"]) <= 3


def test_segment_dense_page_routes_segmented_with_marginalia(tmp_path):
    page = _page(1000, 700)
    _text_block(page, 150, 60, lines=30, line_w=350)  # tall main column
    _text_block(page, 620, 300, lines=3, line_w=50)  # margin note right edge
    job = _job(tmp_path)
    _write_clean_image(job, page)
    payload = Segment().run(job).payload

    assert payload["route"] == "segmented"
    kinds = [r["kind"] for r in payload["regions"]]
    assert "marginalia" in kinds
    # the 30-line column was split to respect the per-region line budget
    main_blobs = [r for r in payload["regions"] if r["kind"] != "marginalia"]
    assert len(main_blobs) >= 2
    assert all(r["est_lines"] <= 20 for r in payload["regions"])
    # reading order: main text before marginalia
    ordered = sorted(payload["regions"], key=lambda r: r["reading_order"])
    assert ordered[0]["kind"] != "marginalia"
    assert ordered[-1]["kind"] == "marginalia"


def test_segment_extracts_figures_whole(tmp_path):
    page = _page(1000, 700)
    _text_block(page, 100, 60, lines=8, line_w=300)
    # a diagram: large thin-stroke concentric circles — big in both dims,
    # nearly empty bbox
    for radius in (150, 110, 70):
        cv2.circle(page, (350, 650), radius, (30,) * 3, 3)
    job = _job(tmp_path)
    _write_clean_image(job, page)
    payload = Segment().run(job).payload

    figures = [r for r in payload["regions"] if r["kind"] == "figure"]
    assert len(figures) == 1
    x, y, bw, bh = figures[0]["bbox"]
    assert bw > 250 and bh > 250  # the whole diagram, not slices
    assert any(r["kind"] != "figure" for r in payload["regions"])  # text kept


def test_segment_drops_bleed_through(tmp_path):
    page = _page(1000, 700)
    _text_block(page, 100, 100, lines=8, line_w=300, shade=30)  # real ink
    _text_block(page, 100, 450, lines=6, line_w=300, shade=150)  # bleed-depth
    job = _job(tmp_path)
    _write_clean_image(job, page)
    payload = Segment().run(job).payload

    tops = [r["bbox"][1] for r in payload["regions"]]
    assert any(y < 400 for y in tops)  # real block survives
    assert not any(y >= 400 for y in tops)  # shallow block dropped


# --- gateway config mapping ----------------------------------------------------


def test_gemini_request_maps_structured_output():
    from palimpsest.factory.gateway import ModelRequest
    from palimpsest.factory.gateway.gemini import _request_kwargs

    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    kwargs = _request_kwargs(
        ModelRequest(model="m", prompt="p", json_output=True, json_schema=schema)
    )
    assert kwargs["store"] is False
    assert kwargs["response_format"] == [
        {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        }
    ]
    assert "response_mime_type" not in kwargs

    plain = _request_kwargs(ModelRequest(model="m", prompt="p", json_output=True))
    assert plain["response_format"] == [
        {"type": "text", "mime_type": "application/json"}
    ]


def test_gemini_request_builds_multimodal_blocks(tmp_path):
    from palimpsest.factory.gateway import ModelRequest
    from palimpsest.factory.gateway.gemini import _request_kwargs

    path_image = tmp_path / "page.jpg"
    path_image.write_bytes(b"jpeg")
    kwargs = _request_kwargs(
        ModelRequest(
            model="gemini-3.6-flash",
            prompt="Transcribe.",
            system="Read exactly.",
            images=(path_image, ImageContent(b"png")),
            media_resolution="high",
            thinking_level="low",
        )
    )

    assert kwargs["input"][0] == {"type": "text", "text": "Transcribe."}
    assert kwargs["input"][1] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "data": base64.b64encode(b"jpeg").decode("ascii"),
        "resolution": "high",
    }
    assert kwargs["input"][2]["mime_type"] == "image/png"
    assert kwargs["input"][2]["resolution"] == "high"
    assert kwargs["system_instruction"] == "Read exactly."
    assert kwargs["generation_config"]["thinking_level"] == "low"


def test_gemini_request_rejects_unsupported_media(tmp_path):
    from palimpsest.factory.gateway import GatewayError, ModelRequest
    from palimpsest.factory.gateway.gemini import _request_kwargs

    tiff = tmp_path / "page.tiff"
    tiff.write_bytes(b"tiff")
    with pytest.raises(GatewayError, match="Unsupported image type"):
        _request_kwargs(ModelRequest(model="m", prompt="p", images=(tiff,)))
    with pytest.raises(GatewayError, match="Unsupported image type"):
        _request_kwargs(
            ModelRequest(
                model="m",
                prompt="p",
                images=(ImageContent(b"tiff", mime="image/tiff"),),
            )
        )
    with pytest.raises(GatewayError, match="Unknown media resolution"):
        _request_kwargs(
            ModelRequest(model="m", prompt="p", media_resolution="enormous")
        )
    with pytest.raises(GatewayError, match="Unknown thinking level"):
        _request_kwargs(ModelRequest(model="m", prompt="p", thinking_level="off"))

    missing = tmp_path / "missing.png"
    with pytest.raises(GatewayError, match="Could not read image"):
        _request_kwargs(ModelRequest(model="m", prompt="p", images=(missing,)))
    with pytest.raises(GatewayError, match="empty or invalid"):
        _request_kwargs(
            ModelRequest(model="m", prompt="p", images=(ImageContent(b""),))
        )
    with pytest.raises(GatewayError, match="Invalid temperature"):
        _request_kwargs(ModelRequest(model="m", prompt="p", temperature=float("nan")))
    with pytest.raises(GatewayError, match="Invalid max output tokens"):
        _request_kwargs(ModelRequest(model="m", prompt="p", max_output_tokens=0))
    with pytest.raises(GatewayError, match="JSON schema must be a mapping"):
        _request_kwargs(ModelRequest(model="m", prompt="p", json_schema=42))


def test_gemini_client_disables_sdk_retries(monkeypatch):
    from palimpsest.factory.gateway import gemini

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False

        def close(self):
            self.closed = True

    clients = []
    gemini._reset_client()
    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: clients.append(FakeClient(**kwargs)) or clients[-1],
    )

    try:
        client = gemini._client()
        assert gemini._client() is client
        assert len(clients) == 1
        assert client.kwargs["http_options"].retry_options.attempts == 0
        gemini._reset_client()
        assert client.closed is True
    finally:
        gemini._reset_client()


def test_gemini_transport_retries_and_bills_thought_tokens(monkeypatch):
    from palimpsest.factory.gateway import ModelRequest, generate
    from palimpsest.factory.gateway import client as gateway_client
    from palimpsest.factory.gateway import gemini

    completed = SimpleNamespace(
        status="completed",
        output_text=" answer ",
        usage=SimpleNamespace(
            total_input_tokens=100,
            total_output_tokens=20,
            total_thought_tokens=30,
            total_tokens=150,
        ),
    )

    class FlakyInteractions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise httpx.ConnectError(
                    "connection reset",
                    request=httpx.Request("POST", "https://example.test"),
                )
            return completed

    interactions = FlakyInteractions()
    prices = []
    monkeypatch.setattr(gemini, "_interactions_client", lambda: interactions)
    monkeypatch.setattr(gateway_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        gemini,
        "estimate_cost",
        lambda model, tokens_in, tokens_out: (
            prices.append((model, tokens_in, tokens_out)) or 0.25
        ),
    )

    response = generate(ModelRequest(model="gemini-3.5-flash", prompt="p"))

    assert len(interactions.calls) == 2
    assert all(call["store"] is False for call in interactions.calls)
    assert response.text == "answer"
    assert response.output_tokens == 20
    assert response.thought_tokens == 30
    assert response.billable_output_tokens == 50
    assert response.total_tokens == 150
    assert response.cost_usd == 0.25
    assert prices == [("gemini-3.5-flash", 100, 50)]


@pytest.mark.parametrize(
    ("costs", "expected_cost"),
    [
        ((0.0, 0.0), 0.0),
        ((0.2, 0.3), 0.5),
        ((0.2, None), None),
    ],
)
def test_generate_json_aggregates_all_usage(monkeypatch, costs, expected_cost):
    from palimpsest.factory.gateway import ModelRequest
    from palimpsest.factory.gateway import client as gateway_client

    responses = iter(
        [
            ModelResponse(
                text="{",
                model="gemini-test",
                prompt_tokens=10,
                output_tokens=20,
                thought_tokens=30,
                total_tokens=60,
                cost_usd=costs[0],
            ),
            ModelResponse(
                text='{"answer": true}',
                model="gemini-test",
                finish_reason="done",
                prompt_tokens=1,
                output_tokens=2,
                thought_tokens=3,
                total_tokens=6,
                cost_usd=costs[1],
            ),
        ]
    )
    monkeypatch.setattr(gateway_client, "generate", lambda _request: next(responses))

    value, response = gateway_client.generate_json(
        ModelRequest(model="gemini-test", prompt="p"), attempts=2
    )

    assert value == {"answer": True}
    assert response.finish_reason == "done"
    assert response.prompt_tokens == 11
    assert response.output_tokens == 22
    assert response.thought_tokens == 33
    assert response.total_tokens == 66
    assert response.cost_usd == expected_cost


def test_generate_json_failure_retains_billed_attempts(monkeypatch):
    from palimpsest.factory.gateway import GatewayError, ModelRequest
    from palimpsest.factory.gateway import client as gateway_client

    responses = iter(
        [
            ModelResponse(
                text="{",
                model="gemini-test",
                prompt_tokens=10,
                output_tokens=20,
                thought_tokens=30,
                cost_usd=0.2,
            ),
            ModelResponse(
                text="still not json",
                model="gemini-test",
                prompt_tokens=1,
                output_tokens=2,
                thought_tokens=3,
                cost_usd=0.3,
            ),
        ]
    )
    monkeypatch.setattr(gateway_client, "generate", lambda _request: next(responses))

    with pytest.raises(GatewayError, match="unparseable JSON") as excinfo:
        gateway_client.generate_json(
            ModelRequest(model="gemini-test", prompt="p"), attempts=2
        )

    assert excinfo.value.tokens_in == 11
    assert excinfo.value.tokens_out == 55
    assert excinfo.value.cost_usd == 0.5


def test_generate_json_rejects_invalid_attempt_count(monkeypatch):
    from palimpsest.factory.gateway import GatewayError, ModelRequest
    from palimpsest.factory.gateway import client as gateway_client

    monkeypatch.setattr(
        gateway_client,
        "generate",
        lambda _request: pytest.fail("invalid configuration reached the provider"),
    )

    with pytest.raises(GatewayError, match="positive integer"):
        gateway_client.generate_json(
            ModelRequest(model="gemini-test", prompt="p"), attempts=0
        )


def test_gateway_retries_only_transient_failures(monkeypatch):
    from palimpsest.factory.gateway import GatewayError, ModelRequest
    from palimpsest.factory.gateway import client as gateway_client

    calls = []

    def permanent_failure(_request):
        calls.append(None)
        raise GatewayError("invalid request")

    monkeypatch.setattr(
        gateway_client, "_resolve_provider", lambda _model: permanent_failure
    )
    monkeypatch.setattr(
        gateway_client.time,
        "sleep",
        lambda _seconds: pytest.fail("permanent failure was retried"),
    )

    with pytest.raises(GatewayError, match="invalid request"):
        gateway_client.generate(ModelRequest(model="gemini-test", prompt="p"))
    assert len(calls) == 1


# --- read routing -------------------------------------------------------------


class RouteGateway:
    """Fake for read's generate_json: returns ({'transcription': ...}, response)."""

    def __init__(self, finish_reason=None, cost_usd=0.001):
        self.calls = []
        self.finish_reason = finish_reason
        self.cost_usd = cost_usd

    def __call__(self, request, **kwargs):
        self.calls.append(request)
        reason = None
        if self.finish_reason and not isinstance(request.images[0], ImageContent):
            reason = self.finish_reason  # only the full-page (Path) call truncates
        response = ModelResponse(
            text="",
            model=request.model,
            finish_reason=reason,
            prompt_tokens=10,
            output_tokens=5,
            cost_usd=self.cost_usd,
        )
        return {"transcription": f"text{len(self.calls)}"}, response


class ScriptedReadGateway:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        value = step["value"]
        return value, ModelResponse(
            text="",
            model=step.get("model", request.model),
            finish_reason=step.get("finish_reason"),
            prompt_tokens=step.get("tokens_in", 10),
            output_tokens=step.get("tokens_out", 5),
            cost_usd=step.get("cost_usd", 0.001),
        )


DUAL_PARAMS = {
    "secondary_model": "omp/gemini-3.6",
    "secondary_thinking_level": None,
    "adjudicator_model": "anthropic/claude-opus-4-6",
    "adjudicator_thinking_level": "high",
}


def _reading(text, **response):
    return {"value": {"transcription": text}, **response}


def _candidate_record(role, requested_model, text, *, model=None, raw_text=None):
    return {
        "role": role,
        "requested_model": requested_model,
        "model": model or requested_model,
        "raw_text": text if raw_text is None else raw_text,
        "text": text,
    }


def _judgment(text, reasoning="visible letterforms", unresolved=None, **response):
    return {
        "value": {
            "transcription": text,
            "reasoning": reasoning,
            "unresolved": unresolved or [],
        },
        **response,
    }


PROMPT = Prompt(name="read/la/diplomatic", text="Transcribe.", sha256="x" * 64)


def _regions_plan(job, route, regions):
    atomic_write_json(
        job.path_of("page_regions"),
        {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "route": route,
            "image": {"width": 600, "height": 800},
            "glyph_height_px": 14,
            "regions": regions,
        },
    )


def test_read_blank_route_spends_nothing(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _page())
    _regions_plan(job, "blank", [])
    fake = RouteGateway()
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)
    assert result.payload["text"] == ""
    assert fake.calls == []
    assert result.cost_usd == 0.0


def test_read_preserves_unknown_model_cost(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    fake = RouteGateway(cost_usd=None)
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.cost_usd is None


def test_read_segmented_one_call_per_region(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    regions = [
        {
            "region_id": "r00",
            "kind": "main_text",
            "bbox": [140, 90, 300, 200],
            "est_lines": 6,
            "reading_order": 0,
        },
        {
            "region_id": "r01",
            "kind": "marginalia",
            "bbox": [500, 90, 80, 60],
            "est_lines": 2,
            "reading_order": 1,
        },
    ]
    _regions_plan(job, "segmented", regions)
    fake = RouteGateway()
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)
    assert len(fake.calls) == 2
    assert all(isinstance(c.images[0], ImageContent) for c in fake.calls)
    assert result.payload["text"] == "text1\n\n[margin] text2"
    assert result.payload["regions"][1]["kind"] == "marginalia"
    assert result.tokens_out == 10  # summed across region calls


def test_read_full_page_escalates_on_truncation(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    regions = [
        {
            "region_id": "r00",
            "kind": "main_text",
            "bbox": [140, 90, 300, 200],
            "est_lines": 6,
            "reading_order": 0,
        }
    ]
    _regions_plan(job, "full_page", regions)
    fake = RouteGateway(finish_reason="FinishReason.MAX_TOKENS")
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)
    # 1 truncated full-page call + 1 region call
    assert len(fake.calls) == 2
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "text2"


def test_read_dual_exact_agreement_skips_adjudication(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    fake = ScriptedReadGateway(_reading("lectio"), _reading("lectio"))
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert [call.model for call in fake.calls] == [
        "fake-model",
        "omp/gemini-3.6",
    ]
    assert fake.calls[0].images == fake.calls[1].images
    assert result.payload["text"] == "lectio"
    assert result.payload["candidate_readings"] == [
        _candidate_record("primary", "fake-model", "lectio"),
        _candidate_record("secondary", "omp/gemini-3.6", "lectio"),
    ]
    assert result.payload["adjudication_status"] == "agreement"
    assert result.payload["adjudication_model"] is None
    assert result.payload["adjudication_requested_model"] == "anthropic/claude-opus-4-6"


def test_read_dual_disagreement_is_anonymously_adjudicated_and_usage_combined(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    fake = ScriptedReadGateway(
        _reading("zeta"), _reading("alpha"), _judgment("alpha", unresolved=["z/e"])
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 3
    judge = fake.calls[2]
    assert judge.model == "anthropic/claude-opus-4-6"
    assert judge.images == fake.calls[0].images == fake.calls[1].images
    assert "fake-model" not in judge.prompt
    assert "omp/gemini-3.6" not in judge.prompt
    assert '"candidate_a": "alpha"' in judge.prompt
    assert '"candidate_b": "zeta"' in judge.prompt
    assert "untrusted data" in judge.prompt
    assert "image is the sole authority" in judge.prompt
    assert judge.json_schema["additionalProperties"] is False
    assert result.payload["text"] == "alpha"
    assert result.payload["adjudication_status"] == "adjudicated"
    assert result.payload["adjudication_model"] == "anthropic/claude-opus-4-6"
    assert result.payload["adjudication_reasoning"] == "visible letterforms"
    assert result.payload["unresolved"] == ["z/e"]
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (30, 15, 0.003)


def test_read_dual_failed_adjudication_never_selects_a_candidate(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    failure = GatewayError(
        "judge unavailable", tokens_in=7, tokens_out=3, cost_usd=0.004
    )
    fake = ScriptedReadGateway(_reading("alpha"), _reading("beta"), failure)
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    with pytest.raises(GatewayError, match="judge unavailable") as caught:
        Read().run(job)

    assert len(fake.calls) == 3
    assert (
        caught.value.tokens_in,
        caught.value.tokens_out,
        caught.value.cost_usd,
    ) == (27, 13, 0.006)


def test_read_dual_segmented_failed_adjudication_is_an_auditable_hole(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    _regions_plan(
        job,
        "segmented",
        [
            {
                "region_id": "r00",
                "kind": "main_text",
                "bbox": [140, 90, 300, 200],
                "est_lines": 6,
                "reading_order": 0,
            }
        ],
    )
    failure = GatewayError(
        "judge unavailable", tokens_in=7, tokens_out=3, cost_usd=0.004
    )
    fake = ScriptedReadGateway(_reading("alpha"), _reading("beta"), failure)
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.payload["text"] == ""
    assert result.payload["adjudication_status"] == "failed"
    assert result.payload["adjudication_requested_model"] == "anthropic/claude-opus-4-6"
    assert result.payload["adjudication_model"] is None
    assert result.payload["adjudication_error"] == "judge unavailable"
    assert result.payload["regions"][0]["candidate_readings"] == [
        _candidate_record("primary", "fake-model", "alpha"),
        _candidate_record("secondary", "omp/gemini-3.6", "beta"),
    ]
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (27, 13, 0.006)


def test_read_dual_segmented_composes_both_candidates_with_region_audit(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    regions = [
        {
            "region_id": "r01",
            "kind": "marginalia",
            "bbox": [500, 90, 80, 60],
            "est_lines": 2,
            "reading_order": 1,
        },
        {
            "region_id": "r00",
            "kind": "main_text",
            "bbox": [140, 90, 300, 200],
            "est_lines": 6,
            "reading_order": 0,
        },
    ]
    _regions_plan(job, "segmented", regions)
    fake = ScriptedReadGateway(
        _reading("alpha"),
        _reading("alphi"),
        _judgment("alpha", reasoning="final stroke"),
        _reading("nota"),
        _reading("nota"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.payload["text"] == "alpha\n\n[margin] nota"
    assert result.payload["candidate_readings"] == [
        _candidate_record("primary", "fake-model", "alpha\n\n[margin] nota"),
        _candidate_record("secondary", "omp/gemini-3.6", "alphi\n\n[margin] nota"),
    ]
    assert result.payload["adjudication_status"] == "adjudicated"
    assert result.payload["adjudication_reasoning"] == "r00: final stroke"
    first, second = result.payload["regions"]
    assert first["region_id"] == "r00"
    assert first["adjudication_status"] == "adjudicated"
    assert first["adjudication_reasoning"] == "final stroke"
    assert second["region_id"] == "r01"
    assert second["adjudication_status"] == "agreement"
    assert fake.calls[0].images == fake.calls[1].images == fake.calls[2].images
    assert fake.calls[3].images == fake.calls[4].images


def test_read_dual_full_page_reader_truncation_escalates_to_tiles(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    _regions_plan(
        job,
        "full_page",
        [
            {
                "region_id": "r00",
                "kind": "main_text",
                "bbox": [140, 90, 300, 200],
                "est_lines": 6,
                "reading_order": 0,
            }
        ],
    )
    fake = ScriptedReadGateway(
        _reading("incomplete"),
        _reading("also incomplete", finish_reason="FinishReason.MAX_TOKENS"),
        _reading("complete"),
        _reading("complete"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 4
    assert not isinstance(fake.calls[0].images[0], ImageContent)
    assert not isinstance(fake.calls[1].images[0], ImageContent)
    assert isinstance(fake.calls[2].images[0], ImageContent)
    assert isinstance(fake.calls[3].images[0], ImageContent)
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "complete"
    assert result.payload["regions"][0]["adjudication_status"] == "agreement"
    assert (result.tokens_in, result.tokens_out) == (40, 20)


def test_read_dual_full_page_adjudicator_truncation_escalates_to_tiles(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    _regions_plan(
        job,
        "full_page",
        [
            {
                "region_id": "r00",
                "kind": "main_text",
                "bbox": [140, 90, 300, 200],
                "est_lines": 6,
                "reading_order": 0,
            }
        ],
    )
    fake = ScriptedReadGateway(
        _reading("alpha"),
        _reading("beta"),
        _judgment("alpha", finish_reason="length"),
        _reading("complete"),
        _reading("complete"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 5
    assert fake.calls[2].model == "anthropic/claude-opus-4-6"
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "complete"
    assert (result.tokens_in, result.tokens_out) == (50, 25)


def test_read_preserves_diplomatic_terminal_punctuation_and_raw_text(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    raw = "  lectio []{}|`_  "
    fake = ScriptedReadGateway(_reading(raw), _reading(raw))
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.payload["text"] == "lectio []{}|`_"
    assert result.payload["candidate_readings"] == [
        _candidate_record(
            "primary",
            "fake-model",
            "lectio []{}|`_",
            raw_text=raw,
        ),
        _candidate_record(
            "secondary",
            "omp/gemini-3.6",
            "lectio []{}|`_",
            raw_text=raw,
        ),
    ]
    assert fake.calls[0].json_schema["additionalProperties"] is False


def test_read_only_removes_observed_malformed_escaped_newline_tail(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    raw = " lectio/n_`} "
    fake = ScriptedReadGateway(_reading(raw))
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.payload["text"] == "lectio"
    assert result.payload["candidate_readings"] == [
        _candidate_record("primary", "fake-model", "lectio", raw_text=raw)
    ]


def test_terminal_punctuation_difference_cannot_create_false_agreement(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    fake = ScriptedReadGateway(
        _reading("lectio}"),
        _reading("lectio]"),
        _judgment("lectio}"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 3
    assert result.payload["adjudication_status"] == "adjudicated"
    assert [item["text"] for item in result.payload["candidate_readings"]] == [
        "lectio}",
        "lectio]",
    ]


def test_read_records_requested_and_resolved_model_ids(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    fake = ScriptedReadGateway(
        _reading("alpha", model="resolved-primary-v1"),
        _reading("beta", model="resolved-secondary-v2"),
        _judgment("alpha", model="resolved-adjudicator-v3"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert result.payload["candidate_readings"] == [
        _candidate_record(
            "primary",
            "fake-model",
            "alpha",
            model="resolved-primary-v1",
        ),
        _candidate_record(
            "secondary",
            "omp/gemini-3.6",
            "beta",
            model="resolved-secondary-v2",
        ),
    ]
    assert result.payload["adjudication_requested_model"] == "anthropic/claude-opus-4-6"
    assert result.payload["adjudication_model"] == "resolved-adjudicator-v3"


@pytest.mark.parametrize("failed_role", ["primary", "secondary"])
def test_read_dual_reader_failure_is_audited_and_retains_usage(
    tmp_path, monkeypatch, failed_role
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _page())
    _regions_plan(job, "full_page", [])
    failure = GatewayError(
        "reader unavailable", tokens_in=7, tokens_out=3, cost_usd=0.004
    )
    steps = (
        (failure, _reading("secondary"))
        if failed_role == "primary"
        else (_reading("primary"), failure)
    )
    fake = ScriptedReadGateway(*steps)
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    with pytest.raises(
        GatewayError, match=f"{failed_role} reader failed: reader unavailable"
    ) as caught:
        Read().run(job)

    assert len(fake.calls) == 2
    assert (
        caught.value.tokens_in,
        caught.value.tokens_out,
        caught.value.cost_usd,
    ) == (17, 8, 0.005)


def test_schema_invalid_truncated_reader_escalates_and_retains_usage(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    _regions_plan(
        job,
        "full_page",
        [
            {
                "region_id": "r00",
                "kind": "main_text",
                "bbox": [140, 90, 300, 200],
                "est_lines": 6,
                "reading_order": 0,
            }
        ],
    )
    truncated = GatewayError(
        "JSON response failed schema validation",
        tokens_in=11,
        tokens_out=6,
        cost_usd=0.004,
        finish_reason="MAX_TOKENS",
    )
    fake = ScriptedReadGateway(
        truncated,
        _reading("complete"),
        _reading("complete"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 3
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "complete"
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (31, 16, 0.006)


def test_schema_invalid_truncated_adjudication_escalates_and_retains_usage(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, prompt=PROMPT, params=DUAL_PARAMS)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    _regions_plan(
        job,
        "full_page",
        [
            {
                "region_id": "r00",
                "kind": "main_text",
                "bbox": [140, 90, 300, 200],
                "est_lines": 6,
                "reading_order": 0,
            }
        ],
    )
    truncated = GatewayError(
        "JSON response failed schema validation",
        tokens_in=11,
        tokens_out=6,
        cost_usd=0.004,
        finish_reason="INCOMPLETE",
    )
    fake = ScriptedReadGateway(
        _reading("alpha"),
        _reading("beta"),
        truncated,
        _reading("complete"),
        _reading("complete"),
    )
    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", fake)

    result = Read().run(job)

    assert len(fake.calls) == 5
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "complete"
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (51, 26, 0.008)


def test_read_rejects_identical_primary_and_secondary_selectors(tmp_path):
    job = _job(
        tmp_path,
        prompt=PROMPT,
        params={**DUAL_PARAMS, "secondary_model": "fake-model"},
    )
    _regions_plan(job, "blank", [])

    with pytest.raises(ValueError, match="must use different selectors"):
        Read().run(job)


def test_read_rejects_partial_dual_reader_configuration(tmp_path):
    job = _job(
        tmp_path,
        prompt=PROMPT,
        params={"secondary_model": "omp/gemini-3.6"},
    )
    _regions_plan(job, "blank", [])

    with pytest.raises(ValueError, match="must be configured together; missing:"):
        Read().run(job)
