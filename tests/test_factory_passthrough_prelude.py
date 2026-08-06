"""Passthrough prelude variants: byte-identical image stages plus the no-CV
full-page segment plan that hands the instrumented rig raw archive bytes."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.stations.deframe import PassthroughDeframe
from palimpsest.factory.stations.dewatermark import PassthroughDewatermark
from palimpsest.factory.stations.flatten import PassthroughFlatten
from palimpsest.factory.stations.segment import PassthroughSegment


def _job(tmp_path):
    pages = ({"page_id": "f001r", "order": 1},)
    return Job(
        doc_id="doc1",
        pages=pages,
        page=pages[0],
        library_root=tmp_path,
        config=StationConfig(),
    )


def _jpeg_bytes(height=80, width=60):
    page = np.full((height, width, 3), 235, np.uint8)
    cv2.rectangle(page, (10, 20), (50, 32), (30,) * 3, -1)
    ok, buffer = cv2.imencode(".jpg", page)
    assert ok
    return buffer.tobytes()


def _stage(job, kind, data):
    path = job.path_of(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.mark.parametrize(
    ("station", "input_kind", "output_kind"),
    [
        (PassthroughDeframe(), "page_image", "page_image_framed"),
        (PassthroughDewatermark(), "page_image_framed", "page_image_unmarked"),
        (PassthroughFlatten(), "page_image_unmarked", "page_image_clean"),
    ],
    ids=["deframe", "dewatermark", "flatten"],
)
def test_image_passthrough_output_is_byte_identical(
    tmp_path, station, input_kind, output_kind
):
    job = _job(tmp_path)
    data = _jpeg_bytes()
    _stage(job, input_kind, data)

    station.run(job)

    assert job.path_of(output_kind).read_bytes() == data


def test_segment_passthrough_payload_satisfies_page_regions_contract(tmp_path):
    job = _job(tmp_path)
    _stage(job, "page_image_clean", _jpeg_bytes(height=80, width=60))

    result = PassthroughSegment().run(job)

    validate_payload("page_regions", result.payload, expected_doc_id="doc1")
    assert result.payload["route"] == "full_page"
    assert result.payload["regions"] == []
    assert result.payload["image"] == {"width": 60, "height": 80}


def test_registry_resolves_every_passthrough_variant():
    resolved = {
        name: registry.get(name, "passthrough/v1")
        for name in ("deframe", "dewatermark", "flatten", "segment")
    }
    assert isinstance(resolved["deframe"], PassthroughDeframe)
    assert isinstance(resolved["dewatermark"], PassthroughDewatermark)
    assert isinstance(resolved["flatten"], PassthroughFlatten)
    assert isinstance(resolved["segment"], PassthroughSegment)
    for station in resolved.values():
        assert station.variant == "passthrough/v1"
