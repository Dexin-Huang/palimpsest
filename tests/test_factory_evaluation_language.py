from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.metrics import MetricDirection, MetricRegistry
from palimpsest.factory.evaluation.probes import trusted_probes
from palimpsest.factory.evaluation.station_metrics.language import (
    register_language_metrics,
)
from palimpsest.factory.evaluation.suite import (
    CaseAsset,
    load_case_manifest,
    load_suite,
    validate_candidate_suite,
)

FACTORY_ROOT = Path(__file__).parents[1] / "palimpsest" / "factory"
EVALUATION_ROOT = FACTORY_ROOT / "evaluation"
STATIONS = ("translate", "survey", "reconstruct")
METRICS = {
    "translate": {
        "translation_passage_coverage": MetricDirection.MAXIMIZE,
        "translation_omission_rate": MetricDirection.MINIMIZE,
        "translation_uncertainty_retention": MetricDirection.MAXIMIZE,
        "translation_terminology_consistency": MetricDirection.MAXIMIZE,
    },
    "survey": {
        "survey_structural_coverage": MetricDirection.MAXIMIZE,
        "survey_terminology_coverage": MetricDirection.MAXIMIZE,
        "survey_entity_coverage": MetricDirection.MAXIMIZE,
        "survey_language_script_identification": MetricDirection.MAXIMIZE,
        "survey_downstream_brief_utility": MetricDirection.MAXIMIZE,
    },
    "reconstruct": {
        "reconstruction_section_order": MetricDirection.MAXIMIZE,
        "reconstruction_page_source_linkage": MetricDirection.MAXIMIZE,
        "reconstruction_no_invented_sections": MetricDirection.MAXIMIZE,
        "reconstruction_traceability": MetricDirection.MAXIMIZE,
    },
}
OUTPUT_HASHES = {
    "translate": {
        "good_output.json": "0c46cc117d548308ae584b52870cecbaf5e1f9ced70e0bba343706cf1c3c4b52",
        "broken_output.json": "8d40854702a65cb7a17d3b10444215e9c6dc25be2b4ed6037aa560dbd84eacd2",
    },
    "survey": {
        "good_output.json": "184dc8d419e3a061bdad6ed737a372ef28787d86fbb83ee41d1e7033db48c7d2",
        "broken_output.json": "bb8125090375a71992532562f37c8471754a150d24a815eec06877940c343d92",
    },
    "reconstruct": {
        "good_output.json": "ee564f4044f22ad7510a428c35ec8947cb8240b976cc42fc0b276fbe24382e41",
        "broken_output.json": "7244031cab676319ee3c536c6441a8da55e775dc2b31c42447bf3a1986c6e096",
    },
}


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _asset_paths(asset: CaseAsset | Mapping[str, CaseAsset]) -> set[str]:
    if isinstance(asset, Mapping):
        return {item.path for item in asset.values() if item.path is not None}
    return {asset.path} if asset.path is not None else set()


def _asset_hashes(asset: CaseAsset | Mapping[str, CaseAsset]) -> set[str]:
    if isinstance(asset, Mapping):
        return {item.sha256 for item in asset.values()}
    return {asset.sha256}


def test_every_language_metric_separates_conforming_and_broken_fixtures() -> None:
    registry = MetricRegistry()
    register_language_metrics(registry)

    expected = {
        name for station_metrics in METRICS.values() for name in station_metrics
    }
    assert {metric.name for metric in registry.all()} == expected

    for station, definitions in METRICS.items():
        gold = _json(EVALUATION_ROOT / "gold" / station / "reference.json")
        conforming = _json(EVALUATION_ROOT / "gold" / station / "good_output.json")
        broken = _json(EVALUATION_ROOT / "gold" / station / "broken_output.json")
        for name, direction in definitions.items():
            metric = registry.get(name)
            assert metric.direction is direction
            good_score = metric.observe(conforming, gold)
            broken_score = metric.observe(broken, gold)
            assert good_score is not None and broken_score is not None
            if direction is MetricDirection.MAXIMIZE:
                assert good_score == 1.0
                assert broken_score < good_score
            else:
                assert good_score == 0.0
                assert broken_score > good_score


def test_language_resources_load_strictly_and_match_station_sockets() -> None:
    registry = MetricRegistry()
    register_language_metrics(registry)

    for station in STATIONS:
        manifest_path = EVALUATION_ROOT / "cases" / station / "development.jsonl"
        direct_cases = load_case_manifest(manifest_path, asset_root=EVALUATION_ROOT)
        suite = load_suite(
            EVALUATION_ROOT / "suites" / station / "development.yaml",
            metric_resolver=registry,
            probe_resolver=trusted_probes(),
            judge_resolver={},
        )
        candidate = load_candidate(
            FACTORY_ROOT / "candidates" / station / "current.yaml"
        )

        assert suite.cases == direct_cases
        assert candidate.variant == "default"
        assert candidate.model == "token-plan/qwen3.8-max"
        assert candidate.can_auto_qualify
        assert not suite.qualification_eligible
        assert not suite.can_auto_qualify
        assert not suite.judges
        assert [probe.id for probe in suite.downstream_probes] == (
            ["survey-to-translate/v1"] if station == "survey" else []
        )
        assert suite.promotion.minimum_completed_cases == len(suite.cases) == 1
        assert suite.promotion.paired_bootstrap_samples == 1
        assert suite.promotion.require_all_hard_limits is False
        validate_candidate_suite(candidate, suite)

        case = suite.cases[0]
        for kind, asset in case.inputs.items():
            assets = asset.values() if isinstance(asset, Mapping) else (asset,)
            for item in assets:
                assert item.path is not None
                validate_payload(
                    kind,
                    _json(EVALUATION_ROOT / item.path),
                    expected_doc_id=case.doc_id,
                )

        assert set(case.references) == {"gold"}
        for fixture_name, expected_hash in OUTPUT_HASHES[station].items():
            output_path = EVALUATION_ROOT / "gold" / station / fixture_name
            assert hashlib.sha256(output_path.read_bytes()).hexdigest() == expected_hash
            validate_payload(
                candidate.produces,
                _json(output_path),
                expected_doc_id=case.doc_id,
            )


def test_scorer_gold_is_reference_only_and_never_an_input() -> None:
    for station in STATIONS:
        cases = load_case_manifest(
            EVALUATION_ROOT / "cases" / station / "development.jsonl",
            asset_root=EVALUATION_ROOT,
        )
        for case in cases:
            gold = case.references["gold"]
            assert gold.path is not None and gold.path.startswith(f"gold/{station}/")
            input_paths = set().union(
                *(_asset_paths(asset) for asset in case.inputs.values())
            )
            input_hashes = set().union(
                *(_asset_hashes(asset) for asset in case.inputs.values())
            )
            assert gold.path not in input_paths
            assert gold.sha256 not in input_hashes
            assert all(path.startswith(f"cases/{station}/") for path in input_paths)
