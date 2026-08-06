"""Trusted downstream-probe definitions.

A probe receives the paired outcomes and evaluation cases of one suite run and
returns a mapping whose ``status`` is one of ``passed`` | ``failed`` |
``unknown`` plus evidence. Probes are deterministic and offline: they validate
that a candidate's output is consumable by the next station in the line, so a
promotion cannot silently degrade downstream stations without a recorded,
blocking signal. They never invoke paid work.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _side_outputs(
    paired_cases: Sequence[Any],
) -> tuple[list[tuple[str, Mapping[str, object]]], list[str]]:
    """(case_id, output) pairs for succeeded sides plus any failed case ids."""
    outputs: list[tuple[str, Mapping[str, object]]] = []
    failed: list[str] = []
    for pair in paired_cases:
        for side in (pair.baseline, pair.challenger):
            if not side.succeeded or side.output_path is None:
                failed.append(side.candidate_id)
                continue
            try:
                payload = json.loads(Path(side.output_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"Probe cannot read output for {side.candidate_id!r}: {error}"
                ) from error
            if not isinstance(payload, Mapping):
                raise ValueError(
                    f"Probe output for {side.candidate_id!r} must be a JSON object"
                )
            outputs.append((side.candidate_id, payload))
    return outputs, failed


def read_to_align(
    paired_cases: Sequence[Any], evaluation_cases: Sequence[Any]
) -> Mapping[str, object]:
    """The align station must be able to consume each transcription output.

    A transcription is consumable when its text is non-empty and, for the
    segmented ``page_transcription`` shape, every region text appears in the
    composed text so the aligner can anchor characters to image regions.
    """

    outputs, failed = _side_outputs(paired_cases)
    problems: list[str] = []
    for candidate_id, payload in outputs:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{candidate_id}: empty transcription text")
            continue
        regions = payload.get("regions")
        if isinstance(regions, list):
            for index, region in enumerate(regions):
                region_text = region.get("text") if isinstance(region, Mapping) else None
                if region_text and region_text not in text:
                    problems.append(
                        f"{candidate_id}: region {index} text not in composed text"
                    )
    status = "unknown" if not outputs and not problems else (
        "failed" if problems else "passed"
    )
    return {
        "status": status,
        "evidence": {
            "checked_outputs": len(outputs),
            "failed_sides": len(failed),
            "problems": problems,
        },
    }


def survey_to_translate(
    paired_cases: Sequence[Any], evaluation_cases: Sequence[Any]
) -> Mapping[str, object]:
    """The translate station must be able to consume each survey brief.

    A brief is consumable when it carries the ``translation_brief`` contract
    fields (``document``, ``glossary``, ``outline``) with the documented
    structure, so translate never starts from a half-built jig.
    """

    outputs, failed = _side_outputs(paired_cases)
    problems: list[str] = []
    for candidate_id, payload in outputs:
        for field in ("document", "glossary", "outline"):
            value = payload.get(field)
            if isinstance(value, Mapping):
                valid = bool(value)
            elif isinstance(value, list):
                valid = bool(value)
            else:
                valid = value not in (None, "")
            if not valid:
                problems.append(f"{candidate_id}: brief field {field!r} is empty")
    status = "unknown" if not outputs and not problems else (
        "failed" if problems else "passed"
    )
    return {
        "status": status,
        "evidence": {
            "checked_outputs": len(outputs),
            "failed_sides": len(failed),
            "problems": problems,
        },
    }


def trusted_probes() -> dict[str, Any]:
    """Resolve the trusted probe IDs declared by suites."""
    return {
        "read-to-align/v1": read_to_align,
        "survey-to-translate/v1": survey_to_translate,
    }
