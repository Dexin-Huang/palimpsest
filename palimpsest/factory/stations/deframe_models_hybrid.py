"""Deterministic/model edge-fusion variant of the deframe station."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job
from palimpsest.factory.imaging import parchment_frame, to_gray, trim_gutter
from palimpsest.factory.stations.deframe_models import (
    _require_number,
    _require_positive_integer,
)
from palimpsest.factory.stations.deframe_models_adaptive import (
    AdaptiveEfficientVitSamDeframe,
)


class HybridEfficientVitSamDeframe(AdaptiveEfficientVitSamDeframe):
    """Blend default and EfficientViT crop edges in source coordinates."""

    variant = "efficientvit-sam-l0-hybrid/v1"
    _hybrid_option_keys = frozenset(
        {
            "frame_margin",
            "left_model_weight",
            "top_model_weight",
            "right_model_weight",
            "bottom_model_weight",
        }
    )
    option_keys = AdaptiveEfficientVitSamDeframe.option_keys | _hybrid_option_keys
    production_dependencies = (
        *AdaptiveEfficientVitSamDeframe.production_dependencies,
        "factory/stations/deframe_models_adaptive.py",
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
        model_options = {
            key: value
            for key, value in options.items()
            if key not in self._hybrid_option_keys
        }
        AdaptiveEfficientVitSamDeframe().validate_options(model_options)
        _require_number(options, "frame_margin", minimum=0.0, maximum=0.1)
        for name in (
            "left_model_weight",
            "top_model_weight",
            "right_model_weight",
            "bottom_model_weight",
        ):
            _require_number(options, name, minimum=0.0, maximum=1.0)

    def _model_weights(self, job: Job, image) -> tuple[float, float, float, float]:
        return tuple(
            float(job.config.options[name])
            for name in (
                "left_model_weight",
                "top_model_weight",
                "right_model_weight",
                "bottom_model_weight",
            )
        )

    def _crop_bbox(
        self,
        job: Job,
        image,
        bbox: list[int],
    ) -> tuple[int, int, int, int]:
        model_bbox = super()._crop_bbox(job, image, bbox)
        gray = to_gray(image)
        x0, y0, x1, y1 = parchment_frame(
            gray,
            margin_fraction=float(job.config.options["frame_margin"]),
        )
        gutter_x0, gutter_x1 = trim_gutter(gray[y0:y1, x0:x1])
        default_bbox = (x0 + gutter_x0, y0, x0 + gutter_x1, y1)
        weights = self._model_weights(job, image)
        fused_bbox = tuple(
            round(default + weight * (model - default))
            for default, model, weight in zip(
                default_bbox,
                model_bbox,
                weights,
                strict=True,
            )
        )
        fused_x0, fused_y0, fused_x1, fused_y1 = fused_bbox
        if fused_x0 >= fused_x1 or fused_y0 >= fused_y1:
            raise RuntimeError(
                f"{self.variant} produced invalid fused bbox {fused_bbox!r}"
            )
        return fused_bbox


class SizeAwareHybridEfficientVitSamDeframe(HybridEfficientVitSamDeframe):
    """Use separate edge-fusion weights for high-resolution source scans."""

    variant = "efficientvit-sam-l0-hybrid/v2"
    _large_page_option_keys = frozenset(
        {
            "large_page_threshold_pixels",
            "large_left_model_weight",
            "large_top_model_weight",
            "large_right_model_weight",
            "large_bottom_model_weight",
        }
    )
    _hybrid_option_keys = (
        HybridEfficientVitSamDeframe._hybrid_option_keys | _large_page_option_keys
    )
    option_keys = AdaptiveEfficientVitSamDeframe.option_keys | _hybrid_option_keys

    def validate_options(self, options: Mapping[str, Any]) -> None:
        super().validate_options(options)
        _require_positive_integer(options, "large_page_threshold_pixels")
        for name in (
            "large_left_model_weight",
            "large_top_model_weight",
            "large_right_model_weight",
            "large_bottom_model_weight",
        ):
            _require_number(options, name, minimum=0.0, maximum=1.0)

    def _model_weights(self, job: Job, image) -> tuple[float, float, float, float]:
        height, width = image.shape[:2]
        if width * height < job.config.options["large_page_threshold_pixels"]:
            return super()._model_weights(job, image)
        return tuple(
            float(job.config.options[name])
            for name in (
                "large_left_model_weight",
                "large_top_model_weight",
                "large_right_model_weight",
                "large_bottom_model_weight",
            )
        )


register(HybridEfficientVitSamDeframe())
register(SizeAwareHybridEfficientVitSamDeframe())
