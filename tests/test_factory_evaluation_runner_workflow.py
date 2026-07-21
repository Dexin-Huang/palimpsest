from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from palimpsest.factory.evaluation.candidate import RecordError, ResolvedCandidate
from palimpsest.factory.evaluation.judge import ResolvedJudge
from palimpsest.factory.evaluation.metrics import Metric, MetricDirection
from palimpsest.factory.evaluation.runner import (
    _side_order,
    filesystem_asset_resolver,
    run_evaluation,
)
from palimpsest.factory.evaluation.store import EvaluationStore
from palimpsest.factory.evaluation.suite import (
    CaseAsset,
    DownstreamProbe,
    EvaluationCase,
    EvaluationSuite,
    JudgeMetric,
    MetricLimit,
    PrimaryMetric,
    PromotionPolicy,
    SlicePolicy,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(
    candidate_id: str, fingerprint: str, *, moving: bool = False
) -> ResolvedCandidate:
    return ResolvedCandidate(
        schema_version=1,
        id=candidate_id,
        station="read",
        variant="test/v1",
        grain="page",
        consumes=("page_list", "page_image"),
        optional_consumes=(),
        produces="page_transcription",
        model="model-latest" if moving else "model-20260721",
        model_identity="moving" if moving else "fixed",
        prompt_name="read/test",
        prompt_hash="1" * 64,
        params=MappingProxyType({}),
        options=MappingProxyType({}),
        notes=None,
        implementation_fingerprint="2" * 64,
        fingerprint=fingerprint,
    )


def _case(
    tmp_path: Path, number: int, *, strata: tuple[str, ...] = ()
) -> EvaluationCase:
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    image = assets / f"input-{number}.jpg"
    image.write_bytes(f"image-{number}".encode())
    gold = assets / f"gold-{number}.json"
    gold_bytes = json.dumps({"target": number}, sort_keys=True).encode()
    gold.write_bytes(gold_bytes)
    return EvaluationCase(
        schema_version=1,
        case_id=f"case-{number}",
        doc_id=f"doc_{number}",
        page_id=f"p{number}",
        pages=(
            MappingProxyType(
                {
                    "page_id": f"p{number}",
                    "label": str(number),
                    "url": f"https://example.test/{number}.jpg",
                    "order": number,
                }
            ),
        ),
        inputs=MappingProxyType(
            {
                "page_image": CaseAsset(
                    sha256=_digest(image.read_bytes()),
                    path=f"assets/{image.name}",
                )
            }
        ),
        references=MappingProxyType(
            {
                "gold": CaseAsset(
                    sha256=_digest(gold_bytes),
                    path=f"assets/{gold.name}",
                )
            }
        ),
        strata=strata,
        license="test",
        adjudication=MappingProxyType({}),
        fingerprint=_digest(f"case-{number}-{strata}".encode()),
    )


def _suite(
    cases: tuple[EvaluationCase, ...],
    metric: Metric,
    *,
    hard_metric: Metric | None = None,
    operational_limits: tuple[MetricLimit, ...] = (),
    probes: tuple[DownstreamProbe, ...] = (),
    minimum_completed: int | None = None,
) -> EvaluationSuite:
    hard_limits = (
        (MetricLimit(hard_metric.name, None, 0.5, hard_metric),)
        if hard_metric is not None
        else ()
    )
    return EvaluationSuite(
        schema_version=1,
        id="read/workflow/v1",
        station="read",
        mission="test paired workflow",
        case_manifest="read/test.jsonl",
        cases=cases,
        primary_metrics=(PrimaryMetric(metric.name, "maximize", 0.01, 0.95, metric),),
        hard_limits=hard_limits,
        protected_slices=("protected",)
        if any("protected" in c.strata for c in cases)
        else (),
        slice_policy=SlicePolicy(minimum_cases=1, maximum_regression=0.0),
        operational_limits=operational_limits,
        judges=(),
        downstream_probes=probes,
        promotion=PromotionPolicy(
            minimum_completed_cases=minimum_completed or len(cases),
            paired_bootstrap_samples=200,
            seed=3477,
            require_all_hard_limits=True,
            require_all_downstream_probes=True,
        ),
        fingerprint="3" * 64,
        qualification_eligible=True,
    )


def _install_execution(monkeypatch, plans, calls, candidate_views):
    station = SimpleNamespace(
        name="read",
        grain="page",
        consumes=("page_list", "page_image"),
        optional_consumes=(),
        produces="page_transcription",
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.runner.registry.get",
        lambda station_name, variant: station,
    )

    class Executor:
        def execute(self, spec):
            root = Path(spec.library_root)
            visible = sorted(path.name for path in root.rglob("*") if path.is_file())
            candidate_views.append(tuple(visible))
            calls.append((spec.page_id, spec.config_fingerprint))
            plan = plans[(spec.config_fingerprint, spec.page_id)]
            output_path = artifact_path(
                spec.doc_id,
                "page_transcription",
                spec.page_id,
                root,
            )
            if plan.get("malformed"):
                atomic_write_json(output_path, {"doc_id": spec.doc_id})
            else:
                atomic_write_json(
                    output_path,
                    {
                        "doc_id": spec.doc_id,
                        "page_id": spec.page_id,
                        "text": "text",
                        "route": "full_page",
                        "regions": [],
                        "score": plan["score"],
                        "hard": plan.get("hard", 0.1),
                    },
                )
            return SimpleNamespace(
                output_path=str(output_path),
                tokens_in=11,
                tokens_out=7,
                cost_usd=plan.get("cost", 0.1),
            )

    monkeypatch.setattr(
        "palimpsest.factory.evaluation.runner.make_executor", lambda name: Executor()
    )


def _run(tmp_path, suite, baseline, challenger):
    store = EvaluationStore(tmp_path / "factory.db")
    result = run_evaluation(
        run_id="run-1",
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        store=store,
        run_root=tmp_path / "runs",
        asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
        executor="inline",
    )
    return store, result


@pytest.mark.parametrize(
    "run_id",
    (
        "",
        ".",
        "..",
        "../escape",
        "nested/run",
        r"nested\run",
        " leading-space",
        "trailing.",
        "CON",
        "nul.json",
        "x" * 256,
    ),
)
def test_workflow_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    case = _case(tmp_path, 1)
    metric = Metric("quality", MetricDirection.MAXIMIZE, lambda output, gold: 1.0)
    suite = _suite((case,), metric)
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    runs_root = tmp_path / "runs"

    with EvaluationStore(tmp_path / "factory.db") as store:
        with pytest.raises(ValueError, match="filesystem-safe identifier"):
            run_evaluation(
                run_id=run_id,
                suite=suite,
                baseline=baseline,
                challenger=challenger,
                store=store,
                run_root=runs_root,
                asset_resolver=filesystem_asset_resolver(
                    tmp_path, tmp_path / "objects"
                ),
                executor="inline",
            )

    assert not runs_root.exists()
    assert not (tmp_path / "escape").exists()


def test_workflow_keeps_gold_scorer_only_and_publishes_deterministic_paired_report(
    tmp_path, monkeypatch
):
    cases = (
        _case(tmp_path, 1, strata=("protected",)),
        _case(tmp_path, 2, strata=("protected",)),
    )
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    score_calls = []

    def score(output, gold):
        score_calls.append((output["score"], dict(gold), len(calls)))
        return output["score"]

    metric = Metric("quality", MetricDirection.MAXIMIZE, score)
    probe_calls = []

    def probe(paired_cases, evaluation_cases):
        probe_calls.append(
            (len(calls), tuple(case.case_id for case in evaluation_cases))
        )
        return {"status": "passed", "evidence": len(paired_cases)}

    suite = _suite(
        cases,
        metric,
        operational_limits=(
            MetricLimit("mean_cost_usd_per_case", None, 1.0, object()),
        ),
        probes=(DownstreamProbe("read-to-next/v1", probe),),
    )
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
        (baseline.fingerprint, "p2"): {"score": 0.5},
        (challenger.fingerprint, "p2"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)

    assert all("gold" not in name for view in candidate_views for name in view)
    assert all(call[2] == 4 for call in score_calls)
    assert [call[:2] for call in score_calls] == [
        (0.5, {"target": 1}),
        (0.8, {"target": 1}),
        (0.5, {"target": 2}),
        (0.8, {"target": 2}),
    ]
    assert probe_calls == [(4, ("case-1", "case-2"))]
    for index, case in enumerate(cases):
        pair = calls[index * 2 : index * 2 + 2]
        labels = tuple(
            "baseline" if fingerprint == baseline.fingerprint else "challenger"
            for _, fingerprint in pair
        )
        assert labels == _side_order("run-1", case.case_id)
        assert report["cases"][index]["side_order"] == list(labels)
    assert report["decision"] == "qualified"
    assert (
        report["aggregates"]["metrics"]["quality"]["comparison"]["decision"] == "pass"
    )
    assert (
        report["aggregates"]["operations"]["challenger"]["mean_cost_usd_per_case"]
        == 0.1
    )
    assert store.run("run-1").report_fingerprint == report["report_fingerprint"]

    with pytest.raises(RecordError, match="already exists"):
        run_evaluation(
            run_id="run-1",
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            store=store,
            run_root=tmp_path / "runs",
            asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
            executor="inline",
        )
    assert len(calls) == 4
    store.close()


def test_multiple_references_are_passed_to_scorer_by_reference_name(
    tmp_path, monkeypatch
):
    case = _case(tmp_path, 1)
    second_path = tmp_path / "assets" / "context.json"
    second_bytes = b'{"language":"la"}'
    second_path.write_bytes(second_bytes)
    case = replace(
        case,
        references=MappingProxyType(
            {
                **case.references,
                "context": CaseAsset(
                    sha256=_digest(second_bytes),
                    path="assets/context.json",
                ),
            }
        ),
        fingerprint="4" * 64,
    )
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    received_gold = []

    def score(output, gold):
        received_gold.append(gold)
        return output["score"]

    metric = Metric("quality", MetricDirection.MAXIMIZE, score)
    suite = _suite((case,), metric)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, _ = _run(tmp_path, suite, baseline, challenger)
    assert received_gold == [
        {"context": {"language": "la"}, "gold": {"target": 1}},
        {"context": {"language": "la"}, "gold": {"target": 1}},
    ]
    store.close()


def test_candidate_suite_validation_happens_before_run_creation(tmp_path):
    case = _case(tmp_path, 1)
    metric = Metric(
        "quality",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["score"],
    )
    suite = _suite((case,), metric)
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = replace(
        _candidate("translate/challenger", "b" * 64),
        station="translate",
    )
    store = EvaluationStore(tmp_path / "factory.db")

    with pytest.raises(RecordError, match="does not match suite station"):
        run_evaluation(
            run_id="run-1",
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            store=store,
            run_root=tmp_path / "runs",
            asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
            executor="inline",
        )

    assert store.run("run-1") is None
    assert not (tmp_path / "runs").exists()
    store.close()


def test_development_suite_cannot_auto_qualify(tmp_path, monkeypatch):
    case = _case(tmp_path, 1)
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    metric = Metric(
        "quality",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["score"],
    )
    suite = replace(_suite((case,), metric), qualification_eligible=False)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)

    assert report["decision"] == "rejected"
    assert "suite is not qualification eligible" in report["qualification"]["reasons"]
    store.close()


