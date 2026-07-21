"""Deterministic paired comparisons and qualification gates.

The module deliberately implements one statistical method: a paired bootstrap over
frozen case IDs.  Raw values remain in their original units; direction is applied
only to the effect used for a decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from random import Random
from typing import Literal

from .metrics import MetricDirection, MetricObservation


class ComparisonDecision(str, Enum):
    PASS = "pass"
    EFFECT_NOT_MET = "effect_not_met"
    REGRESSION = "regression"
    TIE = "tie"
    UNKNOWN = "unknown"
    INSUFFICIENT = "insufficient"


class LimitDecision(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    HARD_LIMIT_FAILED = "hard_limit_failed"
    UNKNOWN = "unknown"
    INSUFFICIENT = "insufficient"
    PROTECTED_SLICE_REGRESSION = "protected_slice_regression"
    EFFECT_NOT_MET = "effect_not_met"


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    minimum_effect: float
    confidence: float
    bootstrap_samples: int
    seed: int
    minimum_pairs: int

    def __post_init__(self) -> None:
        if not isfinite(self.minimum_effect) or self.minimum_effect < 0:
            raise ValueError("minimum_effect must be finite and non-negative")
        if not 0 < self.confidence < 1:
            raise ValueError("confidence must be between zero and one")
        if self.bootstrap_samples < 1:
            raise ValueError("bootstrap_samples must be positive")
        if self.minimum_pairs < 1:
            raise ValueError("minimum_pairs must be positive")


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    lower: float
    upper: float
    confidence: float


@dataclass(frozen=True, slots=True)
class PairedComparison:
    metric: str
    direction: MetricDirection
    baseline_mean: float | None
    candidate_mean: float | None
    paired_delta: float | None
    confidence_interval: ConfidenceInterval | None
    effect_size: float | None
    total_pairs: int
    valid_pairs: int
    missing_case_ids: tuple[str, ...]
    decision: ComparisonDecision

    @property
    def favorable_confidence_interval(self) -> ConfidenceInterval | None:
        """Return the interval with positive values meaning candidate improvement."""

        interval = self.confidence_interval
        if interval is None:
            return None
        if self.direction is MetricDirection.MAXIMIZE:
            return interval
        return ConfidenceInterval(
            lower=-interval.upper,
            upper=-interval.lower,
            confidence=interval.confidence,
        )


@dataclass(frozen=True, slots=True)
class HardLimit:
    metric: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("hard-limit metric must not be empty")
        if self.minimum is None and self.maximum is None:
            raise ValueError("hard limit requires a minimum or maximum")
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise ValueError(f"hard-limit {name} must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("hard-limit minimum cannot exceed maximum")


@dataclass(frozen=True, slots=True)
class HardLimitResult:
    metric: str
    value: float | None
    minimum: float | None
    maximum: float | None
    decision: LimitDecision


@dataclass(frozen=True, slots=True)
class ProtectedSliceResult:
    name: str
    minimum_cases: int
    maximum_regression: float
    comparison: PairedComparison
    decision: ComparisonDecision


@dataclass(frozen=True, slots=True)
class ReliabilitySummary:
    side: Literal["baseline", "candidate"]
    attempted_cases: int
    successful_cases: int
    failed_cases: int
    unknown_cases: tuple[str, ...]
    success_rate: float | None


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    qualified: bool
    status: QualificationStatus
    reasons: tuple[str, ...]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = probability * (len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return (
        sorted_values[lower_index] * (1 - fraction)
        + sorted_values[upper_index] * fraction
    )


def _bootstrap_interval(
    deltas: Sequence[float],
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> ConfidenceInterval:
    random = Random(seed)
    count = len(deltas)
    means = [
        sum(deltas[random.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    ]
    means.sort()
    tail = (1 - confidence) / 2
    return ConfidenceInterval(
        lower=_quantile(means, tail),
        upper=_quantile(means, 1 - tail),
        confidence=confidence,
    )


def _ordered_observations(
    metric: str,
    observations: Iterable[MetricObservation],
) -> tuple[MetricObservation, ...]:
    ordered = tuple(sorted(observations, key=lambda observation: observation.case_id))
    seen: set[str] = set()
    for observation in ordered:
        if observation.metric != metric:
            raise ValueError(
                f"observation metric {observation.metric!r} does not match {metric!r}"
            )
        if observation.case_id in seen:
            raise ValueError(f"duplicate case_id {observation.case_id!r}")
        seen.add(observation.case_id)
    return ordered


def paired_comparison(
    metric: str,
    observations: Iterable[MetricObservation],
    *,
    direction: MetricDirection,
    policy: ComparisonPolicy,
) -> PairedComparison:
    """Compare paired raw values with a deterministic case-level bootstrap.

    Input order does not affect the result: observations are sorted by immutable
    case ID before seeded resampling.  A missing value or non-successful outcome
    blocks the decision rather than disappearing from the comparison.
    """

    if not metric:
        raise ValueError("metric must not be empty")
    if not isinstance(direction, MetricDirection):
        raise TypeError("direction must be MetricDirection")
    ordered = _ordered_observations(metric, observations)
    valid = tuple(
        observation
        for observation in ordered
        if observation.baseline_succeeded is True
        and observation.candidate_succeeded is True
        and observation.baseline is not None
        and observation.candidate is not None
    )
    valid_ids = {observation.case_id for observation in valid}
    missing = tuple(
        observation.case_id
        for observation in ordered
        if observation.case_id not in valid_ids
    )

    baseline_values = tuple(observation.baseline for observation in valid)
    candidate_values = tuple(observation.candidate for observation in valid)
    deltas = tuple(
        candidate - baseline
        for baseline, candidate in zip(
            baseline_values,
            candidate_values,
            strict=True,
        )
    )
    if deltas:
        baseline_mean = _mean(baseline_values)
        candidate_mean = _mean(candidate_values)
        paired_delta = _mean(deltas)
        interval = _bootstrap_interval(
            deltas,
            confidence=policy.confidence,
            samples=policy.bootstrap_samples,
            seed=policy.seed,
        )
        effect_size = (
            paired_delta if direction is MetricDirection.MAXIMIZE else -paired_delta
        )
    else:
        baseline_mean = None
        candidate_mean = None
        paired_delta = None
        interval = None
        effect_size = None

    if len(ordered) < policy.minimum_pairs:
        decision = ComparisonDecision.INSUFFICIENT
    elif missing:
        decision = ComparisonDecision.UNKNOWN
    elif len(valid) < policy.minimum_pairs:
        decision = ComparisonDecision.INSUFFICIENT
    elif all(delta == 0 for delta in deltas):
        decision = ComparisonDecision.TIE
    else:
        assert interval is not None
        favorable_lower = (
            interval.lower if direction is MetricDirection.MAXIMIZE else -interval.upper
        )
        favorable_upper = (
            interval.upper if direction is MetricDirection.MAXIMIZE else -interval.lower
        )
        if favorable_lower >= policy.minimum_effect:
            decision = ComparisonDecision.PASS
        elif favorable_upper < 0:
            decision = ComparisonDecision.REGRESSION
        else:
            decision = ComparisonDecision.EFFECT_NOT_MET

    return PairedComparison(
        metric=metric,
        direction=direction,
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        paired_delta=paired_delta,
        confidence_interval=interval,
        effect_size=effect_size,
        total_pairs=len(ordered),
        valid_pairs=len(valid),
        missing_case_ids=missing,
        decision=decision,
    )


def evaluate_hard_limit(limit: HardLimit, value: float | None) -> HardLimitResult:
    """Evaluate a pre-aggregated raw candidate value against non-tradeable bounds."""

    if value is None:
        decision = LimitDecision.UNKNOWN
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("hard-limit value must be a finite number or None")
        if not isfinite(value):
            raise ValueError("hard-limit value must be finite")
        below = limit.minimum is not None and value < limit.minimum
        above = limit.maximum is not None and value > limit.maximum
        decision = LimitDecision.FAIL if below or above else LimitDecision.PASS
        value = float(value)
    return HardLimitResult(
        metric=limit.metric,
        value=value,
        minimum=limit.minimum,
        maximum=limit.maximum,
        decision=decision,
    )


def compare_protected_slice(
    name: str,
    metric: str,
    observations: Iterable[MetricObservation],
    *,
    direction: MetricDirection,
    minimum_cases: int,
    maximum_regression: float,
    confidence: float,
    bootstrap_samples: int,
    seed: int,
) -> ProtectedSliceResult:
    """Require enough slice cases and conservative evidence of non-regression."""

    if not name:
        raise ValueError("protected slice name must not be empty")
    if minimum_cases < 1:
        raise ValueError("minimum_cases must be positive")
    if not isfinite(maximum_regression) or maximum_regression < 0:
        raise ValueError("maximum_regression must be finite and non-negative")
    selected = tuple(
        observation for observation in observations if name in observation.slices
    )
    comparison = paired_comparison(
        metric,
        selected,
        direction=direction,
        policy=ComparisonPolicy(
            minimum_effect=0,
            confidence=confidence,
            bootstrap_samples=bootstrap_samples,
            seed=seed,
            minimum_pairs=minimum_cases,
        ),
    )
    if comparison.decision in {
        ComparisonDecision.UNKNOWN,
        ComparisonDecision.INSUFFICIENT,
    }:
        decision = comparison.decision
    else:
        favorable = comparison.favorable_confidence_interval
        assert favorable is not None
        if favorable.lower >= -maximum_regression:
            decision = ComparisonDecision.PASS
        elif favorable.upper < -maximum_regression:
            decision = ComparisonDecision.REGRESSION
        else:
            decision = ComparisonDecision.EFFECT_NOT_MET
    return ProtectedSliceResult(
        name=name,
        minimum_cases=minimum_cases,
        maximum_regression=maximum_regression,
        comparison=comparison,
        decision=decision,
    )


def summarize_reliability(
    observations: Iterable[MetricObservation],
    *,
    side: Literal["baseline", "candidate"],
) -> ReliabilitySummary:
    """Summarize execution reliability without excluding failed attempts."""

    if side not in ("baseline", "candidate"):
        raise ValueError("side must be 'baseline' or 'candidate'")
    ordered = tuple(sorted(observations, key=lambda item: item.case_id))
    case_ids: set[str] = set()
    for observation in ordered:
        if observation.case_id in case_ids:
            raise ValueError(f"duplicate case_id {observation.case_id!r}")
        case_ids.add(observation.case_id)
    outcomes = tuple(getattr(item, f"{side}_succeeded") for item in ordered)
    successful = sum(outcome is True for outcome in outcomes)
    failed = sum(outcome is False for outcome in outcomes)
    unknown = tuple(
        item.case_id
        for item, outcome in zip(ordered, outcomes, strict=True)
        if outcome is None
    )
    success_rate = successful / len(ordered) if ordered and not unknown else None
    return ReliabilitySummary(
        side=side,
        attempted_cases=len(ordered),
        successful_cases=successful,
        failed_cases=failed,
        unknown_cases=unknown,
        success_rate=success_rate,
    )


def qualification_decision(
    *,
    primary_metrics: Iterable[PairedComparison],
    hard_limits: Iterable[HardLimitResult],
    protected_slices: Iterable[ProtectedSliceResult],
) -> QualificationDecision:
    """Combine score-vector gates with hard limits taking absolute precedence."""

    primary = tuple(sorted(primary_metrics, key=lambda item: item.metric))
    limits = tuple(sorted(hard_limits, key=lambda item: item.metric))
    slices = tuple(sorted(protected_slices, key=lambda item: item.name))

    failed_limits = tuple(
        item for item in limits if item.decision is LimitDecision.FAIL
    )
    unknown_limits = tuple(
        item for item in limits if item.decision is LimitDecision.UNKNOWN
    )
    unknown_metrics = tuple(
        item for item in primary if item.decision is ComparisonDecision.UNKNOWN
    )
    unknown_slices = tuple(
        item for item in slices if item.decision is ComparisonDecision.UNKNOWN
    )
    insufficient_metrics = tuple(
        item for item in primary if item.decision is ComparisonDecision.INSUFFICIENT
    )
    insufficient_slices = tuple(
        item for item in slices if item.decision is ComparisonDecision.INSUFFICIENT
    )
    regressed_slices = tuple(
        item for item in slices if item.decision is ComparisonDecision.REGRESSION
    )
    unmet_primary = tuple(
        item for item in primary if item.decision is not ComparisonDecision.PASS
    )
    uncertain_slices = tuple(
        item
        for item in slices
        if item.decision not in {ComparisonDecision.PASS, ComparisonDecision.REGRESSION}
    )

    reasons: list[str] = []
    reasons.extend(f"hard limit failed: {item.metric}" for item in failed_limits)
    reasons.extend(f"hard limit unknown: {item.metric}" for item in unknown_limits)
    reasons.extend(f"metric unknown: {item.metric}" for item in unknown_metrics)
    reasons.extend(f"slice unknown: {item.name}" for item in unknown_slices)
    reasons.extend(
        f"metric has insufficient evidence: {item.metric}"
        for item in insufficient_metrics
    )
    reasons.extend(
        f"slice has insufficient evidence: {item.name}" for item in insufficient_slices
    )
    reasons.extend(
        f"protected slice regressed: {item.name}" for item in regressed_slices
    )
    reasons.extend(
        f"primary effect not met: {item.metric}"
        for item in unmet_primary
        if item not in unknown_metrics and item not in insufficient_metrics
    )
    reasons.extend(
        f"protected slice non-regression not established: {item.name}"
        for item in uncertain_slices
        if item not in unknown_slices and item not in insufficient_slices
    )

    if failed_limits:
        status = QualificationStatus.HARD_LIMIT_FAILED
    elif unknown_limits or unknown_metrics or unknown_slices:
        status = QualificationStatus.UNKNOWN
    elif insufficient_metrics or insufficient_slices:
        status = QualificationStatus.INSUFFICIENT
    elif regressed_slices:
        status = QualificationStatus.PROTECTED_SLICE_REGRESSION
    elif unmet_primary or uncertain_slices:
        status = QualificationStatus.EFFECT_NOT_MET
    else:
        status = QualificationStatus.QUALIFIED
    return QualificationDecision(
        qualified=status is QualificationStatus.QUALIFIED,
        status=status,
        reasons=tuple(reasons),
    )
