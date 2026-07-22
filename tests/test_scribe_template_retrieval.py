from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXPERIMENT = ROOT / "experiments" / "scribe_template_retrieval"
sys.path.insert(0, str(EXPERIMENT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate = load_module("scribe_template_candidate", EXPERIMENT / "candidate.py")
evaluation = load_module("scribe_template_evaluation", EXPERIMENT / "evaluate.py")


def test_style_reference_selection_is_deterministic_and_excludes_target() -> None:
    records = [
        {
            "crop_id": f"crop-{index}",
            "claimed_char": char,
            "line_index": index,
            "cell_cost": cost,
            "line_cost": cost + 0.1,
        }
        for index, (char, cost) in enumerate(
            [("甲", 0.1), ("乙", 0.2), ("丙", 0.3), ("丁", 0.4), ("戊", 0.5)]
        )
    ]

    first = candidate.choose_references(records, "甲")
    second = candidate.choose_references(list(reversed(records)), "甲")

    assert [record["crop_id"] for record in first] == [
        "crop-1",
        "crop-2",
        "crop-3",
        "crop-4",
    ]
    assert [record["crop_id"] for record in second] == [
        record["crop_id"] for record in first
    ]
    assert all(record["claimed_char"] != "甲" for record in first)


def test_paired_block_bootstrap_preserves_positive_line_level_effect(monkeypatch) -> None:
    monkeypatch.setattr(evaluation, "BOOTSTRAP_SAMPLES", 200)
    baseline = [
        {"crop_id": f"crop-{index}", "line_index": index // 2, "top1": False}
        for index in range(6)
    ]
    challenger = [
        {"crop_id": f"crop-{index}", "line_index": index // 2, "top1": True}
        for index in range(6)
    ]

    result = evaluation.paired_block_bootstrap(
        baseline,
        challenger,
        {item["crop_id"] for item in baseline},
    )

    assert result == {
        "delta": 1.0,
        "ci95": [1.0, 1.0],
        "blocks": 3,
        "samples": 200,
    }


def test_fixed_coverage_risk_uses_prediction_margin() -> None:
    observations = [
        {"crop_id": "a", "prediction_margin": 0.9, "top1": True},
        {"crop_id": "b", "prediction_margin": 0.8, "top1": False},
        {"crop_id": "c", "prediction_margin": 0.2, "top1": False},
        {"crop_id": "d", "prediction_margin": 0.1, "top1": True},
    ]

    result = evaluation.risk_at_coverage(observations, 0.5)

    assert result == {
        "coverage": 0.5,
        "accepted": 2,
        "risk": 0.5,
        "minimum_margin": 0.8,
    }