def test_one_moving_identity_requires_manual_review_with_fingerprint(
    tmp_path, monkeypatch
):
    case = _case(tmp_path, 1)
    baseline = _candidate("read/baseline", "a" * 64, moving=True)
    challenger = _candidate("read/challenger", "b" * 64)
    metric = Metric(
        "quality",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["score"],
    )
    suite = _suite((case,), metric)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)

    assert report["decision"] == "manual_review_required"
    assert (
        f"baseline identity requires reproducibility waiver: {baseline.fingerprint}"
        in report["qualification"]["reasons"]
    )
    store.close()


def test_two_moving_identities_remain_rejected(tmp_path, monkeypatch):
    case = _case(tmp_path, 1)
    baseline = _candidate("read/baseline", "a" * 64, moving=True)
    challenger = _candidate("read/challenger", "b" * 64, moving=True)
    metric = Metric(
        "quality",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["score"],
    )
    suite = _suite((case,), metric)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)

    assert report["decision"] == "rejected"
    reasons = report["qualification"]["reasons"]
    assert any(baseline.fingerprint in reason for reason in reasons)
    assert any(challenger.fingerprint in reason for reason in reasons)
    store.close()


def test_selected_subset_with_too_few_completed_cases_is_insufficient(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    metric = Metric(
        "quality",
        MetricDirection.MAXIMIZE,
        lambda output, gold: output["score"],
    )
    suite = _suite(cases, metric)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.8},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)
    store = EvaluationStore(tmp_path / "factory.db")

    result = run_evaluation(
        run_id="run-1",
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        store=store,
        run_root=tmp_path / "runs",
        asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
        executor="inline",
        cases=(cases[0],),
    )
    report = read_json(result.report_path)

    assert report["decision"] == "insufficient"
    assert any(
        reason.startswith("minimum completed cases not met")
        for reason in report["qualification"]["reasons"]
    )
    store.close()


