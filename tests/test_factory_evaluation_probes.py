from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from palimpsest.factory.evaluation.probes import (
    read_to_align,
    survey_to_translate,
    trusted_probes,
)


def _side(tmp_path: Path, candidate_id: str, succeeded: bool, output: object = None):
    output_path = None
    if succeeded and output is not None:
        output_path = tmp_path / f"{candidate_id}.json"
        output_path.write_text(json.dumps(output), encoding="utf-8")
    return SimpleNamespace(
        candidate_id=candidate_id,
        succeeded=succeeded,
        output_path=output_path,
    )


def _pair(baseline, challenger):
    return SimpleNamespace(baseline=baseline, challenger=challenger)


def test_read_to_align_passes_consumable_transcriptions(tmp_path: Path) -> None:
    outputs = [
        _pair(
            _side(tmp_path, "b1", True, {"text": "one two\nthree"}),
            _side(tmp_path, "c1", True, {"text": "one two\nthree"}),
        ),
        _pair(
            _side(
                tmp_path,
                "b2",
                True,
                {"text": "one two", "regions": [{"text": "one"}, {"text": "two"}]},
            ),
            _side(tmp_path, "c2", False),
        ),
    ]
    result = read_to_align(outputs, [])
    assert result["status"] == "passed"
    assert result["evidence"]["checked_outputs"] == 3


def test_read_to_align_fails_on_empty_text_and_unanchored_regions(
    tmp_path: Path,
) -> None:
    outputs = [
        _pair(
            _side(tmp_path, "b1", True, {"text": "   "}),
            _side(tmp_path, "c1", True, {"text": "ok"}),
        ),
        _pair(
            _side(
                tmp_path,
                "b2",
                True,
                {"text": "one two", "regions": [{"text": "missing"}]},
            ),
            _side(tmp_path, "c2", True, {"text": "ok"}),
        ),
    ]
    result = read_to_align(outputs, [])
    assert result["status"] == "failed"
    assert len(result["evidence"]["problems"]) == 2


def test_read_to_align_is_unknown_without_outputs(tmp_path: Path) -> None:
    outputs = [_pair(_side(tmp_path, "b", False), _side(tmp_path, "c", False))]
    result = read_to_align(outputs, [])
    assert result["status"] == "unknown"


def test_survey_to_translate_passes_complete_briefs(tmp_path: Path) -> None:
    brief = {"document": {"sections": 3}, "glossary": {"term": "gloss"}, "outline": ["a"]}
    outputs = [
        _pair(_side(tmp_path, "b", True, brief), _side(tmp_path, "c", True, brief))
    ]
    result = survey_to_translate(outputs, [])
    assert result["status"] == "passed"


def test_survey_to_translate_fails_on_empty_required_fields(tmp_path: Path) -> None:
    outputs = [
        _pair(
            _side(tmp_path, "b", True, {"document": {}, "glossary": [], "outline": ""}),
            _side(
                tmp_path,
                "c",
                True,
                {"document": {"sections": 1}, "glossary": [], "outline": ["a"]},
            ),
        )
    ]
    result = survey_to_translate(outputs, [])
    assert result["status"] == "failed"
    assert len(result["evidence"]["problems"]) == 4


def test_trusted_probes_resolves_declared_ids() -> None:
    probes = trusted_probes()
    assert set(probes) == {"read-to-align/v1", "survey-to-translate/v1"}
    for definition in probes.values():
        assert callable(definition)
