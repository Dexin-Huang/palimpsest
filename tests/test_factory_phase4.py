"""Phase 4 tests: CV imaging primitives, segment station, read v2 routing."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from palimpsest.factory import imaging
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway.client import ImageContent, ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.read import Read
from palimpsest.factory.stations.segment import Segment
from palimpsest.factory.workspace.io import atomic_write_json, read_json


def _page(height=800, width=600, bg=235):
    return np.full((height, width, 3), bg, np.uint8)


def _text_block(page, x, y, lines, line_w=250, glyph_h=8, gap=12, shade=30):
    for row in range(lines):
        y0 = y + row * (glyph_h + gap)
        cv2.rectangle(page, (x, y0), (x + line_w, y0 + glyph_h), (shade,) * 3, -1)
    return page


def _job(tmp_path, page_id="f001r", options=None, prompt=None):
    doc_dir = tmp_path / "doc1"
    doc_dir.mkdir(exist_ok=True)
    pages = ({"page_id": page_id, "order": 1},)
    return Job(
        doc_id="doc1", pages=pages, page=pages[0], library_root=tmp_path,
        config=StationConfig(
            model="fake-model", prompt=prompt, options=options or {},
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
    assert gray[220, 300] > 225          # top of watermark ring painted away
    assert gray[400, 120] > 225          # left of ring painted away
    assert gray[60, 92] < 220            # faint small mark survives
    assert gray[706, 200] < 60           # dark ink untouched


def test_ink_masks_split_dark_and_faint():
    page = _page()
    cv2.rectangle(page, (100, 100), (300, 114), (30,) * 3, -1)  # dark line
    cv2.line(page, (100, 200), (125, 200), (205,) * 3, 2)       # faint small stroke
    dark, faint = imaging.ink_masks(imaging.to_gray(page))
    assert dark[107, 200] == 255 and faint[107, 200] == 0
    assert faint[200, 110] == 255 and dark[200, 110] == 0


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
    _text_block(page, 150, 60, lines=30, line_w=350)   # tall main column
    _text_block(page, 620, 300, lines=3, line_w=50)    # margin note right edge
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
    _text_block(page, 100, 100, lines=8, line_w=300, shade=30)    # real ink
    _text_block(page, 100, 450, lines=6, line_w=300, shade=150)   # bleed-depth
    job = _job(tmp_path)
    _write_clean_image(job, page)
    payload = Segment().run(job).payload

    tops = [r["bbox"][1] for r in payload["regions"]]
    assert any(y < 400 for y in tops)        # real block survives
    assert not any(y >= 400 for y in tops)   # shallow block dropped


# --- gateway config mapping ----------------------------------------------------

def test_gemini_config_maps_json_schema():
    from palimpsest.factory.gateway.gemini import _config_kwargs
    from palimpsest.factory.gateway.client import ModelRequest

    schema = {"type": "object", "properties": {"a": {"type": "string"}},
              "required": ["a"]}
    kwargs = _config_kwargs(ModelRequest(
        model="m", prompt="p", json_output=True, json_schema=schema))
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_json_schema"] == schema
    assert "response_schema" not in kwargs  # mutually exclusive on the backend

    plain = _config_kwargs(ModelRequest(model="m", prompt="p", json_output=True))
    assert plain["response_mime_type"] == "application/json"
    assert "response_json_schema" not in plain


# --- read v2 routing ----------------------------------------------------------

class RouteGateway:
    def __init__(self, finish_reason=None):
        self.calls = []
        self.finish_reason = finish_reason

    def __call__(self, request):
        self.calls.append(request)
        reason = None
        if self.finish_reason and not isinstance(request.images[0], ImageContent):
            reason = self.finish_reason  # only the full-page (Path) call truncates
        return ModelResponse(
            text=f"text{len(self.calls)}", model=request.model,
            finish_reason=reason, prompt_tokens=10, output_tokens=5, cost_usd=0.001,
        )


PROMPT = Prompt(name="read/la/diplomatic", text="Transcribe.", sha256="x" * 64)


def _regions_plan(job, route, regions):
    atomic_write_json(job.path_of("page_regions"), {
        "doc_id": job.doc_id, "page_id": job.page_id, "route": route,
        "image": {"width": 600, "height": 800}, "glyph_height_px": 14,
        "regions": regions,
    })


def test_read_blank_route_spends_nothing(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _page())
    _regions_plan(job, "blank", [])
    fake = RouteGateway()
    monkeypatch.setattr("palimpsest.factory.stations.read.generate", fake)

    result = Read().run(job)
    assert result.payload["text"] == ""
    assert fake.calls == []
    assert result.cost_usd is None


def test_read_segmented_one_call_per_region(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    regions = [
        {"region_id": "r00", "kind": "main_text", "bbox": [140, 90, 300, 200],
         "est_lines": 6, "reading_order": 0},
        {"region_id": "r01", "kind": "marginalia", "bbox": [500, 90, 80, 60],
         "est_lines": 2, "reading_order": 1},
    ]
    _regions_plan(job, "segmented", regions)
    fake = RouteGateway()
    monkeypatch.setattr("palimpsest.factory.stations.read.generate", fake)

    result = Read().run(job)
    assert len(fake.calls) == 2
    assert all(isinstance(c.images[0], ImageContent) for c in fake.calls)
    assert result.payload["text"] == "text1\n\n[margin] text2"
    assert result.payload["regions"][1]["kind"] == "marginalia"
    assert result.tokens_out == 10  # summed across region calls


def test_read_full_page_escalates_on_truncation(tmp_path, monkeypatch):
    job = _job(tmp_path, prompt=PROMPT)
    _write_clean_image(job, _text_block(_page(), 150, 100, lines=6))
    regions = [{"region_id": "r00", "kind": "main_text", "bbox": [140, 90, 300, 200],
                "est_lines": 6, "reading_order": 0}]
    _regions_plan(job, "full_page", regions)
    fake = RouteGateway(finish_reason="FinishReason.MAX_TOKENS")
    monkeypatch.setattr("palimpsest.factory.stations.read.generate", fake)

    result = Read().run(job)
    # 1 truncated full-page call + 1 region call
    assert len(fake.calls) == 2
    assert result.payload["route"] == "segmented(escalated)"
    assert result.payload["text"] == "text2"