def test_invalid_output_is_not_scored_and_failure_usage_and_unknowns_are_retained(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    scored = []

    def score(output, gold):
        scored.append(output["score"])
        return output["score"]

    metric = Metric("quality", MetricDirection.MAXIMIZE, score)
    suite = _suite(
        cases,
        metric,
        operational_limits=(
            MetricLimit("mean_cost_usd_per_case", None, 1.0, object()),
        ),
    )
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5, "cost": 0.2},
        (challenger.fingerprint, "p1"): {
            "score": 0.0,
            "cost": None,
            "malformed": True,
        },
        (baseline.fingerprint, "p2"): {"score": 0.6, "cost": 0.2},
        (challenger.fingerprint, "p2"): {"score": None, "cost": 0.3},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)
    failed_challenger = report["cases"][0]["challenger"]
    failed_observation = report["cases"][0]["observations"]["quality"]
    unknown_challenger = report["cases"][1]["challenger"]
    unknown_observation = report["cases"][1]["observations"]["quality"]

    assert scored == [0.5, 0.6, None]
    assert failed_challenger["succeeded"] is False
    assert failed_challenger["tokens_in"] == 11
    assert failed_challenger["tokens_out"] == 7
    assert failed_challenger["cost_usd"] is None
    assert failed_observation["candidate"] is None
    assert unknown_challenger["succeeded"] is True
    assert unknown_observation["candidate"] is None
    assert report["aggregates"]["reliability"]["challenger"]["failed_cases"] == 1
    assert (
        report["aggregates"]["operations"]["challenger"]["mean_cost_usd_per_case"]
        is None
    )
    assert report["aggregates"]["operations"]["challenger"]["known_cost_usd"] == 0.3
    assert report["aggregates"]["operations"]["unknown_cost"]["challenger"] is True
    assert report["decision"] == "unknown"
    assert any(
        "operational limit unknown" in reason
        for reason in report["qualification"]["reasons"]
    )
    store.close()


