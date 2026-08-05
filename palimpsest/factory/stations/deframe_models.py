"""Development-only model-backed variants of the deframe station."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.imaging import encode_jpeg
from palimpsest.factory.stations.deframe import Deframe
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import atomic_write_bytes


def _require_exact_options(
    options: Mapping[str, Any], expected: frozenset[str]
) -> None:
    actual = set(options)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"Expected options {sorted(expected)}; missing={missing}, unknown={unknown}"
        )


def _require_number(
    options: Mapping[str, Any],
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _require_positive_integer(options: Mapping[str, Any], name: str) -> int:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class ModelDeframe(Deframe):
    """Run a fixed local segmentation battery and apply its source-relative crop."""

    production_dependencies = (
        *Deframe.production_dependencies,
        "factory/stations/deframe.py",
        "factory/stations/deframe_model_runtime.py",
    )
    _runtime_root = Path(tempfile.gettempdir()) / "palimpsest-sam-bakeoff"
    _runtime_python = _runtime_root / "Scripts" / "python.exe"
    _runtime_script = Path(__file__).with_name("deframe_model_runtime.py")
    _checkpoint_name: str

    def _model_arguments(self, options: Mapping[str, Any]) -> list[str]:
        raise NotImplementedError

    def _model_bbox(self, job: Job) -> list[int]:
        if not self._runtime_python.is_file():
            raise FileNotFoundError(
                f"Missing isolated model runtime: {self._runtime_python}"
            )
        options = job.config.options
        source_path = job.path_of("page_image")
        checkpoint_path = self._runtime_root / self._checkpoint_name
        command = [
            str(self._runtime_python),
            str(self._runtime_script),
            *self._model_arguments(options),
            str(source_path),
            str(checkpoint_path),
            str(options["checkpoint_sha256"]),
            str(options["torch_version"]),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"{self.variant} inference failed: {detail}")
        output_lines = [
            line for line in completed.stdout.splitlines() if line.strip()
        ]
        if not output_lines:
            raise RuntimeError(f"{self.variant} inference returned no geometry")
        try:
            result = json.loads(output_lines[-1])
            bbox = result["bbox"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise RuntimeError(
                f"{self.variant} inference returned invalid geometry"
            ) from error
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(isinstance(value, int) for value in bbox)
        ):
            raise RuntimeError(f"{self.variant} inference returned an invalid bbox")
        return bbox

    def _crop_bbox(
        self,
        job: Job,
        image,
        bbox: list[int],
    ) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        x0, y0, x1, y1 = bbox
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise RuntimeError(
                f"{self.variant} bbox {bbox!r} is outside "
                f"source size {(width, height)!r}"
            )
        margin_fraction = float(job.config.options["crop_margin"])
        margin_x = round(width * margin_fraction)
        margin_y = round(height * margin_fraction)
        return (
            max(0, x0 - margin_x),
            max(0, y0 - margin_y),
            min(width, x1 + margin_x),
            min(height, y1 + margin_y),
        )

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image")
        x0, y0, x1, y1 = self._crop_bbox(job, image, self._model_bbox(job))
        atomic_write_bytes(
            self.output_path(job),
            encode_jpeg(image[y0:y1, x0:x1]),
        )
        return StationResult(cost_usd=0.0)


class FastSamDeframe(ModelDeframe):
    variant = "fastsam-s/v1"
    _checkpoint_name = "FastSAM-s.pt"
    option_keys = frozenset(
        {
            "checkpoint_sha256",
            "torch_version",
            "runtime_version",
            "image_size",
            "confidence",
            "iou",
            "min_box_fraction",
            "min_box_height_fraction",
            "crop_margin",
        }
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _require_exact_options(options, self.option_keys)
        for name in ("checkpoint_sha256", "torch_version", "runtime_version"):
            if not isinstance(options[name], str) or not options[name]:
                raise TypeError(f"{name} must be a non-empty string")
        if len(options["checkpoint_sha256"]) != 64:
            raise ValueError("checkpoint_sha256 must have 64 hexadecimal characters")
        _require_positive_integer(options, "image_size")
        _require_number(options, "confidence", minimum=0.0, maximum=1.0)
        _require_number(options, "iou", minimum=0.0, maximum=1.0)
        _require_number(options, "min_box_fraction", minimum=0.0, maximum=1.0)
        _require_number(options, "min_box_height_fraction", minimum=0.0, maximum=1.0)
        _require_number(options, "crop_margin", minimum=0.0, maximum=0.1)

    def _model_arguments(self, options: Mapping[str, Any]) -> list[str]:
        return [
            "fastsam-s",
            "--runtime-version",
            str(options["runtime_version"]),
            "--image-size",
            str(options["image_size"]),
            "--confidence",
            str(options["confidence"]),
            "--iou",
            str(options["iou"]),
            "--min-box-fraction",
            str(options["min_box_fraction"]),
            "--min-box-height-fraction",
            str(options["min_box_height_fraction"]),
        ]


class EfficientVitSamDeframe(ModelDeframe):
    variant = "efficientvit-sam-l0/v1"
    _checkpoint_name = "efficientvit-sam-l0.pt"
    _source_root = Path(tempfile.gettempdir()) / "efficientvit-796cb9f"
    option_keys = frozenset(
        {
            "checkpoint_sha256",
            "torch_version",
            "source_revision",
            "points_per_side",
            "points_per_batch",
            "predicted_iou_threshold",
            "stability_threshold",
            "box_nms_threshold",
            "min_box_fraction",
            "min_mask_fraction",
            "min_box_height_fraction",
            "crop_margin",
        }
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _require_exact_options(options, self.option_keys)
        for name in ("checkpoint_sha256", "torch_version", "source_revision"):
            if not isinstance(options[name], str) or not options[name]:
                raise TypeError(f"{name} must be a non-empty string")
        if len(options["checkpoint_sha256"]) != 64:
            raise ValueError("checkpoint_sha256 must have 64 hexadecimal characters")
        if len(options["source_revision"]) != 40:
            raise ValueError("source_revision must be a full Git commit")
        _require_positive_integer(options, "points_per_side")
        _require_positive_integer(options, "points_per_batch")
        _require_number(options, "predicted_iou_threshold", minimum=0.0, maximum=1.0)
        _require_number(options, "stability_threshold", minimum=0.0, maximum=1.0)
        _require_number(options, "box_nms_threshold", minimum=0.0, maximum=1.0)
        _require_number(options, "min_box_fraction", minimum=0.0, maximum=1.0)
        _require_number(options, "min_mask_fraction", minimum=0.0, maximum=1.0)
        _require_number(options, "min_box_height_fraction", minimum=0.0, maximum=1.0)
        _require_number(options, "crop_margin", minimum=0.0, maximum=0.1)

    def _model_arguments(self, options: Mapping[str, Any]) -> list[str]:
        return [
            "efficientvit-sam-l0",
            "--efficientvit-source",
            str(self._source_root),
            "--source-revision",
            str(options["source_revision"]),
            "--points-per-side",
            str(options["points_per_side"]),
            "--points-per-batch",
            str(options["points_per_batch"]),
            "--predicted-iou-threshold",
            str(options["predicted_iou_threshold"]),
            "--stability-threshold",
            str(options["stability_threshold"]),
            "--box-nms-threshold",
            str(options["box_nms_threshold"]),
            "--min-box-fraction",
            str(options["min_box_fraction"]),
            "--min-mask-fraction",
            str(options["min_mask_fraction"]),
            "--min-box-height-fraction",
            str(options["min_box_height_fraction"]),
        ]


register(FastSamDeframe())
register(EfficientVitSamDeframe())
