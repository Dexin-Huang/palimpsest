from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from palimpsest.factory import cli
from palimpsest.factory.evaluation.judge import ResolvedJudge, load_judge
from palimpsest.factory.evaluation.judging import (
    GatewayJudgeExecutor,
    JudgeExecutionResult,
)
from palimpsest.factory.evaluation.metrics import MetricObservation, MetricRegistry
from palimpsest.factory.evaluation.report import CaseSideOutcome, PairedCaseOutcome
from palimpsest.factory.evaluation.response_schemas import (
    PAIRWISE_PREFERENCE_SCHEMA_V1,
    PAIRWISE_PREFERENCE_V1,
    PairwisePreference,
    trusted_response_schemas,
)
from palimpsest.factory.evaluation.runner import (
    EvaluationRunner,
    _judge_aggregates,
    _judge_side_assignments,
    _run_judges,
    _scorecard,
)
from palimpsest.factory.evaluation.suite import CaseAsset
from palimpsest.factory.gateway.protocol import GatewayError, ModelResponse


ROOT = Path(__file__).resolve().parents[1]
FACTORY_ROOT = ROOT / "palimpsest" / "factory"
JUDGE_PATH = FACTORY_ROOT / "judges" / "read-image-pairwise-qwen3.8-v1.yaml"
SUITE_PATH = (
    FACTORY_ROOT
    / "evaluation"
    / "suites"
    / "read"
    / "zh-vatican-borg-cin-361-f004r-development-v1.yaml"
)


def _fixed_judge() -> ResolvedJudge:
    return load_judge(
        JUDGE_PATH,
        response_schema_resolver=trusted_response_schemas(),
    )


def _side(path: Path, candidate_id: str) -> CaseSideOutcome:
    return CaseSideOutcome(
        candidate_id=candidate_id,
        candidate_fingerprint=hashlib.sha256(candidate_id.encode()).hexdigest(),
        succeeded=True,
        output_path=str(path),
        output_fingerprint=hashlib.sha256(path.read_bytes()).hexdigest(),
        latency_seconds=0.1,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.01,
    )


