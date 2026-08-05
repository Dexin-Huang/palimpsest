"""Exodia evaluator response contract: constraints and asi side-information."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from palimpsest.factory.evaluation import exodia_evaluator
from palimpsest.factory.evaluation.suite import MetricLimit, PrimaryMetric

_CONTRACT_KEYS = {
    "metrics",
    "cost",
    "latencyMs",
    "failureClass",
    "evidenceIds",
    "traceArtifactRefs",
    "evaluatorArtifactRefs",
    "constraints",
    "asi",
}
_ASI_FIELDS = {
    "submission_status",
    "dominant_failure",
    "hard_limit_values",
    "metric_values",
    "case_id",
    "strata",
    "baseline_succeeded",
    "challenger_succeeded",
    "unknown_cost",
}


def _suite(
    hard_limits: tuple[MetricLimit, ...],
    primary_metrics: tuple[PrimaryMetric, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(hard_limits=hard_limits, primary_metrics=primary_metrics)


def _limit_row(
    metric: str,
    *,
    value: float | None,
    minimum: float | None = None,
    maximum: float | None = None,
    decision: str = "pass",
) -> dict[str, object]:
    return {
        "metric": metric,
        "value": value,
        "minimum": minimum,
        "maximum": maximum,
        "decision": decision,
    }


def _report(
    *,
    observations: dict[str, dict[str, float]] | None = None,
    hard_limits: list[dict[str, object]],
    baseline_ok: bool = True,
    challenger_ok: bool = True,
    unknown_cost: bool = False,
    challenger_process_stats: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "report_fingerprint": "a" * 64,
        "cases": [
            {
                "case_id": "case-1",
                "baseline": {
                    "succeeded": baseline_ok,
                    "error_kind": "infra",
                    "error_message": "baseline collapsed",
                    "output_fingerprint": None,
                },
                "challenger": {
                    "succeeded": challenger_ok,
                    "error_kind": "target",
                    "error_message": "challenger collapsed",
                    "output_fingerprint": None,
                    "process_stats": challenger_process_stats,
                },
                "observations": dict(observations or {}),
            }
        ],
        "aggregates": {
            "cost_ceiling": {
                "total_known_cost_usd": 0.25,
                "unknown_cost": unknown_cost,
            },
            "hard_limits": hard_limits,
        },
    }


def _observe(report: dict[str, object], suite: SimpleNamespace) -> dict[str, object]:
    return exodia_evaluator._observation_from_report(
        report=report,
        suite=suite,
        case_id="case-1",
        strata=("zh", "printed"),
        elapsed_ms=12.5,
        evaluator_artifacts=[
            {"id": "palimpsest-suite", "sha256": "sha256:" + "b" * 64}
        ],
    )


def test_hard_limit_constraints_bounds_and_missing_metric() -> None:
    limits = (
        MetricLimit("invented_character_rate", None, 0.02, None),
        MetricLimit("coverage", 0.8, None, None),
        MetricLimit("contamination_rate", None, 0.0, None),
    )

    at_bounds = exodia_evaluator._hard_limit_constraints(
        limits,
        {"invented_character_rate": 0.02, "coverage": 0.8, "contamination_rate": 0.0},
    )
    assert at_bounds == {
        "contamination_rate": "pass",
        "coverage": "pass",
        "invented_character_rate": "pass",
    }

    violated = exodia_evaluator._hard_limit_constraints(
        limits,
        {"invented_character_rate": 0.021, "coverage": 0.79, "contamination_rate": 0.5},
    )
    assert violated == {
        "contamination_rate": "fail",
        "coverage": "fail",
        "invented_character_rate": "fail",
    }

    sparse = exodia_evaluator._hard_limit_constraints(
        limits, {"coverage": float("nan"), "contamination_rate": 0.0}
    )
    assert sparse == {
        "contamination_rate": "pass",
        "coverage": "fail",
        "invented_character_rate": "fail",
    }


def test_validate_asi_rejects_non_finite_and_non_json_values() -> None:
    exodia_evaluator._validate_asi(
        {
            "submission_status": "completed",
            "dominant_failure": None,
            "hard_limit_values": {"coverage": 0.9},
            "strata": ["printed", "zh"],
            "count": 3,
            "flag": True,
        },
        field="asi",
    )

    with pytest.raises(AssertionError, match="is not finite"):
        exodia_evaluator._validate_asi({"bad": float("nan")}, field="asi")
    with pytest.raises(AssertionError, match="is not finite"):
        exodia_evaluator._validate_asi({"nested": {"bad": float("inf")}}, field="asi")
    with pytest.raises(AssertionError, match="is not a string"):
        exodia_evaluator._validate_asi({"values": {1: 0.5}}, field="asi")
    with pytest.raises(AssertionError, match="is not plain JSON"):
        exodia_evaluator._validate_asi({"opaque": object()}, field="asi")


def test_observation_carries_constraints_and_asi() -> None:
    suite = _suite(
        hard_limits=(
            MetricLimit("invented_character_rate", None, 0.02, None),
            MetricLimit("coverage", 0.8, None, None),
        ),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    report = _report(
        observations={
            "similarity": {"candidate": 0.9},
            "invented_character_rate": {"candidate": 0.01},
            "coverage": {"candidate": 0.85},
        },
        hard_limits=[
            _limit_row("invented_character_rate", value=0.01, maximum=0.02),
            _limit_row("coverage", value=0.85, minimum=0.8),
        ],
    )

    result = _observe(report, suite)

    assert exodia_evaluator._OUTPUT_KEYS == _CONTRACT_KEYS
    assert set(result) == _CONTRACT_KEYS
    assert result["failureClass"] is None
    assert result["constraints"] == {
        "coverage": "pass",
        "invented_character_rate": "pass",
    }

    asi = result["asi"]
    assert set(asi) == _ASI_FIELDS
    assert asi["submission_status"] == "completed"
    assert asi["dominant_failure"] is None
    assert asi["hard_limit_values"] == {
        "coverage": 0.85,
        "invented_character_rate": 0.01,
    }
    assert asi["metric_values"] == {
        "coverage": 0.85,
        "invented_character_rate": 0.01,
        "similarity": 0.9,
    }
    assert asi["case_id"] == "case-1"
    assert asi["strata"] == ["printed", "zh"]
    assert asi["baseline_succeeded"] is True
    assert asi["challenger_succeeded"] is True
    assert asi["unknown_cost"] is False
    assert "process_stats" not in asi

    round_tripped = json.loads(exodia_evaluator._canonical_json(result))
    assert set(round_tripped) == _CONTRACT_KEYS


def test_observation_missing_hard_limit_metric_fails_conservatively() -> None:
    suite = _suite(
        hard_limits=(MetricLimit("invented_character_rate", None, 0.02, None),),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    report = _report(
        observations={"similarity": {"candidate": 0.9}},
        hard_limits=[
            _limit_row(
                "invented_character_rate", value=None, maximum=0.02, decision="unknown"
            )
        ],
    )

    result = _observe(report, suite)

    assert result["constraints"] == {"invented_character_rate": "fail"}
    assert result["failureClass"] == "evaluation-integrity"
    asi = result["asi"]
    assert asi["dominant_failure"] == "unknown-hard-limit"
    assert asi["hard_limit_values"] == {}
    assert asi["metric_values"] == {"similarity": 0.9}


def test_observation_challenger_failure_shapes_constraints_and_asi() -> None:
    suite = _suite(
        hard_limits=(MetricLimit("invented_character_rate", None, 0.02, None),),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    report = _report(
        observations={},
        hard_limits=[
            _limit_row(
                "invented_character_rate", value=None, maximum=0.02, decision="unknown"
            )
        ],
        challenger_ok=False,
    )

    result = _observe(report, suite)

    assert result["failureClass"] == "target"
    assert result["constraints"] == {"invented_character_rate": "fail"}
    assert result["metrics"] == {"quality": 0.0, "values": {}}
    asi = result["asi"]
    assert asi["submission_status"] == "challenger-failed"
    assert asi["dominant_failure"] == "challenger"
    assert asi["metric_values"] == {}
    assert asi["hard_limit_values"] == {}
    assert asi["challenger_succeeded"] is False
    assert asi["failure_detail"] == {
        "side": "challenger",
        "kind": "target",
        "message": "challenger collapsed",
    }


def test_observation_surfaces_challenger_process_stats_in_asi() -> None:
    suite = _suite(
        hard_limits=(MetricLimit("invented_character_rate", None, 0.02, None),),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    report = _report(
        observations={
            "similarity": {"candidate": 0.9},
            "invented_character_rate": {"candidate": 0.01},
        },
        hard_limits=[
            _limit_row("invented_character_rate", value=0.01, maximum=0.02),
        ],
        challenger_process_stats={
            "assistant_turns": 14,
            "tool_calls": 41,
            "output_tokens": 9001,
        },
    )

    result = _observe(report, suite)

    asi = result["asi"]
    assert set(asi) == _ASI_FIELDS | {"process_stats"}
    assert asi["process_stats"] == {
        "assistant_turns": 14,
        "tool_calls": 41,
        "output_tokens": 9001,
    }
    round_tripped = json.loads(exodia_evaluator._canonical_json(result))
    assert round_tripped["asi"]["process_stats"] == {
        "assistant_turns": 14,
        "tool_calls": 41,
        "output_tokens": 9001,
    }


def test_observation_rejects_malformed_challenger_process_stats() -> None:
    suite = _suite(
        hard_limits=(MetricLimit("invented_character_rate", None, 0.02, None),),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    hard_limits = [_limit_row("invented_character_rate", value=0.01, maximum=0.02)]
    observations = {
        "similarity": {"candidate": 0.9},
        "invented_character_rate": {"candidate": 0.01},
    }

    for bad_stats in (
        {"assistant_turns": 1},
        {"assistant_turns": -1, "tool_calls": 0},
        {"assistant_turns": 1.5, "tool_calls": 0},
        {"assistant_turns": 1, "tool_calls": 0, "wall_clock": 12},
        "not an object",
    ):
        report = _report(
            observations=observations,
            hard_limits=hard_limits,
            challenger_process_stats=bad_stats,
        )
        with pytest.raises(exodia_evaluator.EvaluationIntegrityError):
            _observe(report, suite)


def test_observation_surfaces_error_structure_in_asi() -> None:
    suite = _suite(
        hard_limits=(MetricLimit("invented_character_rate", None, 0.02, None),),
        primary_metrics=(PrimaryMetric("similarity", "maximize", 0.01, 0.95, None),),
    )
    report = _report(
        observations={
            "similarity": {"candidate": 0.9},
            "invented_character_rate": {"candidate": 0.01},
        },
        hard_limits=[_limit_row("invented_character_rate", value=0.01, maximum=0.02)],
    )
    structure = {
        "totals": {"reference_characters": 12, "errors": 3},
        "lines": {"missing_lines": 1},
    }

    result = exodia_evaluator._observation_from_report(
        report=report,
        suite=suite,
        case_id="case-1",
        strata=("zh", "printed"),
        elapsed_ms=12.5,
        evaluator_artifacts=[
            {"id": "palimpsest-suite", "sha256": "sha256:" + "b" * 64}
        ],
        error_structure=structure,
    )

    asi = result["asi"]
    assert set(asi) == _ASI_FIELDS | {"error_structure"}
    assert asi["error_structure"] == structure

    without = _observe(report, suite)
    assert "error_structure" not in without["asi"]


def _candidate(
    *,
    runtime_requirements: dict[str, str] | None = None,
) -> dict[str, object]:
    harness_config = '{"extensionBundle":"harness-baseline"}'
    digest = (
        "sha256:"
        + hashlib.sha256(
            b"@exodia/harness-config:v1\x00" + harness_config.encode("utf-8")
        ).hexdigest()
    )
    return {
        "ref": {"id": "cand", "version": "1", "digest": "sha256:" + "a" * 64},
        "specialty": "manuscript.diplomatic-transcription",
        "modelRef": "openai-codex/gpt-5.6-luna",
        "harnessConfig": harness_config,
        "harnessDigest": digest,
        "runtimeRequirements": runtime_requirements or {},
        "lineage": {
            "id": "lineage",
            "derivation": "seed",
            "sourceRevisions": [],
            "changeArtifactRefs": [],
        },
        "createdBy": "tests",
        "createdAt": "2026-07-29T00:00:00.000Z",
    }


def _harness(**overrides: object) -> dict[str, object]:
    return {
        "extensionBundle": "harness-baseline",
        "model": "openai-codex/gpt-5.6-luna",
        "variant": "omp_extension",
        **overrides,
    }


def test_validate_candidate_accepts_bound_draft_model() -> None:
    candidate = _candidate(
        runtime_requirements={"harness.tool.gemini_3_6_flash_draft_v1": "required"}
    )
    harness_value = _harness(
        toolBindings=[
            {
                "id": "gemini_3_6_flash_draft_v1",
                "kind": "draft_model",
                "model": "google/gemini-3.6-flash",
            }
        ]
    )

    _validated_candidate, harness = exodia_evaluator._validate_candidate(
        candidate, harness_value
    )

    assert harness["toolBindings"] == harness_value["toolBindings"]


def test_validate_candidate_accepts_toolless_rig() -> None:
    _validated_candidate, harness = exodia_evaluator._validate_candidate(
        _candidate(), _harness()
    )

    assert "toolBindings" not in harness


def test_validate_candidate_rejects_missing_station_variant() -> None:
    harness = _harness()
    del harness["variant"]

    with pytest.raises(exodia_evaluator.EvaluationIntegrityError):
        exodia_evaluator._validate_candidate(_candidate(), harness)


def test_validate_candidate_accepts_station_variant() -> None:
    _validated_candidate, harness = exodia_evaluator._validate_candidate(
        _candidate(),
        _harness(variant="omp_toolbelt6"),
    )

    assert harness["variant"] == "omp_toolbelt6"


def test_validate_candidate_rejects_empty_station_variant() -> None:
    with pytest.raises(exodia_evaluator.EvaluationIntegrityError):
        exodia_evaluator._validate_candidate(_candidate(), _harness(variant=""))


@pytest.mark.parametrize(
    "bindings",
    [
        [],
        [
            {
                "id": "gemini_3_6_flash_draft_v1",
                "kind": "draft_model",
                "model": "google/gemini-3.6-flash",
            },
            {
                "id": "gemini_3_6_flash_draft_v1",
                "kind": "draft_model",
                "model": "google/gemini-3.6-flash",
            },
        ],
        [
            {
                "id": "gemini_3_6_flash_draft_v1",
                "kind": "unknown",
                "model": "google/gemini-3.6-flash",
            }
        ],
        [{"id": "gemini_3_6_flash_draft_v1", "kind": "draft_model"}],
        [1],
    ],
)
def test_validate_candidate_rejects_bad_tool_bindings(
    bindings: list[object],
) -> None:
    candidate = _candidate(
        runtime_requirements={"harness.tool.gemini_3_6_flash_draft_v1": "required"}
    )

    with pytest.raises(exodia_evaluator.EvaluationIntegrityError):
        exodia_evaluator._validate_candidate(
            candidate,
            _harness(toolBindings=bindings),
        )


def test_validate_candidate_rejects_tool_binding_not_required_by_rig() -> None:
    with pytest.raises(
        exodia_evaluator.EvaluationIntegrityError,
        match="do not match CandidateRig runtime requirements",
    ):
        exodia_evaluator._validate_candidate(
            _candidate(),
            _harness(
                toolBindings=[
                    {
                        "id": "gemini_3_6_flash_draft_v1",
                        "kind": "draft_model",
                        "model": "google/gemini-3.6-flash",
                    }
                ]
            ),
        )


def _retention_report(runs_root, fingerprint: str) -> dict[str, object]:
    output = runs_root / "challenger" / "transcription.json"
    report = _report(hard_limits=[])
    case = report["cases"][0]
    for side in ("baseline", "challenger"):
        case[side]["output_path"] = str(output)
        case[side]["output_fingerprint"] = fingerprint
    return report


def test_retain_outputs_writes_verified_sides(tmp_path, monkeypatch) -> None:
    from palimpsest.factory.core.artifact import content_fingerprint

    runs_root = tmp_path / "runs"
    (runs_root / "challenger").mkdir(parents=True)
    output = runs_root / "challenger" / "transcription.json"
    output.write_text('{"text": "天地玄黃", "layers": []}', encoding="utf-8")
    retain_root = tmp_path / "retained"
    monkeypatch.setenv("PALIMPSEST_RETAIN_OUTPUTS", str(retain_root))

    report = _retention_report(runs_root, content_fingerprint(output))
    exodia_evaluator._retain_outputs(
        report,
        case_id="case-1",
        candidate_ref="rig-123",
        runs_root=runs_root,
    )

    written = sorted((retain_root / "case-1").glob("*.json"))
    assert [path.name.split("-")[0] for path in written] == ["baseline", "challenger"]
    challenger_payload = json.loads(written[1].read_text(encoding="utf-8"))
    assert challenger_payload["record"]["text"] == "天地玄黃"
    assert challenger_payload["candidate_ref"] == "rig-123"
    baseline_payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert baseline_payload["candidate_ref"] is None


def test_retain_outputs_skips_drifted_fingerprint(tmp_path, monkeypatch) -> None:
    runs_root = tmp_path / "runs"
    (runs_root / "challenger").mkdir(parents=True)
    output = runs_root / "challenger" / "transcription.json"
    output.write_text('{"text": "天地玄黃"}', encoding="utf-8")
    retain_root = tmp_path / "retained"
    monkeypatch.setenv("PALIMPSEST_RETAIN_OUTPUTS", str(retain_root))

    report = _retention_report(runs_root, "0" * 64)
    exodia_evaluator._retain_outputs(
        report,
        case_id="case-1",
        candidate_ref="rig-123",
        runs_root=runs_root,
    )

    assert not retain_root.exists()


def test_retain_outputs_inactive_without_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PALIMPSEST_RETAIN_OUTPUTS", raising=False)
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    exodia_evaluator._retain_outputs(
        _report(hard_limits=[]),
        case_id="case-1",
        candidate_ref="rig-123",
        runs_root=runs_root,
    )

    assert list(tmp_path.iterdir()) == [runs_root]
