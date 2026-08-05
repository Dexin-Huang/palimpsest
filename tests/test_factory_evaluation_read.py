"""Read-station metric tests using synthetic Latin strings only.

These fixtures are not manuscript benchmark resources or adjudicated gold.  No
Latin source in the repository currently records both a redistributable license
identifier and a documented gold-transcription adjudication method.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palimpsest.factory.han_variants import (
    HAN_VARIANT_TABLE_SHA256,
    HAN_VARIANT_TABLE_VERSION,
    normalize_han_variants_v1,
)
from palimpsest.factory.evaluation.metrics import MetricDirection, MetricRegistry
from palimpsest.factory.evaluation.station_metrics.read import (
    character_edits,
    character_error_structure,
    contamination_hits,
    contamination_rate,
    empty_output_rate,
    invented_character_rate,
    han_variant_v1_character_error_rate,
    han_variant_v1_partial_gold_character_error_rate,
    normalize_diplomatic,
    normalized_character_error_rate,
    partial_gold_character_error_rate,
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


def test_han_variant_v1_normalization_is_symmetric_and_conservative() -> None:
    assert HAN_VARIANT_TABLE_VERSION == 1
    assert len(HAN_VARIANT_TABLE_SHA256) == 64
    assert normalize_han_variants_v1("佛陁眞丗衆經目録") == "佛陀真世眾經目錄"
    assert normalize_han_variants_v1("日曰已巳真靜異護") == "日曰已巳真靜異護"

    reference = "佛陁眞丗衆經目録"
    alternate_forms = "佛陀真世眾經目錄"
    wrong_character = "佛陀真世眾經目錯"
    assert han_variant_v1_character_error_rate(alternate_forms, reference) == 0.0
    assert (
        han_variant_v1_partial_gold_character_error_rate(
            "題記" + alternate_forms, reference
        )
        == 0.0
    )
    assert han_variant_v1_character_error_rate(wrong_character, reference) > 0.0


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


def test_partial_gold_alignment_ignores_additions_but_requires_gold() -> None:
    reference = "abc"

    assert (
        partial_gold_character_error_rate("left margin\nabc\nrunning title", reference)
        == 0.0
    )
    assert partial_gold_character_error_rate("aXc", reference) == pytest.approx(1 / 3)
    assert partial_gold_character_error_rate("ac", reference) == pytest.approx(1 / 3)
    assert partial_gold_character_error_rate("", reference) == 1.0
    assert partial_gold_character_error_rate("unscored text", "") == 0.0


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
        "partial_gold_character_error_rate",
        "han_variant_v1_character_error_rate",
        "han_variant_v1_partial_gold_character_error_rate",
        "recognized_text_v1_character_error_rate",
        "recognized_text_v1_partial_gold_character_error_rate",
        "page_completeness",
        "region_completeness",
        "repetition_rate",
    }
    assert registry.get("character_error_rate").direction is MetricDirection.MINIMIZE
    assert (
        registry.get("partial_gold_character_error_rate").direction
        is MetricDirection.MINIMIZE
    )
    assert (
        registry.get("han_variant_v1_partial_gold_character_error_rate").direction
        is MetricDirection.MINIMIZE
    )
    assert (
        registry.get("recognized_text_v1_character_error_rate").direction
        is MetricDirection.MINIMIZE
    )
    assert (
        registry.get("recognized_text_v1_partial_gold_character_error_rate").direction
        is MetricDirection.MINIMIZE
    )
    assert registry.get("region_completeness").direction is MetricDirection.MAXIMIZE
    assert registry.observe("blind_image_pairwise", output, scorer_gold) is None
    assert registry.observe("character_error_rate", output, scorer_gold) == 0.0
    assert (
        registry.observe("partial_gold_character_error_rate", output, scorer_gold)
        == 0.0
    )
    assert (
        registry.observe(
            "han_variant_v1_partial_gold_character_error_rate",
            output,
            scorer_gold,
        )
        == 0.0
    )
    assert registry.observe("region_completeness", output, scorer_gold) == 1.0
    assert registry.observe("character_error_rate", output, {}) is None
    assert (
        registry.observe(
            "recognized_text_v1_character_error_rate",
            output,
            scorer_gold,
        )
        is None
    )

    assert not (candidate_workspace / "gold").exists()
    assert not any(
        path.name == "synthetic-reference.txt"
        for path in candidate_workspace.rglob("*")
    )


def test_character_error_structure_totals_match_scored_edits() -> None:
    reference = "line one\nline two\nline three"
    candidate = "line one\nline twX\nextra tail"

    structure = character_error_structure(candidate, reference)
    edits = character_edits(candidate, reference)

    totals = structure["totals"]
    assert totals["substitutions"] == edits.substitutions
    assert totals["deletions"] == edits.deletions
    assert totals["insertions"] == edits.insertions
    assert totals["reference_characters"] == edits.reference_characters
    assert totals["candidate_characters"] == edits.candidate_characters
    assert totals["error_rate"] == pytest.approx(
        edits.errors / max(edits.reference_characters, 1)
    )
    assert character_error_structure(candidate, reference) == structure


def test_character_error_structure_localizes_late_page_damage() -> None:
    gold_lines = ["alpha", "bravo", "carol", "delta", "eagle", "flint"]
    reference = "\n".join(gold_lines)
    candidate = "\n".join(gold_lines[:4] + ["eXgle", "fXint"])

    structure = character_error_structure(candidate, reference)

    bands = structure["line_bands"]
    assert bands["first_third"]["errors"] == 0
    assert bands["middle_third"]["errors"] == 0
    assert bands["last_third"]["errors"] == 2
    assert bands["last_third"]["error_rate"] == pytest.approx(0.2)
    lines = structure["lines"]
    assert lines["gold_lines"] == 6
    assert lines["matched_lines"] == 6
    assert lines["missing_lines"] == 0
    assert lines["extra_lines"] == 0
    assert lines["displaced_lines"] == 0


def test_character_error_structure_flags_displacement_and_confusions() -> None:
    reference = "first column\nsecond column\nthird column"
    candidate = "second column\nthird column\nfirst column"

    displaced = character_error_structure(candidate, reference)

    assert displaced["lines"]["matched_lines"] == 2
    assert displaced["lines"]["missing_lines"] == 1
    assert displaced["lines"]["extra_lines"] == 1
    assert displaced["lines"]["displaced_lines"] == 1

    confused = character_error_structure("aXa\naXa", "aba\naba")
    assert confused["confusion_pairs"][0] == {
        "gold": "b",
        "candidate": "X",
        "count": 2,
    }


def test_scope_aware_scoring_scores_primary_layer_only() -> None:
    registry = MetricRegistry()
    register_read_metrics(registry)
    layered_output = {
        "text": "main one\nmain two\nnote text",
        "layers": [
            {"kind": "primary", "text": "main one\nmain two"},
            {"kind": "commentary", "text": "note text"},
        ],
    }
    primary_gold = {"text": "main one\nmain two", "gold_scope": "primary_scope"}
    full_gold = {"text": "main one\nmain two\nnote text"}

    assert registry.observe("character_error_rate", layered_output, primary_gold) == 0.0
    assert (
        registry.observe("invented_character_rate", layered_output, primary_gold) == 0.0
    )
    assert registry.observe("page_completeness", layered_output, primary_gold) == 1.0
    assert registry.observe("character_error_rate", layered_output, full_gold) == 0.0

    flat_output = {"text": "main one\nmain two\nnote text"}
    flat_invented = registry.observe(
        "invented_character_rate", flat_output, primary_gold
    )
    assert flat_invented is not None and flat_invented > 0.0
    assert (
        registry.observe("partial_gold_character_error_rate", flat_output, primary_gold)
        == 0.0
    )

    assert registry.observe("empty_output_rate", layered_output, primary_gold) == 0.0

    broken_layers = {"text": "x", "layers": [{"kind": "primary"}]}
    assert registry.observe("character_error_rate", broken_layers, primary_gold) is None
