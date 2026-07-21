"""Executable development/conformance resources for editorial stations."""

from __future__ import annotations

import json
from pathlib import Path

from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.metrics import MetricRegistry
from palimpsest.factory.evaluation.station_metrics.editorial import (
    register_editorial_metrics,
)
from palimpsest.factory.evaluation.suite import load_suite, validate_candidate_suite


ROOT = Path(__file__).parents[1] / "palimpsest" / "factory"
EVALUATION = ROOT / "evaluation"


def _gold(relative: str) -> dict[str, object]:
    value = json.loads((EVALUATION / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _reference_outputs() -> tuple[dict[str, object], dict[str, object]]:
    faithful = {
        "doc_id": "editorial_livy_dev",
        "identification": {
            "work": "Titus Livius, Ab urbe condita",
            "tradition": "Roman historiography",
            "period_script": "No manuscript dating asserted",
            "witness_status": "Development fixture",
            "summary": "Two source-backed passages from Livy 1.58-59.",
        },
        "reference_points": [
            {
                "section": "Lucretia",
                "anchor": "Tace, Lucretia; ferrum in manu est.",
                "work": "Titus Livius, Ab urbe condita",
                "chapter": "1.58",
                "received_text": "Tace, Lucretia; Sex. Tarquinius sum; ferrum in manu est; moriere, si emiseris vocem.",
                "relationship": "quotes",
                "confidence": "high",
                "verified": "web: The Latin Library, Livy I.58",
            },
            {
                "section": "Lucretia",
                "anchor": "Per hunc castissimum ante regiam iniuriam sanguinem iuro.",
                "work": "Titus Livius, Ab urbe condita",
                "chapter": "1.59",
                "received_text": "Per hunc castissimum ante regiam iniuriam sanguinem iuro.",
                "relationship": "quotes",
                "confidence": "high",
                "verified": "web: The Latin Library, Livy I.59",
            },
        ],
        "editorial_notes": [],
    }
    broken = {
        **faithful,
        "reference_points": [
            {
                "section": "Lucretia",
                "anchor": "Tace, Lucretia; ferrum in manu est.",
                "work": "Marcus Tullius Cicero, De re publica",
                "chapter": "2.25",
                "received_text": "Lucretia founded the Roman republic.",
                "relationship": "shares doctrine",
                "confidence": "high",
                "verified": "memory",
            },
            {
                "section": "Lucretia",
                "anchor": "Per hunc castissimum ante regiam iniuriam sanguinem iuro.",
                "work": "Wikipedia, Lucretia",
                "chapter": "Narrative",
                "received_text": "Brutus then swore an oath.",
                "relationship": "shares doctrine",
                "confidence": "high",
                "verified": "web: Wikipedia, Lucretia",
            },
        ],
    }
    return faithful, broken


def _emend_outputs() -> tuple[dict[str, object], dict[str, object]]:
    faithful = {
        "doc_id": "editorial_emend_dev",
        "sections": [
            {
                "heading": "Lucretia A",
                "reading": "cultrum ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Lucretia B",
                "reading": "cultrum ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Oath",
                "reading": "Per hunc castissimum ante regiam iniuriam sanguinem iuro.\nNomen 〔?〕 manet.",
            },
        ],
        "apparatus": [
            {
                "section": "Lucretia A",
                "original": "cultrun",
                "emended": "cultrum",
                "reason": "Final graph conflicts with the received wording.",
                "evidence": "parallel: Livius·1.59",
            },
            {
                "section": "Lucretia B",
                "original": "cultrun",
                "emended": "cultrum",
                "reason": "The same systematic final-graph error recurs.",
                "evidence": "parallel: Livius·1.59",
            },
            {
                "section": "Oath",
                "original": "castisimum",
                "emended": "castissimum",
                "reason": "The received wording supplies the omitted letter.",
                "evidence": "parallel: Livius·1.59",
            },
        ],
    }
    broken = {
        "doc_id": "editorial_emend_dev",
        "sections": [
            {
                "heading": "Lucretia A",
                "reading": "cultrum ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Lucretia B",
                "reading": "cultrun ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Oath",
                "reading": "Per hunc castisimum ante regiam iniuriam sanguinem iuro.\nNomen Lucretia manet.",
            },
        ],
        "apparatus": [
            {
                "section": "Lucretia A",
                "original": "cultrun",
                "emended": "cultrum",
                "reason": "Only one occurrence was noticed.",
                "evidence": "parallel: Livius·1.59",
            }
        ],
        "diplomatic_sections": [
            {
                "heading": "Lucretia A",
                "original": "cultrum ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Lucretia B",
                "original": "cultrun ex volnere Lucretiae extractum.",
            },
            {
                "heading": "Oath",
                "original": "Per hunc castisimum ante regiam iniuriam sanguinem iuro.\nNomen Lucretia manet.",
            },
        ],
    }
    return faithful, broken


def test_editorial_metrics_reward_supported_evidence_and_reject_broken_outputs() -> (
    None
):
    registry = MetricRegistry()
    register_editorial_metrics(registry)
    names = {metric.name for metric in registry.all()}
    reference_names = {name for name in names if name.startswith("reference_")}
    emend_names = {name for name in names if name.startswith("emend_")}

    reference_gold = _gold("gold/reference/livy_claim_sources_v1.json")
    faithful_reference, broken_reference = _reference_outputs()
    validate_payload(
        "reference", faithful_reference, expected_doc_id="editorial_livy_dev"
    )
    for name in reference_names:
        assert registry.observe(name, faithful_reference, reference_gold) == 1.0
        broken_score = registry.observe(name, broken_reference, reference_gold)
        assert broken_score is not None and broken_score < 1.0

    emend_gold = _gold("gold/emend/livy_corrections_v1.json")
    faithful_emend, broken_emend = _emend_outputs()
    validate_payload(
        "emendations", faithful_emend, expected_doc_id="editorial_emend_dev"
    )
    for name in emend_names:
        assert registry.observe(name, faithful_emend, emend_gold) == 1.0
        broken_score = registry.observe(name, broken_emend, emend_gold)
        assert broken_score is not None and broken_score < 1.0

    assert (
        registry.observe("emend_correction_precision", broken_emend, emend_gold) < 1.0
    )
    assert registry.observe("emend_apparatus_coverage", broken_emend, emend_gold) == 0.0
    assert (
        registry.observe("emend_diplomatic_unchanged", broken_emend, emend_gold) == 0.0
    )
    assert (
        registry.observe("emend_uncertainty_explicit", broken_emend, emend_gold) == 0.0
    )


def test_editorial_resources_load_strictly_with_real_sockets_and_scorer_only_gold() -> (
    None
):
    registry = MetricRegistry()
    register_editorial_metrics(registry)
    expected_identities = {
        "reference": ("gpt-5.6-sol", "reference/generic/identify"),
        "emend": ("gpt-5.6-luna", "emend/generic/agent"),
    }

    for station in ("reference", "emend"):
        suite = load_suite(
            EVALUATION / "suites" / station / "development-v1.yaml",
            metric_resolver=registry,
            probe_resolver={},
            judge_resolver={},
        )
        candidate = load_candidate(
            ROOT / "candidates" / station / "current-development-v1.yaml"
        )
        validate_candidate_suite(candidate, suite)

        assert suite.station == candidate.station == station
        assert suite.promotion.minimum_completed_cases == len(suite.cases) == 1
        assert suite.qualification_eligible is False
        assert suite.can_auto_qualify is False
        assert candidate.variant == "default"
        assert candidate.model_identity == "fixed"
        assert candidate.prompt_hash is not None
        assert (candidate.model, candidate.prompt_name) == expected_identities[station]
        assert "not fully adjudicated qualification evidence" in suite.mission

        case = suite.cases[0]
        input_paths = {
            asset.path
            for value in case.inputs.values()
            for asset in (value.values() if hasattr(value, "values") else (value,))
        }
        reference_paths = {asset.path for asset in case.references.values()}
        assert input_paths.isdisjoint(reference_paths)
        assert all(path and path.startswith("gold/") for path in reference_paths)
        assert all(path and not path.startswith("gold/") for path in input_paths)
    for kind, relative in (
        ("manuscript", "cases/reference/assets/livy_excerpt_manuscript.json"),
        ("manuscript", "cases/emend/assets/livy_corrupt_manuscript.json"),
        ("reference", "cases/emend/assets/livy_reference_dossier.json"),
        ("page_assembled", "cases/emend/assets/p001_assembled.json"),
    ):
        payload = _gold(relative)
        validate_payload(kind, payload, expected_doc_id=payload.get("doc_id"))

    jpeg = (EVALUATION / "cases/emend/assets/p001_clean.jpg").read_bytes()
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")

    reference_candidate = load_candidate(
        ROOT / "candidates" / "reference" / "current-development-v1.yaml"
    )
    assert reference_candidate.consumes == ("manuscript",)
    assert reference_candidate.produces == "reference"

    emend_candidate = load_candidate(
        ROOT / "candidates" / "emend" / "current-development-v1.yaml"
    )
    assert emend_candidate.consumes == (
        "manuscript",
        "reference",
        "page_assembled",
        "page_image_clean",
    )
    assert emend_candidate.produces == "emendations"