def test_hard_limit_protected_slice_and_moving_identity_each_block_qualification(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1, strata=("protected",)), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64, moving=True)
    quality = Metric(
        "quality", MetricDirection.MAXIMIZE, lambda output, gold: output["score"]
    )
    hard = Metric(
        "catastrophe", MetricDirection.MINIMIZE, lambda output, gold: output["hard"]
    )
    suite = _suite(
        cases,
        quality,
        hard_metric=hard,
        operational_limits=(
            MetricLimit("mean_cost_usd_per_case", None, 1.0, object()),
        ),
    )
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5, "hard": 0.0},
        (challenger.fingerprint, "p1"): {
            "score": 0.0,
            "hard": 1.0,
            "cost": None,
        },
        (baseline.fingerprint, "p2"): {"score": 0.5, "hard": 0.0},
        (challenger.fingerprint, "p2"): {"score": 1.0, "hard": 1.0},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    store, result = _run(tmp_path, suite, baseline, challenger)
    report = read_json(result.report_path)
    reasons = report["qualification"]["reasons"]

    assert report["decision"] == "rejected"
    assert report["aggregates"]["hard_limits"][0]["decision"] == "fail"
    assert report["aggregates"]["protected_slices"][0]["decision"] == "regression"
    assert "hard limit failed: catastrophe" in reasons
    assert "operational limit unknown: mean_cost_usd_per_case" in reasons
    assert "protected slice regressed: protected" in reasons
    assert (
        f"challenger identity requires reproducibility waiver: {challenger.fingerprint}"
        in reasons
    )
    store.close()


