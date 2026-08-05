"""Adaptive-confidence EfficientViT deframe development variant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationConfig, StationResult
from palimpsest.factory.stations.deframe_models import EfficientVitSamDeframe


class AdaptiveEfficientVitSamDeframe(EfficientVitSamDeframe):
    """Retry at a lower mask confidence only when the strict pass finds no page."""

    variant = "efficientvit-sam-l0-adaptive/v1"
    option_keys = frozenset(
        {*EfficientVitSamDeframe.option_keys, "fallback_predicted_iou_threshold"}
    )
    production_dependencies = (
        *EfficientVitSamDeframe.production_dependencies,
        "factory/stations/deframe_models.py",
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        expected = self.option_keys
        actual = set(options)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                f"Expected options {sorted(expected)}; "
                f"missing={missing}, unknown={unknown}"
            )
        primary_options = dict(options)
        fallback = primary_options.pop("fallback_predicted_iou_threshold")
        EfficientVitSamDeframe().validate_options(primary_options)
        if isinstance(fallback, bool) or not isinstance(fallback, int | float):
            raise TypeError("fallback_predicted_iou_threshold must be a number")
        if not 0.0 <= float(fallback) < float(options["predicted_iou_threshold"]):
            raise ValueError(
                "fallback_predicted_iou_threshold must be non-negative and "
                "lower than predicted_iou_threshold"
            )

    def run(self, job: Job) -> StationResult:
        try:
            return super().run(job)
        except RuntimeError as error:
            if "Model produced no manuscript-scale mask" not in str(error):
                raise

        retry_options = dict(job.config.options)
        retry_options["predicted_iou_threshold"] = retry_options.pop(
            "fallback_predicted_iou_threshold"
        )
        retry_config = StationConfig(
            model=job.config.model,
            prompt=job.config.prompt,
            params=job.config.params,
            options=retry_options,
        )
        return super().run(replace(job, config=retry_config))


register(AdaptiveEfficientVitSamDeframe())
