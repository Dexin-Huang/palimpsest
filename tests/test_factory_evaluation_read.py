"""Read-station metric tests using synthetic Latin strings only.

These fixtures are not manuscript benchmark resources or adjudicated gold.  No
Latin source in the repository currently records both a redistributable license
identifier and a documented gold-transcription adjudication method.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palimpsest.factory.evaluation.metrics import MetricDirection, MetricRegistry
from palimpsest.factory.evaluation.station_metrics.read import (
    character_edits,
    contamination_hits,
    contamination_rate,
    empty_output_rate,
    invented_character_rate,
    normalize_diplomatic,
    normalized_character_error_rate,
    page_completeness,
    region_completeness,
    register_read_metrics,
    repetition_rate,
)


def _build_synthetic_latin_case(
    root: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Build scorer-only synthetic data without representing it as real gold."""

    candidate_workspace = root / "candidate-workspace"
    input_directory = candidate_workspace / "page_image_clean"
    input_directory.mkdir(parents=True)
    (input_directory / "synthetic-page.jpg").write_bytes(b"synthetic image input")

    scorer_directory = root / "scorer-only"
    scorer_directory.mkdir()
    reference = "In prīncipio erat Verbum.\nſigillum & ꝑ"
    (scorer_directory / "synthetic-reference.txt").write_text(
        reference, encoding="utf-8"
    )
    output = {
        "text": reference,
        "regions": [
            {"region_id": "main", "text": "In prīncipio erat Verbum."},
            {"region_id": "seal", "text": "ſigillum & ꝑ"},
        ],
    }
    scorer_gold = {
        "text": reference,
        "regions": [
            {"region_id": "main", "text": "In prīncipio erat Verbum."},
            {"region_id": "seal", "text": "ſigillum & ꝑ"},
        ],
    }
    return candidate_workspace, output, scorer_gold


def test_normalization_only_canonicalizes_representation() -> None:
    decomposed = "A\u0304\r\nſ æ & ꝑ ·\t "

    assert normalize_diplomatic(decomposed) == "Ā\nſ æ & ꝑ ·\t "
    assert normalize_diplomatic("ſ") != normalize_diplomatic("s")
    assert normalize_diplomatic("A") != normalize_diplomatic("a")
    assert normalize_diplomatic("ꝑ") != normalize_diplomatic("per")
    assert normalize_diplomatic("a ") != normalize_diplomatic("a")


def test_deliberately_worse_diplomatic_transcription_loses() -> None:
    reference = "In prīncipio erat Verbum.\nſigillum & ꝑ"
    faithful = "In pri\u0304ncipio erat Verbum.\r\nſigillum & ꝑ"
    silently_normalized = "In principio erat verbum\nsigillum et per"

    assert normalized_character_error_rate(faithful, reference) == 0.0
    assert normalized_character_error_rate(silently_normalized, reference) > 0.0
    assert normalized_character_error_rate(
        silently_normalized, reference
    ) > normalized_character_error_rate(faithful, reference)


def test_edit_alignment_exposes_inserted_content() -> None:
    edits = character_edits("abc XYZ", "abc")

    assert edits.substitutions == 0
    assert edits.deletions == 0
    assert edits.insertions == 4
    assert edits.errors == 4
    assert invented_character_rate("abc XYZ", "abc") == pytest.approx(4 / 7)
    assert invented_character_rate("abc", "abc") == 0.0
    assert invented_character_rate("XYZ", "") == 1.0
    assert normalized_character_error_rate("XYZ", "") == 3.0


def test_contamination_repetition_and_empty_output_are_observable() -> None:
    contaminated = "Visible text\nBIBLIOTECA APOSTOLICA ©"
    repeated = "loop\nloop\nloop\nunique"

    assert contamination_hits(contaminated) == 2
    assert 0.0 < contamination_rate(contaminated) < 1.0
    assert contamination_rate("Visible manuscript text") == 0.0
    assert repetition_rate(repeated) == pytest.approx(0.75)
    assert repetition_rate("loop\nloop\nunique") == 0.0
    assert empty_output_rate(" \n\t") == 1.0
    assert empty_output_rate("x") == 0.0


def test_completeness_requires_explicit_gold_support() -> None:
    reference_regions = [
        {"region_id": "main", "text": "prima linea"},
        {"region_id": "margin", "text": "nota"},
        {"region_id": "figure", "text": ""},
    ]
    candidate_regions = [
        {"region_id": "main", "text": "prima linea"},
        {"region_id": "margin", "text": ""},
    ]

    assert region_completeness(candidate_regions, reference_regions) == 0.5
    assert region_completeness([], reference_regions) == 0.0
    assert region_completeness([], [{"region_id": "figure", "text": ""}]) is None
    assert page_completeness("aliquid", "textus") == 1.0
    assert page_completeness("", "textus") == 0.0
    assert page_completeness("", "") is None


def test_read_metric_registration_uses_scorer_only_gold(tmp_path: Path) -> None:
    candidate_workspace, output, scorer_gold = _build_synthetic_latin_case(tmp_path)
    registry = MetricRegistry()
    register_read_metrics(registry)

    assert {metric.name for metric in registry.all()} == {
        "blind_image_pairwise",
        "character_error_rate",
        "contamination_rate",
        "empty_output_rate",
        "invented_character_rate",
        "page_completeness",
        "region_completeness",
        "repetition_rate",
    }
    assert registry.get("character_error_rate").direction is MetricDirection.MINIMIZE
    assert registry.get("region_completeness").direction is MetricDirection.MAXIMIZE
    assert registry.observe("blind_image_pairwise", output, scorer_gold) is None
    assert registry.observe("character_error_rate", output, scorer_gold) == 0.0
    assert registry.observe("region_completeness", output, scorer_gold) == 1.0
    assert registry.observe("character_error_rate", output, {}) is None

    assert not (candidate_workspace / "gold").exists()
    assert not any(
        path.name == "synthetic-reference.txt"
        for path in candidate_workspace.rglob("*")
    )
