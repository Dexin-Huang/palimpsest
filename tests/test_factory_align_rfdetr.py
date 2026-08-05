"""Same-socket RF-DETR alignment variant behavior and boundaries."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.stations.align import Align
from palimpsest.factory.stations import align_rfdetr_runtime
from palimpsest.factory.stations.align_rfdetr import (
    RfDetrAlign,
    _request_worker,
    align_detections,
)


CANDIDATE = (
    Path(__file__).parents[1]
    / "palimpsest"
    / "factory"
    / "candidates"
    / "align"
    / "rfdetr-mth600-development-v3.yaml"
)


def _options() -> dict[str, object]:
    return {
        "checkpoint_sha256": "c" * 64,
        "rfdetr_version": "1.8.3",
        "torch_version": "2.7.0+cu118",
        "torchvision_version": "0.22.0+cu118",
        "tile_size": 512,
        "overlap": 96,
        "threshold": 0.31,
        "nms_iou": 0.4,
    }


def _job(tmp_path: Path, *, text: str = "甲乙\n丙丁") -> Job:
    pages = ({"page_id": "p01", "order": 1},)
    job = Job(
        doc_id="align-rfdetr-test",
        pages=pages,
        page=pages[0],
        library_root=tmp_path,
        config=StationConfig(options=_options()),
    )
    image_path = job.path_of("page_image_clean")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((100, 180, 3), 240, np.uint8)
    encoded, buffer = cv2.imencode(".jpg", image)
    assert encoded
    image_path.write_bytes(buffer.tobytes())
    transcription_path = job.path_of("page_transcription")
    transcription_path.parent.mkdir(parents=True, exist_ok=True)
    transcription_path.write_text(json.dumps({"text": text}), encoding="utf-8")
    return job


def _boxes() -> list[dict[str, object]]:
    return [
        {"bbox": [150, 10, 10, 10], "score": 0.95},
        {"bbox": [100, 10, 10, 10], "score": 0.99},
        {"bbox": [100, 30, 10, 10], "score": 0.98},
        {"bbox": [50, 10, 10, 10], "score": 0.97},
        {"bbox": [50, 30, 10, 10], "score": 0.96},
    ]


def _inference() -> dict[str, object]:
    return {
        "boxes": _boxes(),
        "image_size": [180, 100],
        "model_load_seconds": 0.1,
        "inference_seconds": 0.2,
        "peak_vram_bytes": 1024,
    }


def test_rfdetr_align_is_registered_as_a_distinct_same_socket_variant():
    baseline = Align()
    challenger = registry.get("align", "rfdetr-mth600/v1")

    assert isinstance(challenger, RfDetrAlign)
    assert challenger.socket == baseline.socket
    assert challenger.implementation_fingerprint != baseline.implementation_fingerprint
    assert {path.name for path in challenger.production_source_paths} >= {
        "align.py",
        "align_rfdetr.py",
        "align_rfdetr_runtime.py",
        "glyphs.py",
    }


def test_rfdetr_candidate_resolves_fixed_checkpoint_options():
    candidate = load_candidate(CANDIDATE)

    assert candidate.id == "align/rfdetr-mth600-development-v3"
    assert candidate.variant == "rfdetr-mth600/v1"
    assert candidate.options["checkpoint_sha256"] == (
        "cdc06d36dd2273e139571b3196d58c13dee11211ec847fadffa9fee3af46624d"
    )
    assert (
        candidate.fingerprint
        != load_candidate(CANDIDATE.with_name("current.yaml")).fingerprint
    )


def test_rfdetr_options_reject_unknown_and_invalid_values():
    station = RfDetrAlign()
    station.validate_options(_options())

    with pytest.raises(ValueError, match=r"unknown=\['extra'\]"):
        station.validate_options({**_options(), "extra": True})
    with pytest.raises(ValueError, match="overlap must be smaller"):
        station.validate_options({**_options(), "overlap": 512})
    with pytest.raises(ValueError, match="64 hexadecimal"):
        station.validate_options({**_options(), "checkpoint_sha256": "z" * 64})


def test_align_detections_skips_spurious_column_without_shifting_text():
    result = align_detections(
        "甲乙\n丙丁",
        _boxes(),
        image_width=180,
        image_height=100,
    )

    columns = result["columns"]
    assert [[char["ch"] for char in column["chars"]] for column in columns] == [
        ["甲", "乙"],
        ["丙", "丁"],
    ]
    assert [column["bbox"] for column in columns] == [
        [100, 10, 10, 30],
        [50, 10, 10, 30],
    ]
    assert all(
        char["method"] == "rfdetr" for column in columns for char in column["chars"]
    )
    assert result["stats"] == {
        "transcribed": 4,
        "boxed": 4,
        "count_mismatch_columns": 0,
        "image_columns": 3,
        "small_blobs_unassigned": 0,
        "detected_boxes": 5,
        "skipped_detection_columns": 1,
        "region_count": 0,
        "unassigned_detections": 0,
    }


def test_align_detections_uses_transcription_regions_as_reading_order():
    result = align_detections(
        "甲乙\n丙丁",
        [
            {"bbox": [100, 10, 10, 10], "score": 0.99},
            {"bbox": [100, 30, 10, 10], "score": 0.98},
            {"bbox": [50, 10, 10, 10], "score": 0.97},
            {"bbox": [50, 30, 10, 10], "score": 0.96},
        ],
        image_width=180,
        image_height=100,
        regions=[
            {"bbox": [45, 5, 20, 40], "text": "甲乙"},
            {"bbox": [95, 5, 20, 40], "text": "丙丁"},
        ],
    )

    assert [column["bbox"] for column in result["columns"]] == [
        [50, 10, 10, 30],
        [100, 10, 10, 30],
    ]
    assert result["stats"]["region_count"] == 2
    assert result["stats"]["unassigned_detections"] == 0


def test_align_detections_rejects_regions_that_do_not_reproduce_page_text():
    with pytest.raises(RuntimeError, match="regions do not reproduce"):
        align_detections(
            "甲乙",
            [{"bbox": [50, 10, 10, 10], "score": 0.97}],
            image_width=180,
            image_height=100,
            regions=[{"bbox": [45, 5, 20, 40], "text": "甲"}],
        )


def test_align_detections_keeps_overlapping_wide_glyphs_in_adjacent_columns():
    result = align_detections(
        "甲\n乙",
        [
            {"bbox": [100, 10, 70, 20], "score": 0.99},
            {"bbox": [50, 10, 70, 20], "score": 0.98},
        ],
        image_width=200,
        image_height=100,
    )

    assert [column["bbox"] for column in result["columns"]] == [
        [100, 10, 70, 20],
        [50, 10, 70, 20],
    ]
    assert result["stats"]["image_columns"] == 2


def test_align_detections_never_fabricates_missing_coordinates():
    result = align_detections(
        "甲乙",
        [{"bbox": [100, 10, 10, 10], "score": 0.99}],
        image_width=180,
        image_height=100,
    )

    chars = result["columns"][0]["chars"]
    assert [char["ch"] for char in chars] == ["甲", "乙"]
    assert sum(char["bbox"] is None for char in chars) == 1
    assert next(char for char in chars if char["bbox"] is None)["method"] == "none"


def test_align_detections_rejects_out_of_bounds_model_geometry():
    with pytest.raises(RuntimeError, match="outside the source image"):
        align_detections(
            "甲",
            [{"bbox": [175, 10, 10, 10], "score": 0.99}],
            image_width=180,
            image_height=100,
        )


def test_rfdetr_station_emits_existing_page_alignment_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job = _job(tmp_path)
    station = RfDetrAlign()
    monkeypatch.setattr(station, "_predict", lambda _: _inference())

    result = station.run(job)

    assert result.cost_usd == 0.0
    assert result.payload is not None
    assert result.payload["doc_id"] == job.doc_id
    assert result.payload["page_id"] == "p01"
    assert result.payload["stats"]["boxed"] == 4
    assert [
        char["ch"] for column in result.payload["columns"] for char in column["chars"]
    ] == ["甲", "乙", "丙", "丁"]


def test_rfdetr_station_skips_model_for_empty_transcription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job = _job(tmp_path, text="\n")
    station = RfDetrAlign()

    def reject_predict(_: Job) -> dict[str, object]:
        raise AssertionError("blank transcription dispatched RF-DETR")

    monkeypatch.setattr(station, "_predict", reject_predict)

    result = station.run(job)

    assert result.payload is not None
    assert result.payload["columns"] == []
    assert result.payload["stats"]["detected_boxes"] == 0


def test_rfdetr_station_fails_before_dispatch_when_runtime_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    job = _job(tmp_path)
    station = RfDetrAlign()
    missing = tmp_path / "missing-python.exe"
    monkeypatch.setenv("PALIMPSEST_RFDETR_PYTHON", str(missing))

    with pytest.raises(FileNotFoundError, match="Missing isolated RF-DETR runtime"):
        station._predict(job)


def test_rfdetr_runtime_worker_reuses_one_model_across_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    state_path = tmp_path / "worker.json"
    calls = {"loads": 0, "predictions": 0}

    def fake_load(_):
        calls["loads"] += 1
        return object(), 0.25

    def fake_predict(_, *, detector, model_load_seconds, source):
        assert detector is not None
        assert model_load_seconds == 0.25
        calls["predictions"] += 1
        return {
            "boxes": [],
            "image_size": [10, 10],
            "model_load_seconds": model_load_seconds,
            "inference_seconds": 0.01,
            "peak_vram_bytes": 100,
            "source_name": source.name,
        }

    monkeypatch.setattr(align_rfdetr_runtime, "_load_detector", fake_load)
    monkeypatch.setattr(align_rfdetr_runtime, "_predict", fake_predict)
    args = SimpleNamespace(
        state_path=state_path,
        token="worker-token",
        idle_timeout=1,
    )
    failures: list[BaseException] = []

    def serve():
        try:
            align_rfdetr_runtime._serve(args)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=serve)
    worker.start()
    deadline = time.monotonic() + 2
    while not state_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert state_path.is_file()

    first = _request_worker(state_path, tmp_path / "page-1.jpg")
    second = _request_worker(state_path, tmp_path / "page-2.jpg")
    worker.join(timeout=4)

    assert not worker.is_alive()
    assert failures == []
    assert first["source_name"] == "page-1.jpg"
    assert second["source_name"] == "page-2.jpg"
    assert calls == {"loads": 1, "predictions": 2}