def test_internal_scoring_error_publishes_failed_report_without_hiding_error(
    tmp_path, monkeypatch
):
    case = _case(tmp_path, 1)
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)

    def broken_score(output, gold):
        raise RuntimeError("scorer exploded")

    metric = Metric("quality", MetricDirection.MAXIMIZE, broken_score)
    suite = _suite((case,), metric)
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5},
        (challenger.fingerprint, "p1"): {"score": 0.6},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)
    store = EvaluationStore(tmp_path / "factory.db")

    with pytest.raises(RuntimeError, match="scorer exploded"):
        run_evaluation(
            run_id="run-1",
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            store=store,
            run_root=tmp_path / "runs",
            asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
            executor="inline",
        )

    report = read_json(tmp_path / "runs" / "run-1" / "report.json")
    assert report["status"] == "failed"
    assert report["decision"] == "error"
    assert report["aggregates"]["failure"] == {
        "kind": "RuntimeError",
        "message": "scorer exploded",
    }
    assert store.run("run-1").status == "failed"
    store.close()


@pytest.mark.parametrize("value", [-0.01, float("inf"), float("nan"), True, "1"])
def test_invalid_cost_ceiling_is_rejected_before_run_creation(tmp_path, value):
    case = _case(tmp_path, 1)
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    suite = _suite(
        (case,),
        Metric("quality", MetricDirection.MAXIMIZE, lambda output, gold: 1.0),
    )
    store = EvaluationStore(tmp_path / "factory.db")

    with pytest.raises(ValueError, match="finite non-negative"):
        run_evaluation(
            run_id="run-1",
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            store=store,
            run_root=tmp_path / "runs",
            asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
            executor="inline",
            max_cost_usd=value,
        )

    assert store.run("run-1") is None
    assert not (tmp_path / "runs" / "run-1").exists()
    store.close()


