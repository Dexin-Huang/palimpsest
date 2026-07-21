"""Metric definitions and immutable paired observations for evaluations.

Metrics are registered as concrete Python callables by trusted station modules.  Suite
files resolve names through :class:`MetricRegistry`; they never provide import paths.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias


class MetricDirection(str, Enum):
    """Whether a smaller or larger raw metric value is better."""

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


MetricScorer: TypeAlias = Callable[
    [Mapping[str, object], Mapping[str, object]], float | None
]


def _validate_value(value: float | None, *, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number or None")
    if not isfinite(value):
        raise ValueError(f"{field} must be finite")


@dataclass(frozen=True, slots=True)
class Metric:
    """A named scorer with an explicit comparison direction.

    ``scorer`` receives the validated candidate output and scorer-only gold record.
    Returning ``None`` records an explicit unknown; it is never coerced to zero.
    """

    name: str
    direction: MetricDirection
    scorer: MetricScorer

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("metric name must not be empty")
        if not isinstance(self.direction, MetricDirection):
            raise TypeError("metric direction must be MetricDirection")
        if not callable(self.scorer):
            raise TypeError("metric scorer must be callable")

    def observe(
        self,
        output: Mapping[str, object],
        gold: Mapping[str, object],
    ) -> float | None:
        value = self.scorer(output, gold)
        _validate_value(value, field=f"metric {self.name!r} result")
        return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class MetricObservation:
    """One immutable paired case observation.

    An observation represents an attempted case on both sides.  Failed candidate
    execution is retained with ``*_succeeded=False`` and a missing value, so it
    remains present in reliability denominators.  ``None`` success or value means
    unknown, not success and not zero.
    """

    case_id: str
    metric: str
    baseline: float | None
    candidate: float | None
    slices: frozenset[str] = frozenset()
    baseline_succeeded: bool | None = True
    candidate_succeeded: bool | None = True

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must not be empty")
        if not self.metric:
            raise ValueError("metric must not be empty")
        _validate_value(self.baseline, field="baseline")
        _validate_value(self.candidate, field="candidate")
        if not isinstance(self.slices, frozenset):
            object.__setattr__(self, "slices", frozenset(self.slices))
        if any(not item for item in self.slices):
            raise ValueError("slice names must not be empty")
        for side in ("baseline", "candidate"):
            succeeded = getattr(self, f"{side}_succeeded")
            value = getattr(self, side)
            if (
                succeeded is not True
                and succeeded is not False
                and succeeded is not None
            ):
                raise TypeError(f"{side}_succeeded must be bool or None")
            if succeeded is not True and value is not None:
                raise ValueError(f"{side} value requires a successful outcome")
        if self.baseline is not None:
            object.__setattr__(self, "baseline", float(self.baseline))
        if self.candidate is not None:
            object.__setattr__(self, "candidate", float(self.candidate))


class MetricRegistry:
    """Explicit registry for trusted, concrete metric implementations."""

    def __init__(self, metrics: Iterable[Metric] = ()) -> None:
        self._metrics: dict[str, Metric] = {}
        for metric in metrics:
            self.register(metric)

    def register(self, metric: Metric) -> None:
        if not isinstance(metric, Metric):
            raise TypeError("registry entries must be Metric instances")
        if metric.name in self._metrics:
            raise ValueError(f"metric {metric.name!r} is already registered")
        self._metrics[metric.name] = metric

    def get(self, name: str) -> Metric:
        try:
            return self._metrics[name]
        except KeyError:
            raise KeyError(f"unknown metric {name!r}") from None

    def observe(
        self,
        name: str,
        output: Mapping[str, object],
        gold: Mapping[str, object],
    ) -> float | None:
        return self.get(name).observe(output, gold)

    def all(self) -> tuple[Metric, ...]:
        return tuple(self._metrics[name] for name in sorted(self._metrics))

    @property
    def by_name(self) -> Mapping[str, Metric]:
        return MappingProxyType(self._metrics)
