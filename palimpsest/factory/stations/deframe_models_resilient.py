"""Failure-resilient EfficientViT hybrid deframe variant."""

from __future__ import annotations

import subprocess

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.imaging import (
    encode_jpeg,
    parchment_spread_frame,
    to_gray,
)
from palimpsest.factory.stations.deframe_models_hybrid import (
    SizeAwareHybridEfficientVitSamDeframe,
)
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import atomic_write_bytes


class ResilientHybridEfficientVitSamDeframe(SizeAwareHybridEfficientVitSamDeframe):
    """Use retention-first deterministic geometry when model inference fails."""

    variant = "efficientvit-sam-l0-resilient-hybrid/v1"
    production_dependencies = (
        *SizeAwareHybridEfficientVitSamDeframe.production_dependencies,
        "factory/stations/deframe_models_hybrid.py",
    )

    def run(self, job: Job) -> StationResult:
        try:
            return super().run(job)
        except subprocess.TimeoutExpired:
            return self._run_retention_fallback(job)
        except RuntimeError as error:
            if not self._is_model_inference_failure(error):
                raise
            return self._run_retention_fallback(job)

    def _is_model_inference_failure(self, error: RuntimeError) -> bool:
        message = str(error)
        return message.startswith(f"{self.variant} inference ") or (
            message.startswith(f"{self.variant} bbox ")
            and "outside source size" in message
        )

    def _run_retention_fallback(self, job: Job) -> StationResult:
        image = load_image(job, "page_image")
        gray = to_gray(image)
        x0, y0, x1, y1 = parchment_spread_frame(
            gray,
            margin_fraction=float(job.config.options["frame_margin"]),
        )
        atomic_write_bytes(
            self.output_path(job),
            encode_jpeg(image[y0:y1, x0:x1]),
        )
        return StationResult(cost_usd=0.0)


register(ResilientHybridEfficientVitSamDeframe())