def test_cost_ceiling_stops_before_next_pair_and_blocks_qualification(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    suite = _suite(
        cases,
        Metric(
            "quality",
            MetricDirection.MAXIMIZE,
            lambda output, gold: output["score"],
        ),
    )
    plans = {
        (baseline.fingerprint, "p1"): {"score": 0.5, "cost": 0.2},
        (challenger.fingerprint, "p1"): {"score": 0.8, "cost": 0.2},
        (baseline.fingerprint, "p2"): {"score": 0.5, "cost": 0.2},
        (challenger.fingerprint, "p2"): {"score": 0.8, "cost": 0.2},
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)
    store = EvaluationStore(tmp_path / "factory.db")

    result = run_evaluation(
        run_id="run-1",
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        store=store,
        run_root=tmp_path / "runs",
        asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
        executor="inline",
        max_cost_usd=0.4,
    )
    report = read_json(result.report_path)

    assert calls == [
        ("p1", baseline.fingerprint)
        if _side_order("run-1", "case-1")[0] == "baseline"
        else ("p1", challenger.fingerprint),
        ("p1", challenger.fingerprint)
        if _side_order("run-1", "case-1")[1] == "challenger"
        else ("p1", baseline.fingerprint),
    ]
    assert [case["case_id"] for case in report["cases"]] == ["case-1"]
    assert report["decision"] == "insufficient"
    assert report["aggregates"]["cost_ceiling"] == {
        "maximum_cost_usd": 0.4,
        "candidate_known_cost_usd": 0.4,
        "candidate_unknown_cost": False,
        "judge_known_cost_usd": 0,
        "judge_unknown_cost": False,
        "total_known_cost_usd": 0.4,
        "unknown_cost": False,
        "limit_reached": True,
        "limit_exceeded": False,
        "dispatch_stopped": True,
    }
    assert (
        "missing paired case observations: 1 of 2" in report["qualification"]["reasons"]
    )
    store.close()


def test_judge_and_candidate_spend_both_apply_to_dispatch_ceiling(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    judge = ResolvedJudge(
        schema_version=1,
        id="judge/fixed",
        model="judge-20260721",
        model_identity="fixed",
        prompt_name="judge/test",
        prompt_hash="4" * 64,
        response_schema="pairwise/v1",
        params=MappingProxyType({}),
        fingerprint="5" * 64,
    )
    suite = replace(
        _suite(
            cases,
            Metric(
                "quality",
                MetricDirection.MAXIMIZE,
                lambda output, gold: output["score"],
            ),
        ),
        judges=(JudgeMetric("preference", judge, object()),),
    )
    plans = {
        (candidate.fingerprint, f"p{number}"): {"score": score, "cost": 0.1}
        for number in (1, 2)
        for candidate, score in ((baseline, 0.5), (challenger, 0.8))
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)

    def paid_judge(
        runner,
        active_suite,
        selected,
        outcomes,
        active_baseline,
        active_challenger,
        executor,
        bindings=None,
    ):
        binding = tuple(bindings or active_suite.judges)[0]
        return tuple(
            {
                "case_id": outcome.case_id,
                "metric": binding.metric,
                "judge_id": binding.judge.id,
                "judge_fingerprint": binding.judge.fingerprint,
                "model": binding.judge.model,
                "status": "completed",
                "order": {"A": "baseline", "B": "challenger", "seed": 1},
                "winner": "challenger",
                "response": {
                    "winner": "B",
                    "confidence": 0.9,
                    "reason": "better",
                    "failure_flags": [],
                },
                "confidence": 0.9,
                "reason": "better",
                "failure_flags": [],
                "usage": {
                    "prompt_tokens": 10,
                    "output_tokens": 5,
                    "thought_tokens": 0,
                    "total_tokens": 15,
                    "cost_usd": 0.3,
                },
                "error_kind": None,
                "error_message": None,
            }
            for outcome in outcomes
        )

    monkeypatch.setattr("palimpsest.factory.evaluation.runner._run_judges", paid_judge)
    store = EvaluationStore(tmp_path / "factory.db")
    result = run_evaluation(
        run_id="run-1",
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        store=store,
        run_root=tmp_path / "runs",
        asset_resolver=filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
        executor="inline",
        max_cost_usd=0.4,
        judge_executor=object(),
    )
    report = read_json(result.report_path)
    costs = report["aggregates"]["cost_ceiling"]

    assert len(calls) == 2
    assert costs["candidate_known_cost_usd"] == 0.2
    assert costs["judge_known_cost_usd"] == 0.3
    assert costs["total_known_cost_usd"] == 0.5
    assert costs["limit_exceeded"] is True
    assert costs["dispatch_stopped"] is True
    assert report["decision"] == "rejected"
    store.close()


def test_resume_reuses_complete_pairs_reruns_incomplete_and_rejects_drift(
    tmp_path, monkeypatch
):
    cases = (_case(tmp_path, 1), _case(tmp_path, 2))
    baseline = _candidate("read/baseline", "a" * 64)
    challenger = _candidate("read/challenger", "b" * 64)
    suite = _suite(
        cases,
        Metric(
            "quality",
            MetricDirection.MAXIMIZE,
            lambda output, gold: output["score"],
        ),
    )
    plans = {
        (candidate.fingerprint, f"p{number}"): {"score": score, "cost": 0.1}
        for number in (1, 2)
        for candidate, score in ((baseline, 0.5), (challenger, 0.8))
    }
    calls, candidate_views = [], []
    _install_execution(monkeypatch, plans, calls, candidate_views)
    from palimpsest.factory.evaluation import runner as runner_module

    clock = 0

    def deterministic_clock():
        nonlocal clock
        clock += 1
        return float(clock)

    monkeypatch.setattr(runner_module.time, "perf_counter", deterministic_clock)

    base_factory = runner_module.make_executor
    interrupted = False

    class InterruptingExecutor:
        def execute(self, spec):
            nonlocal interrupted
            if spec.page_id == "p2" and not interrupted:
                interrupted = True
                calls.append((spec.page_id, spec.config_fingerprint))
                raise KeyboardInterrupt("operator interruption")
            return base_factory("inline").execute(spec)

    monkeypatch.setattr(
        runner_module, "make_executor", lambda name: InterruptingExecutor()
    )
    store = EvaluationStore(tmp_path / "factory.db")
    arguments = {
        "run_id": "run-1",
        "suite": suite,
        "baseline": baseline,
        "challenger": challenger,
        "store": store,
        "run_root": tmp_path / "runs",
        "asset_resolver": filesystem_asset_resolver(tmp_path, tmp_path / "objects"),
        "executor": "inline",
    }

    with pytest.raises(KeyboardInterrupt, match="operator interruption"):
        run_evaluation(**arguments)

    assert store.run("run-1").status == "running"
    assert not (tmp_path / "runs" / "run-1" / "report.json").exists()
    assert sum(page == "p1" for page, _ in calls) == 2

    with pytest.raises(ValueError, match="store identity drift"):
        run_evaluation(
            **{
                **arguments,
                "challenger": replace(challenger, fingerprint="c" * 64),
                "resume": "run-1",
            }
        )
    with pytest.raises(ValueError, match="store identity drift"):
        run_evaluation(
            **{
                **arguments,
                "suite": replace(suite, fingerprint="6" * 64),
                "resume": "run-1",
            }
        )
    case_identity_drift = replace(cases[1], fingerprint="7" * 64)
    with pytest.raises(ValueError, match="run identity drift"):
        run_evaluation(
            **{
                **arguments,
                "suite": replace(
                    suite,
                    cases=(cases[0], case_identity_drift),
                ),
                "cases": (cases[0], case_identity_drift),
                "resume": "run-1",
            }
        )
    with pytest.raises(ValueError, match="max-cost identity drift"):
        run_evaluation(**arguments, resume="run-1", max_cost_usd=1.0)
    with pytest.raises(ValueError, match="run identity drift"):
        run_evaluation(
            **{
                **arguments,
                "executor": "subprocess",
                "resume": "run-1",
            }
        )
    ambiguous = tmp_path / "runs" / "run-1" / "cases" / "case-not-from-manifest"
    ambiguous.mkdir()
    with pytest.raises(ValueError, match="Ambiguous partial evaluation state"):
        run_evaluation(**arguments, resume="run-1")
    ambiguous.rmdir()
    drift_case = replace(
        cases[1],
        pages=(
            MappingProxyType(
                {
                    **dict(cases[1].pages[0]),
                    "label": "changed input identity",
                }
            ),
        ),
    )
    with pytest.raises(ValueError, match="run identity drift"):
        run_evaluation(
            **{
                **arguments,
                "suite": replace(suite, cases=(cases[0], drift_case)),
                "cases": (cases[0], drift_case),
                "resume": "run-1",
            }
        )
    assert store.run("run-1").status == "running"

    result = run_evaluation(**arguments, resume="run-1")
    report = read_json(result.report_path)

    assert [case["case_id"] for case in report["cases"]] == ["case-1", "case-2"]
    assert sum(page == "p1" for page, _ in calls) == 2
    assert sum(page == "p2" for page, _ in calls) == 3
    assert report["aggregates"]["cost_ceiling"]["candidate_known_cost_usd"] == 0.4
    assert report["aggregates"]["observed_cases"] == 2
    assert report["decision"] == "qualified"
    assert len(calls) == 5

    full_store = EvaluationStore(tmp_path / "full-factory.db")
    full_result = run_evaluation(
        **{
            **arguments,
            "store": full_store,
            "run_root": tmp_path / "full-runs",
        }
    )
    full_report = read_json(full_result.report_path)

    def canonical_evidence(value, run_path):
        if isinstance(value, dict):
            return {
                key: canonical_evidence(item, run_path)
                for key, item in value.items()
                if key not in {"started_at", "finished_at", "report_fingerprint"}
            }
        if isinstance(value, list):
            return [canonical_evidence(item, run_path) for item in value]
        if isinstance(value, str):
            return value.replace(str(run_path.resolve()), "<RUN_ROOT>").replace(
                "attempt-0002", "attempt-0001"
            )
        return value

    assert canonical_evidence(report, tmp_path / "runs") == canonical_evidence(
        full_report,
        tmp_path / "full-runs",
    )
    full_store.close()
    calls_before_terminal_rejection = len(calls)

    with pytest.raises(ValueError, match="Terminal evaluation run cannot be resumed"):
        run_evaluation(**arguments, resume="run-1")
    assert len(calls) == calls_before_terminal_rejection
    store.close()
