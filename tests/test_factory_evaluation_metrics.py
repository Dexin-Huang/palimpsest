from __future__ import annotations

import pytest

from palimpsest.factory.evaluation.metrics import (
    Metric,
    MetricDirection,
    MetricObservation,
    MetricRegistry,
)
from palimpsest.factory.evaluation.statistics import (
    ComparisonDecision,
    ComparisonPolicy,
    HardLimit,
    LimitDecision,
    QualificationStatus,
    compare_protected_slice,
    evaluate_hard_limit,
    paired_comparison,
    qualification_decision,
    summarize_reliability,
)


def observation(
    case_id: str,
    baseline: float | None,
    candidate: float | None,
    *,
    metric: str = "quality",
    slices: frozenset[str] = frozenset(),
    baseline_succeeded: bool | None = True,
    candidate_succeeded: bool | None = True,
) -> MetricObservation:
    return MetricObservation(
        case_id=case_id,
        metric=metric,
        baseline=baseline,
        candidate=candidate,
        slices=slices,
        baseline_succeeded=baseline_succeeded,
        candidate_succeeded=candidate_succeeded,
    )


def policy(*, minimum_effect: float = 0.05, minimum_pairs: int = 2):
    return ComparisonPolicy(
        minimum_effect=minimum_effect,
        confidence=0.95,
        bootstrap_samples=500,
        seed=3477,
        minimum_pairs=minimum_pairs,
    )


def test_metric_registry_resolves_only_explicit_concrete_metrics():
    accuracy = Metric(
        "accuracy",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["correct"] / gold["total"],
    )
    registry = MetricRegistry([accuracy])

    assert registry.get("accuracy") is accuracy
    assert registry.observe("accuracy", {"correct": 3}, {"total": 4}) == 0.75
    with pytest.raises(ValueError, match="already registered"):
        registry.register(accuracy)
    with pytest.raises(KeyError, match="unknown metric"):
        registry.get("dynamic.module.metric")


def test_metric_direction_is_applied_only_to_decision_effect():
    minimize = paired_comparison(
        "error",
        [
            observation("a", 0.40, 0.20, metric="error"),
            observation("b", 0.30, 0.10, metric="error"),
        ],
        direction=MetricDirection.MINIMIZE,
        policy=policy(minimum_effect=0.15),
    )
    maximize = paired_comparison(
        "quality",
        [observation("a", 0.40, 0.60), observation("b", 0.30, 0.50)],
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0.15),
    )

    assert minimize.paired_delta == pytest.approx(-0.20)
    assert minimize.effect_size == pytest.approx(0.20)
    assert minimize.decision is ComparisonDecision.PASS
    assert maximize.paired_delta == pytest.approx(0.20)
    assert maximize.effect_size == pytest.approx(0.20)
    assert maximize.decision is ComparisonDecision.PASS


def test_paired_bootstrap_is_seeded_deterministic_and_order_independent():
    observations = [
        observation("case-c", 0.3, 0.7),
        observation("case-a", 0.4, 0.5),
        observation("case-b", 0.2, 0.5),
        observation("case-d", 0.6, 0.65),
    ]

    first = paired_comparison(
        "quality",
        observations,
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0, minimum_pairs=4),
    )
    second = paired_comparison(
        "quality",
        reversed(observations),
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0, minimum_pairs=4),
    )

    assert first == second
    assert first.confidence_interval is not None
    assert first.confidence_interval.lower == pytest.approx(0.075)
    assert first.confidence_interval.upper == pytest.approx(0.35)


def test_hard_limit_failure_has_precedence_over_primary_improvement():
    primary = paired_comparison(
        "quality",
        [observation("a", 0.1, 0.4), observation("b", 0.2, 0.5)],
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0.1),
    )
    hard_limit = evaluate_hard_limit(
        HardLimit("invented_character_rate", maximum=0.001),
        0.002,
    )

    decision = qualification_decision(
        primary_metrics=[primary],
        hard_limits=[hard_limit],
        protected_slices=[],
    )

    assert primary.decision is ComparisonDecision.PASS
    assert hard_limit.decision is LimitDecision.FAIL
    assert decision.status is QualificationStatus.HARD_LIMIT_FAILED
    assert not decision.qualified