def _judge_fixture(tmp_path: Path, *, image_hash: str | None = None, judge=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = b"\x89PNG\r\n\x1a\nsource-image"
    image_path = tmp_path / "source.png"
    image_path.write_bytes(image)
    baseline_path = tmp_path / "baseline.json"
    challenger_path = tmp_path / "challenger.json"
    baseline_path.write_text(json.dumps({"text": "alpha"}), encoding="utf-8")
    challenger_path.write_text(json.dumps({"text": "beta"}), encoding="utf-8")
    asset = CaseAsset(
        image_hash or hashlib.sha256(image).hexdigest(), path="source.png"
    )
    case = SimpleNamespace(
        case_id="case-1",
        page_id="p1",
        inputs={"page_image_clean": asset},
        strata=(),
    )
    outcome = PairedCaseOutcome(
        "case-1",
        _side(baseline_path, "private-baseline-id"),
        _side(challenger_path, "private-challenger-id"),
    )
    resolved = judge or _fixed_judge()
    binding = SimpleNamespace(metric="blind_image_pairwise", judge=resolved)
    suite = SimpleNamespace(
        judges=(binding,),
        promotion=SimpleNamespace(seed=7183),
    )
    runner = EvaluationRunner(
        run_root=tmp_path / "runs",
        asset_resolver=lambda _asset: image_path,
    )
    candidate = SimpleNamespace(produces="page_transcription")
    return runner, suite, (case,), (outcome,), candidate, candidate


def _otherwise_qualifiable_suite(binding):
    return SimpleNamespace(
        primary_metrics=(),
        hard_limits=(),
        protected_slices=(),
        slice_policy=SimpleNamespace(minimum_cases=1, maximum_regression=0.0),
        operational_limits=(),
        downstream_probes=(),
        judges=(binding,),
        promotion=SimpleNamespace(
            paired_bootstrap_samples=10,
            seed=1,
            minimum_completed_cases=1,
            require_all_hard_limits=True,
            require_all_downstream_probes=False,
        ),
        qualification_eligible=True,
        can_auto_qualify=binding.judge.can_auto_qualify,
    )


@pytest.mark.parametrize(
    "value",
    [
        {"winner": "left", "confidence": 0.5, "reason": "x"},
        {"winner": "A", "confidence": -0.01, "reason": "x"},
        {"winner": "A", "confidence": True, "reason": "x"},
        {"winner": "A", "confidence": 0.5, "reason": ""},
        {"winner": "A", "confidence": 0.5, "reason": "x", "extra": 1},
        {"winner": "A", "confidence": 0.5, "reason": "x", "failure_flags": ["other"]},
    ],
)
def test_pairwise_schema_strictly_rejects_malformed_responses(value) -> None:
    with pytest.raises(ValueError):
        PAIRWISE_PREFERENCE_SCHEMA_V1.validate(value)


def test_pairwise_schema_accepts_only_bounded_versioned_contract() -> None:
    registry = trusted_response_schemas()
    schema = registry.get(PAIRWISE_PREFERENCE_V1)
    response = schema.validate(
        {
            "winner": "tie",
            "confidence": 0.75,
            "reason": "Both preserve the visible line equally well.",
            "failure_flags": ["insufficient_visible_evidence"],
        }
    )
    assert response == PairwisePreference(
        "tie",
        0.75,
        "Both preserve the visible line equally well.",
        ("insufficient_visible_evidence",),
    )
    assert schema.json_schema["additionalProperties"] is False


def test_gateway_executor_sends_only_blinded_text_and_exact_resolved_protocol() -> None:
    judge = _fixed_judge()
    captured = []

    def generate(request, *, attempts):
        captured.append((request, attempts))
        return (
            {"winner": "B", "confidence": 0.9, "reason": "B matches the image."},
            ModelResponse(
                text="{}",
                model=judge.model,
                prompt_tokens=101,
                output_tokens=12,
                total_tokens=115,
                thought_tokens=2,
                cost_usd=0.004,
            ),
        )

    result = GatewayJudgeExecutor(generate).execute(
        judge=judge,
        source_image=b"image",
        source_mime="image/png",
        text_a="anonymous first text",
        text_b="anonymous second text",
    )

    request, attempts = captured[0]
    assert attempts == 1
    assert request.model == judge.model
    assert request.max_output_tokens == judge.params["max_output_tokens"]
    assert request.json_output is True
    assert request.json_schema is judge.response_schema_definition.json_schema
    assert request.images[0].data == b"image"
    assert "private-baseline-id" not in request.prompt
    assert "private-challenger-id" not in request.prompt
    assert "baseline" not in request.prompt.lower()
    assert "challenger" not in request.prompt.lower()
    assert "anonymous first text" in request.prompt
    assert "anonymous second text" in request.prompt
    assert result.prompt_tokens == 101
    assert result.output_tokens == 12
    assert result.thought_tokens == 2
    assert result.total_tokens == 115
    assert result.cost_usd == 0.004


def test_gateway_executor_hash_verifies_immutable_prompt_before_provider_call() -> None:
    called = False

    def generate(_request, *, attempts):
        nonlocal called
        called = True
        raise AssertionError(attempts)

    judge = replace(_fixed_judge(), prompt_hash="0" * 64)
    with pytest.raises(ValueError, match="prompt hash mismatch"):
        GatewayJudgeExecutor(generate).execute(
            judge=judge,
            source_image=b"image",
            source_mime="image/png",
            text_a="A",
            text_b="B",
        )
    assert called is False


def test_judge_side_assignment_is_deterministic_balanced_and_identity_free() -> None:
    cases = tuple(SimpleNamespace(case_id=f"case-{index:02d}") for index in range(11))
    suite = SimpleNamespace(promotion=SimpleNamespace(seed=934))
    first = _judge_side_assignments(suite, cases, "judge/v1")
    second = _judge_side_assignments(suite, tuple(reversed(cases)), "judge/v1")
    assert first == second
    baseline_a = sum(is_a for is_a, _seed in first.values())
    assert abs(baseline_a - (len(cases) - baseline_a)) == 1
    assert all(type(seed) is int for _is_a, seed in first.values())


class _RecordingExecutor:
    def __init__(self, response=None, error: Exception | None = None):
        self.calls = []
        self.response = response or PairwisePreference("A", 0.8, "A is more faithful.")
        self.error = error

    def execute(self, **request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return JudgeExecutionResult(
            self.response,
            request["judge"].model,
            prompt_tokens=20,
            output_tokens=4,
            thought_tokens=1,
            total_tokens=25,
            cost_usd=0.002,
        )


def test_runner_blinds_maps_and_persists_fixed_judge_evidence(tmp_path: Path) -> None:
    fixture = _judge_fixture(tmp_path)
    executor = _RecordingExecutor()
    evidence = _run_judges(*fixture, executor)
    item = evidence[0]
    call = executor.calls[0]

    assert set(call) == {
        "judge",
        "source_image",
        "source_mime",
        "text_a",
        "text_b",
    }
    assert call["source_image"].endswith(b"source-image")
    assert call["source_mime"] == "image/png"
    assert "private-baseline-id" not in repr(call)
    assert "private-challenger-id" not in repr(call)
    expected = "baseline" if item["order"]["A"] == "baseline" else "challenger"
    assert item["winner"] == expected
    assert item["response"] == {
        "winner": "A",
        "confidence": 0.8,
        "reason": "A is more faithful.",
        "failure_flags": [],
    }
    assert item["confidence"] == 0.8
    assert item["reason"] == "A is more faithful."
    assert item["usage"] == {
        "prompt_tokens": 20,
        "output_tokens": 4,
        "thought_tokens": 1,
        "total_tokens": 25,
        "cost_usd": 0.002,
    }
    aggregate = _judge_aggregates(fixture[1], evidence)[0]
    assert aggregate["judge_id"] == _fixed_judge().id
    assert aggregate["judge_fingerprint"] == _fixed_judge().fingerprint
    assert aggregate["wins"] + aggregate["losses"] == 1
    assert aggregate["ties"] == 0
    assert aggregate["unknowns"] == 0
    assert aggregate["total_cost_usd"] == 0.002
    assert aggregate["observations"] == list(evidence)


def test_missing_executor_is_explicit_unknown_never_zero(tmp_path: Path) -> None:
    fixture = _judge_fixture(tmp_path)
    item = _run_judges(*fixture, None)[0]
    assert item["status"] == "unknown"
    assert item["winner"] is None
    assert item["confidence"] is None
    assert item["usage"]["cost_usd"] is None
    aggregate = _judge_aggregates(fixture[1], (item,))[0]
    assert aggregate["unknowns"] == 1
    assert aggregate["total_cost_usd"] is None
    identity = SimpleNamespace(can_auto_qualify=True, fingerprint="f" * 64)
    _aggregates, decision, reasons = _scorecard(
        _otherwise_qualifiable_suite(fixture[1].judges[0]),
        fixture[3],
        {},
        (),
        (item,),
        identity,
        identity,
    )
    assert decision == "unknown"
    assert reasons == (f"required judge unknown: {_fixed_judge().id}",)


def test_provider_and_malformed_executor_failures_are_unknown_with_usage(
    tmp_path: Path,
) -> None:
    fixture = _judge_fixture(tmp_path)
    provider = GatewayError(
        "provider failed", tokens_in=31, tokens_out=7, cost_usd=0.003
    )
    failed = _run_judges(*fixture, _RecordingExecutor(error=provider))[0]
    assert failed["status"] == "unknown"
    assert failed["usage"]["prompt_tokens"] == 31
    assert failed["usage"]["output_tokens"] == 7
    assert failed["usage"]["cost_usd"] == 0.003

    class Malformed:
        def execute(self, **_request):
            return {"winner": "A"}

    malformed = _run_judges(*fixture, Malformed())[0]
    assert malformed["status"] == "unknown"
    assert "malformed result" in malformed["error_message"]
    assert malformed["usage"]["cost_usd"] is None


def test_schema_invalid_paid_response_remains_unknown_with_full_usage(
    tmp_path: Path,
) -> None:
    fixture = _judge_fixture(tmp_path)

    def malformed(_request, *, attempts):
        assert attempts == 1
        return (
            {"winner": "A", "confidence": 2.0, "reason": "invalid"},
            ModelResponse(
                text="{}",
                model=_fixed_judge().model,
                prompt_tokens=40,
                output_tokens=6,
                thought_tokens=2,
                total_tokens=48,
                cost_usd=0.006,
            ),
        )

    item = _run_judges(*fixture, GatewayJudgeExecutor(malformed))[0]
    assert item["status"] == "unknown"
    assert item["usage"] == {
        "prompt_tokens": 40,
        "output_tokens": 6,
        "thought_tokens": 2,
        "total_tokens": 48,
        "cost_usd": 0.006,
    }


def test_moving_judge_and_source_hash_failure_are_unknown_without_call(
    tmp_path: Path,
) -> None:
    moving = replace(_fixed_judge(), model_identity="moving")
    moving_fixture = _judge_fixture(tmp_path / "moving", judge=moving)
    moving_executor = _RecordingExecutor()
    moving_item = _run_judges(*moving_fixture, moving_executor)[0]
    assert moving_item["status"] == "unknown"
    assert "cannot auto-qualify" in moving_item["error_message"]
    assert moving_executor.calls == []
    identity = SimpleNamespace(can_auto_qualify=True, fingerprint="f" * 64)
    _aggregates, decision, reasons = _scorecard(
        _otherwise_qualifiable_suite(moving_fixture[1].judges[0]),
        moving_fixture[3],
        {},
        (),
        (moving_item,),
        identity,
        identity,
    )
    assert decision == "unknown"
    assert any("judge identity cannot auto-qualify" in reason for reason in reasons)

    hash_fixture = _judge_fixture(tmp_path / "hash", image_hash="0" * 64)
    hash_executor = _RecordingExecutor()
    hash_item = _run_judges(*hash_fixture, hash_executor)[0]
    assert hash_item["status"] == "unknown"
    assert "hash mismatch" in hash_item["error_message"]
    assert hash_executor.calls == []


def test_deterministic_hard_limit_rejects_despite_judge_preference() -> None:
    primary = SimpleNamespace(
        name="quality", direction="maximize", minimum_effect=0.0, confidence=0.8
    )
    hard = SimpleNamespace(name="invented", minimum=None, maximum=0.0)
    judge = _fixed_judge()
    binding = SimpleNamespace(metric="blind_image_pairwise", judge=judge)
    promotion = SimpleNamespace(
        paired_bootstrap_samples=50,
        seed=8,
        minimum_completed_cases=1,
        require_all_hard_limits=True,
        require_all_downstream_probes=False,
    )
    suite = SimpleNamespace(
        primary_metrics=(primary,),
        hard_limits=(hard,),
        protected_slices=(),
        slice_policy=SimpleNamespace(minimum_cases=1, maximum_regression=0.0),
        operational_limits=(),
        downstream_probes=(),
        judges=(binding,),
        promotion=promotion,
        qualification_eligible=True,
        can_auto_qualify=True,
    )
    baseline_side = CaseSideOutcome(
        "base", "a" * 64, True, "a", "b" * 64, 0.1, 1, 1, 0.0
    )
    challenger_side = CaseSideOutcome(
        "next", "c" * 64, True, "b", "d" * 64, 0.1, 1, 1, 0.0
    )
    outcomes = (PairedCaseOutcome("case", baseline_side, challenger_side),)
    observations = {
        "quality": (MetricObservation("case", "quality", baseline=0.0, candidate=1.0),),
        "invented": (
            MetricObservation("case", "invented", baseline=0.0, candidate=0.5),
        ),
    }
    judge_evidence = (
        {
            "case_id": "case",
            "metric": binding.metric,
            "judge_id": judge.id,
            "judge_fingerprint": judge.fingerprint,
            "status": "completed",
            "winner": "challenger",
            "response": {"winner": "A"},
            "confidence": 1.0,
            "usage": {"cost_usd": 0.0},
        },
    )
    identity = SimpleNamespace(can_auto_qualify=True, fingerprint="e" * 64)
    aggregates, decision, _reasons = _scorecard(
        suite, outcomes, observations, (), judge_evidence, identity, identity
    )
    assert decision == "rejected"
    assert aggregates["hard_limits"][0]["decision"] == "fail"
    assert aggregates["judges"][0]["wins"] == 1


def test_cli_run_constructs_and_wires_gateway_executor_for_declared_judge(
    tmp_path: Path, monkeypatch
) -> None:
    suite = SimpleNamespace(
        cases=(SimpleNamespace(case_id="case"),), judges=(object(),)
    )
    baseline, challenger, resolver, executor = object(), object(), object(), object()
    workflow_calls = []

    class Store:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def workflow(**kwargs):
        workflow_calls.append(kwargs)
        return SimpleNamespace(report_path=tmp_path / "report.json")

    monkeypatch.setattr(cli, "_resolve_suite", lambda *_args, **_kwargs: suite)
    monkeypatch.setattr(cli, "_verify_source_objects", lambda *_args: 0)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.candidate.load_candidate",
        lambda path: baseline if str(path) == "base.yaml" else challenger,
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.runner.filesystem_asset_resolver",
        lambda *_args: resolver,
    )
    monkeypatch.setattr("palimpsest.factory.evaluation.runner.run_evaluation", workflow)
    monkeypatch.setattr("palimpsest.factory.evaluation.store.EvaluationStore", Store)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.judging.GatewayJudgeExecutor",
        lambda: executor,
    )
    args = SimpleNamespace(
        suite=Path("suite.yaml"),
        baseline=Path("base.yaml"),
        challenger=Path("next.yaml"),
        cases=None,
        object_root=tmp_path / "objects",
        asset_root=tmp_path / "assets",
        db=tmp_path / "factory.db",
        run_id="run",
        runs_root=tmp_path / "runs",
        executor="inline",
        workers=1,
    )
    cli.cmd_bench_run(args)
    assert workflow_calls[0]["judge_executor"] is executor


def test_cli_resolves_judges_and_suites_without_constructing_paid_executor(
    monkeypatch,
) -> None:
    def paid(*_args, **_kwargs):
        raise AssertionError(
            "offline list/verify resolution constructed a paid executor"
        )

    monkeypatch.setattr(
        "palimpsest.factory.evaluation.judging.GatewayJudgeExecutor", paid
    )
    metrics, _probes, judges = cli._trusted_resolvers(FACTORY_ROOT / "judges")
    suite = cli._resolve_suite(
        SUITE_PATH,
        judges_root=FACTORY_ROOT / "judges",
        asset_root=FACTORY_ROOT / "evaluation",
    )
    assert isinstance(metrics, MetricRegistry)
    assert _fixed_judge().id in judges
    assert suite.judges[0].judge.fingerprint == judges[_fixed_judge().id].fingerprint
    assert suite.qualification_eligible is False
