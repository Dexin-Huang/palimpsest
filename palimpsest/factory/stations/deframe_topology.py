"""Topology-aware deterministic deframe variant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import cv2
import numpy as np

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.imaging import (
    encode_jpeg,
    ink_masks,
    parchment_frame,
    parchment_spread_frame,
    to_gray,
)
from palimpsest.factory.stations.deframe import Deframe
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import atomic_write_bytes


def _require_fraction(options: Mapping[str, Any], name: str) -> float:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")
    return result


def _ink_fraction(mask: np.ndarray) -> float:
    if mask.size == 0:
        return 0.0
    return cv2.countNonZero(mask) / mask.size


def _guarded_bbox(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
    marks: np.ndarray,
    *,
    minimum_ink_fraction: float,
) -> tuple[int, int, int, int]:
    """Keep an outer edge when its inset band contains substantial writing."""

    outer_x0, outer_y0, outer_x1, outer_y1 = outer
    inner_x0, inner_y0, inner_x1, inner_y1 = inner
    edge_ink = (
        _ink_fraction(marks[outer_y0:outer_y1, outer_x0:inner_x0]),
        _ink_fraction(marks[outer_y0:inner_y0, outer_x0:outer_x1]),
        _ink_fraction(marks[outer_y0:outer_y1, inner_x1:outer_x1]),
        _ink_fraction(marks[inner_y1:outer_y1, outer_x0:outer_x1]),
    )
    return (
        outer_x0 if edge_ink[0] >= minimum_ink_fraction else inner_x0,
        outer_y0 if edge_ink[1] >= minimum_ink_fraction else inner_y0,
        outer_x1 if edge_ink[2] >= minimum_ink_fraction else inner_x1,
        outer_y1 if edge_ink[3] >= minimum_ink_fraction else inner_y1,
    )


class TopologyAwareDeframe(Deframe):
    """Select one leaf or a complete multi-span envelope, guarding edge marks."""

    variant = "topology-aware/v1"
    option_keys = frozenset(
        {
            "frame_margin",
            "multi_span_aspect_ratio",
            "protected_band_ink_fraction",
            "topology_edge_disagreement",
        }
    )
    production_dependencies = (
        *Deframe.production_dependencies,
        "factory/stations/deframe.py",
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        actual = set(options)
        if actual != self.option_keys:
            missing = sorted(self.option_keys - actual)
            unknown = sorted(actual - self.option_keys)
            raise ValueError(
                f"Expected options {sorted(self.option_keys)}; "
                f"missing={missing}, unknown={unknown}"
            )
        frame_margin = _require_fraction(options, "frame_margin")
        if frame_margin > 0.1:
            raise ValueError("frame_margin must be between 0.0 and 0.1")
        _require_fraction(options, "protected_band_ink_fraction")
        _require_fraction(options, "topology_edge_disagreement")
        aspect_ratio = options["multi_span_aspect_ratio"]
        if isinstance(aspect_ratio, bool) or not isinstance(aspect_ratio, int | float):
            raise TypeError("multi_span_aspect_ratio must be a number")
        if float(aspect_ratio) < 1.0:
            raise ValueError("multi_span_aspect_ratio must be at least 1.0")

    def _crop_bbox(
        self,
        gray: np.ndarray,
        options: Mapping[str, Any],
    ) -> tuple[int, int, int, int]:
        height, width = gray.shape
        frame_margin = float(options["frame_margin"])
        largest_outer = tuple(map(int, parchment_frame(gray, margin_fraction=0.0)))
        largest_inner = tuple(
            map(int, parchment_frame(gray, margin_fraction=frame_margin))
        )
        envelope_outer = tuple(
            map(int, parchment_spread_frame(gray, margin_fraction=0.0))
        )
        envelope_inner = tuple(
            map(int, parchment_spread_frame(gray, margin_fraction=frame_margin))
        )
        disagreement = max(
            abs(largest_outer[0] - envelope_outer[0]) / width,
            abs(largest_outer[1] - envelope_outer[1]) / height,
            abs(largest_outer[2] - envelope_outer[2]) / width,
            abs(largest_outer[3] - envelope_outer[3]) / height,
        )
        aspect_ratio = max(width, height) / min(width, height)
        multi_span = aspect_ratio >= float(
            options["multi_span_aspect_ratio"]
        ) or disagreement > float(options["topology_edge_disagreement"])
        outer, inner = (
            (envelope_outer, envelope_inner)
            if multi_span
            else (largest_outer, largest_inner)
        )
        dark, faint = ink_masks(gray)
        bbox = _guarded_bbox(
            outer,
            inner,
            cv2.bitwise_or(dark, faint),
            minimum_ink_fraction=float(options["protected_band_ink_fraction"]),
        )
        x0, y0, x1, y1 = bbox
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise RuntimeError(
                f"{self.variant} produced invalid crop {bbox!r} "
                f"for source size {(width, height)!r}"
            )
        return bbox

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image")
        x0, y0, x1, y1 = self._crop_bbox(
            to_gray(image),
            job.config.options,
        )
        atomic_write_bytes(
            self.output_path(job),
            encode_jpeg(image[y0:y1, x0:x1]),
        )
        return StationResult()


register(TopologyAwareDeframe())