def test_protected_slice_regression_blocks_qualification():
    observations = [
        observation("a", 0.8, 0.7, slices=frozenset({"damaged"})),
        observation("b", 0.7, 0.6, slices=frozenset({"damaged"})),
    ]
    overall = paired_comparison(
        "quality",
        [observation("c", 0.1, 0.4), observation("d", 0.2, 0.5)],
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0.1),
    )
    protected = compare_protected_slice(
        "damaged",
        "quality",
        observations,
        direction=MetricDirection.MAXIMIZE,
        minimum_cases=2,
        maximum_regression=0.01,
        confidence=0.95,
        bootstrap_samples=500,
        seed=3477,
    )

    decision = qualification_decision(
        primary_metrics=[overall],
        hard_limits=[],
        protected_slices=[protected],
    )

    assert protected.decision is ComparisonDecision.REGRESSION
    assert decision.status is QualificationStatus.PROTECTED_SLICE_REGRESSION
    assert not decision.qualified


def test_protected_slice_with_too_few_cases_is_insufficient():
    protected = compare_protected_slice(
        "marginalia",
        "quality",
        [observation("a", 0.5, 0.6, slices=frozenset({"marginalia"}))],
        direction=MetricDirection.MAXIMIZE,
        minimum_cases=2,
        maximum_regression=0.01,
        confidence=0.95,
        bootstrap_samples=100,
        seed=1,
    )

    decision = qualification_decision(
        primary_metrics=[],
        hard_limits=[],
        protected_slices=[protected],
    )

    assert protected.comparison.total_pairs == 1
    assert protected.decision is ComparisonDecision.INSUFFICIENT
    assert decision.status is QualificationStatus.INSUFFICIENT
    assert not decision.qualified


def test_missing_values_remain_explicit_and_block_comparison():
    comparison = paired_comparison(
        "quality",
        [observation("known", 0.4, 0.6), observation("missing", 0.3, None)],
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0, minimum_pairs=1),
    )
    unknown_limit = evaluate_hard_limit(HardLimit("cost", maximum=1.0), None)

    assert comparison.valid_pairs == 1
    assert comparison.total_pairs == 2
    assert comparison.missing_case_ids == ("missing",)
    assert comparison.candidate_mean == pytest.approx(0.6)
    assert comparison.decision is ComparisonDecision.UNKNOWN
    assert unknown_limit.value is None
    assert unknown_limit.decision is LimitDecision.UNKNOWN


def test_exact_paired_ties_are_reported_not_promoted():
    comparison = paired_comparison(
        "quality",
        [observation("a", 0.5, 0.5), observation("b", 0.7, 0.7)],
        direction=MetricDirection.MAXIMIZE,
        policy=policy(minimum_effect=0.01),
    )

    assert comparison.paired_delta == 0
    assert comparison.effect_size == 0
    assert comparison.decision is ComparisonDecision.TIE
    assert comparison.confidence_interval is not None
    assert comparison.confidence_interval.lower == 0
    assert comparison.confidence_interval.upper == 0


def test_reliability_denominator_retains_failed_cases():
    observations = [
        observation("success-a", 1, 1),
        observation("success-b", 1, 1),
        observation(
            "failed",
            1,
            None,
            candidate_succeeded=False,
        ),
    ]

    summary = summarize_reliability(observations, side="candidate")

    assert summary.attempted_cases == 3
    assert summary.successful_cases == 2
    assert summary.failed_cases == 1
    assert summary.unknown_cases == ()
    assert summary.success_rate == pytest.approx(2 / 3)
