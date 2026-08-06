"""Deterministic diplomatic-reading metrics.

Character metrics normalize both strings to Unicode NFC and normalize CRLF or
bare CR line endings to LF.  They deliberately do not case-fold, strip,
collapse whitespace, remove punctuation or diacritics, expand abbreviations,
or replace historic characters.  Those marks can all be diplomatically
meaningful.

``partial_gold_character_error_rate`` treats the reference as positive evidence:
it scores only reference characters that are missing or contradicted. Candidate
insertions are free because incomplete gold cannot prove that visible extra text
is invented.

``invented_character_rate`` remains a conservative diagnostic, not a claim about
authorial intent. It reports insertions from a deterministic minimum-edit
alignment with scorer-only gold.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from palimpsest.factory.han_variants import normalize_han_variants_v1
from palimpsest.factory.recognized_text import (
    normalize_recognized_text_v1,
    recognized_reference_text,
)
from ..metrics import Metric, MetricDirection, MetricRegistry

# Vocabulary that can only come from the digitization, never from the page.
CONTAMINATION_TERMS = (
    "biblioteca apostolica",
    "all rights",
    "reserved",
    "copyright",
    "amlad",
    "ntt data",
    "vaticana ©",
    "©",
)


@dataclass(frozen=True, slots=True)
class CharacterEdits:
    """Counts from one deterministic minimum-character-edit alignment."""

    reference_characters: int
    candidate_characters: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def normalize_diplomatic(text: str) -> str:
    """Apply only representation-level normalization used by character metrics."""

    if not isinstance(text, str):
        raise TypeError("diplomatic text must be a string")
    line_normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", line_normalized)


def character_edits(candidate: str, reference: str) -> CharacterEdits:
    """Return deterministic Levenshtein operation counts for normalized text.

    Dynamic programming uses two rows, so memory is linear in candidate length.
    Ties minimize insertions, then deletions, then substitutions.  Consequently,
    inserted content is reported only when required by the selected optimal
    alignment rather than inflated by an arbitrary backtrace.
    """

    candidate = normalize_diplomatic(candidate)
    reference = normalize_diplomatic(reference)

    # Cells are (distance, substitutions, deletions, insertions).
    previous = [(index, 0, 0, index) for index in range(len(candidate) + 1)]
    for reference_index, reference_character in enumerate(reference, start=1):
        current = [(reference_index, 0, reference_index, 0)]
        for candidate_index, candidate_character in enumerate(candidate, start=1):
            diagonal = previous[candidate_index - 1]
            if reference_character == candidate_character:
                current.append(diagonal)
                continue

            substitution = (
                diagonal[0] + 1,
                diagonal[1] + 1,
                diagonal[2],
                diagonal[3],
            )
            above = previous[candidate_index]
            deletion = (above[0] + 1, above[1], above[2] + 1, above[3])
            left = current[candidate_index - 1]
            insertion = (left[0] + 1, left[1], left[2], left[3] + 1)
            current.append(
                min(
                    (substitution, deletion, insertion),
                    key=lambda counts: (
                        counts[0],
                        counts[3],
                        counts[2],
                        counts[1],
                    ),
                )
            )
        previous = current

    _, substitutions, deletions, insertions = previous[-1]
    return CharacterEdits(
        reference_characters=len(reference),
        candidate_characters=len(candidate),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
    )


def normalized_character_error_rate(candidate: str, reference: str) -> float:
    """Return ``(substitutions + deletions + insertions) / reference chars``.

    An empty reference uses denominator one: two empty strings score zero, while
    non-empty candidate text remains an insertion error rather than disappearing
    behind an undefined or zero denominator.  Like conventional CER, the result
    can exceed one.
    """

    edits = character_edits(candidate, reference)
    return edits.errors / max(edits.reference_characters, 1)


def han_variant_v1_character_error_rate(candidate: str, reference: str) -> float:
    """Return CER after the same conservative Han-form mapping on both sides."""

    return normalized_character_error_rate(
        normalize_han_variants_v1(candidate),
        normalize_han_variants_v1(reference),
    )


def partial_gold_character_error_rate(candidate: str, reference: str) -> float:
    """Return minimum missing or contradicted reference characters per reference.

    The alignment preserves reference order. Skipping a candidate character is
    free, while deleting or substituting a reference character costs one. This
    makes incomplete gold usable as positive evidence without treating
    image-supported candidate text outside the annotation as an error.
    """

    candidate = normalize_diplomatic(candidate)
    reference = normalize_diplomatic(reference)
    if not reference:
        return 0.0

    previous = [0] * (len(candidate) + 1)
    for reference_index, reference_character in enumerate(reference, start=1):
        current = [reference_index]
        for candidate_index, candidate_character in enumerate(candidate, start=1):
            current.append(
                min(
                    current[candidate_index - 1],
                    previous[candidate_index] + 1,
                    previous[candidate_index - 1]
                    + (reference_character != candidate_character),
                )
            )
        previous = current
    return previous[-1] / len(reference)


def han_variant_v1_partial_gold_character_error_rate(
    candidate: str, reference: str
) -> float:
    """Return positive-reference error after symmetric Han-form normalization."""

    return partial_gold_character_error_rate(
        normalize_han_variants_v1(candidate),
        normalize_han_variants_v1(reference),
    )


def recognized_text_v1_character_error_rate(candidate: str, reference: str) -> float:
    """Return CER after symmetric recognized-text normalization."""

    return normalized_character_error_rate(
        normalize_recognized_text_v1(candidate),
        normalize_recognized_text_v1(reference),
    )


def recognized_text_v1_partial_gold_character_error_rate(
    candidate: str, reference: str
) -> float:
    """Return positive-reference CER after recognized-text normalization."""

    return partial_gold_character_error_rate(
        normalize_recognized_text_v1(candidate),
        normalize_recognized_text_v1(reference),
    )


def invented_character_rate(candidate: str, reference: str) -> float:
    """Return the fraction of candidate characters inserted relative to gold."""

    edits = character_edits(candidate, reference)
    if edits.candidate_characters == 0:
        return 0.0
    return edits.insertions / edits.candidate_characters


_LINE_BAND_NAMES = ("first_third", "middle_third", "last_third")
_DISPLACED_LINE_RATIO = 0.6


def character_error_structure(
    candidate: str, reference: str, *, max_confusions: int = 10
) -> dict[str, object]:
    """Aggregated gold-alignment diagnostics for ``R_train`` side information.

    The result reports counts, per-band rates, and single-character
    substitution pairs only. It never emits a multi-character gold span, so a
    reader cannot reconstruct the reference text from this structure.

    ``totals`` reuses :func:`character_edits`, so its counts match the scored
    ``character_error_rate`` exactly. The line-level blocks use a
    deterministic ``difflib.SequenceMatcher`` alignment and are diagnostics,
    not scores.
    """

    edits = character_edits(candidate, reference)
    reference_lines = [
        line for line in normalize_diplomatic(reference).split("\n") if line.strip()
    ]
    candidate_lines = [
        line for line in normalize_diplomatic(candidate).split("\n") if line.strip()
    ]

    pairs: list[tuple[int, str, str]] = []
    missing: list[tuple[int, str]] = []
    extra: list[str] = []
    line_matcher = difflib.SequenceMatcher(
        a=reference_lines, b=candidate_lines, autojunk=False
    )
    for tag, a_start, a_end, b_start, b_end in line_matcher.get_opcodes():
        if tag == "equal":
            for offset in range(a_end - a_start):
                pairs.append(
                    (
                        a_start + offset,
                        reference_lines[a_start + offset],
                        candidate_lines[b_start + offset],
                    )
                )
        elif tag == "replace":
            paired = min(a_end - a_start, b_end - b_start)
            for offset in range(paired):
                pairs.append(
                    (
                        a_start + offset,
                        reference_lines[a_start + offset],
                        candidate_lines[b_start + offset],
                    )
                )
            for index in range(a_start + paired, a_end):
                missing.append((index, reference_lines[index]))
            extra.extend(candidate_lines[b_start + paired : b_end])
        elif tag == "delete":
            for index in range(a_start, a_end):
                missing.append((index, reference_lines[index]))
        else:
            extra.extend(candidate_lines[b_start:b_end])

    def band_name(index: int) -> str:
        if not reference_lines:
            return _LINE_BAND_NAMES[0]
        return _LINE_BAND_NAMES[min(2, index * 3 // len(reference_lines))]

    band_counts = {
        name: {"reference_characters": 0, "errors": 0} for name in _LINE_BAND_NAMES
    }
    confusions: Counter[tuple[str, str]] = Counter()
    for index, gold_line, candidate_line in pairs:
        pair_edits = character_edits(candidate_line, gold_line)
        counts = band_counts[band_name(index)]
        counts["reference_characters"] += pair_edits.reference_characters
        counts["errors"] += pair_edits.errors
        pair_matcher = difflib.SequenceMatcher(
            a=gold_line, b=candidate_line, autojunk=False
        )
        for tag, a_start, a_end, b_start, b_end in pair_matcher.get_opcodes():
            if tag != "replace":
                continue
            for gold_char, candidate_char in zip(
                gold_line[a_start:a_end], candidate_line[b_start:b_end]
            ):
                confusions[(gold_char, candidate_char)] += 1
    for index, gold_line in missing:
        counts = band_counts[band_name(index)]
        counts["reference_characters"] += len(gold_line)
        counts["errors"] += len(gold_line)

    displaced = 0
    consumed: set[int] = set()
    for _, gold_line in missing:
        best_ratio = 0.0
        best_position: int | None = None
        for position, candidate_line in enumerate(extra):
            if position in consumed:
                continue
            ratio = difflib.SequenceMatcher(
                a=gold_line, b=candidate_line, autojunk=False
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_position = position
        if best_position is not None and best_ratio >= _DISPLACED_LINE_RATIO:
            displaced += 1
            consumed.add(best_position)

    return {
        "totals": {
            "reference_characters": edits.reference_characters,
            "candidate_characters": edits.candidate_characters,
            "substitutions": edits.substitutions,
            "deletions": edits.deletions,
            "insertions": edits.insertions,
            "error_rate": edits.errors / max(edits.reference_characters, 1),
        },
        "lines": {
            "gold_lines": len(reference_lines),
            "candidate_lines": len(candidate_lines),
            "matched_lines": len(pairs),
            "missing_lines": len(missing),
            "extra_lines": len(extra),
            "displaced_lines": displaced,
            "extra_line_characters": sum(len(line) for line in extra),
        },
        "line_bands": {
            name: {
                "reference_characters": counts["reference_characters"],
                "errors": counts["errors"],
                "error_rate": counts["errors"] / max(counts["reference_characters"], 1),
            }
            for name, counts in band_counts.items()
        },
        "confusion_pairs": [
            {"gold": gold_char, "candidate": candidate_char, "count": count}
            for (gold_char, candidate_char), count in sorted(
                confusions.items(), key=lambda item: (-item[1], item[0])
            )[:max_confusions]
        ],
    }


def contamination_hits(text: str) -> int:
    """Count occurrences of known digitization-only phrases."""

    normalized = normalize_diplomatic(text).lower()
    return sum(normalized.count(term) for term in CONTAMINATION_TERMS)


def contamination_rate(text: str) -> float:
    """Return the fraction of output characters covered by contamination terms.

    Overlapping terms (for example ``"vaticana ©"`` and ``"©"``) count their
    covered characters once.  Matching is case-insensitive; the output itself is
    otherwise left diplomatically intact.
    """

    normalized = normalize_diplomatic(text)
    if not normalized:
        return 0.0
    lower = normalized.lower()
    covered: set[int] = set()
    for term in CONTAMINATION_TERMS:
        start = 0
        while True:
            match = lower.find(term, start)
            if match < 0:
                break
            covered.update(range(match, match + len(term)))
            start = match + len(term)
    return len(covered) / len(normalized)


def repetition_rate(text: str) -> float:
    """Return the fraction of nonblank lines belonging to 3+-copy loops."""

    lines = [
        line.strip() for line in normalize_diplomatic(text).splitlines() if line.strip()
    ]
    if not lines:
        return 0.0
    counts = Counter(lines)
    repeated_lines = sum(count for count in counts.values() if count > 2)
    return repeated_lines / len(lines)


def empty_output_rate(text: str) -> float:
    """Return one for empty/whitespace-only output, otherwise zero."""

    return float(not normalize_diplomatic(text).strip())


def page_completeness(candidate: str, reference: str) -> float | None:
    """Observe whether a gold-supported, nonblank page has any candidate text."""

    if not normalize_diplomatic(reference).strip():
        return None
    return 1.0 - empty_output_rate(candidate)


def region_completeness(
    candidate_regions: Sequence[Mapping[str, object]],
    reference_regions: Sequence[Mapping[str, object]],
) -> float | None:
    """Return the fraction of gold text-bearing regions with nonblank output.

    Region identity comes from ``region_id``.  Gold regions without reference
    text do not establish visible textual content and are excluded.  No eligible
    gold regions yields ``None`` (unknown), never a fabricated perfect score.
    """

    candidate_by_id = _regions_by_id(candidate_regions, label="candidate")
    reference_by_id = _regions_by_id(reference_regions, label="reference")
    expected = {
        region_id: text for region_id, text in reference_by_id.items() if text.strip()
    }
    if not expected:
        return None
    recovered = sum(
        bool(candidate_by_id.get(region_id, "").strip()) for region_id in expected
    )
    return recovered / len(expected)


def _regions_by_id(
    regions: Sequence[Mapping[str, object]], *, label: str
) -> dict[str, str]:
    if isinstance(regions, (str, bytes)) or not isinstance(regions, Sequence):
        raise TypeError(f"{label} regions must be a sequence")
    by_id: dict[str, str] = {}
    for region in regions:
        if not isinstance(region, Mapping):
            raise TypeError(f"each {label} region must be a mapping")
        region_id = region.get("region_id")
        text = region.get("text")
        if not isinstance(region_id, str) or not region_id:
            raise ValueError(f"each {label} region requires a nonempty region_id")
        if region_id in by_id:
            raise ValueError(f"duplicate {label} region_id {region_id!r}")
        if not isinstance(text, str):
            raise TypeError(f"{label} region {region_id!r} text must be a string")
        by_id[region_id] = normalize_diplomatic(text)
    return by_id


def _record_text(record: Mapping[str, object]) -> str | None:
    text = record.get("text")
    if isinstance(text, str):
        return text
    transcription = record.get("transcription")
    if isinstance(transcription, str):
        return transcription
    if isinstance(transcription, Mapping):
        nested_text = transcription.get("text")
        if isinstance(nested_text, str):
            return nested_text
    return None


_PRIMARY_SCOPE = "primary_scope"


def _scoped_candidate_text(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> str | None:
    """Candidate text for gold-dependent metrics under the gold scope.

    Scope rides in the gold record as ``gold_scope``. When the gold covers
    only the primary layer and the output declares layers, only the primary
    layers are scored, so faithful commentary reading never counts as
    invented text. Flat outputs and full-scope gold score the flat text.
    Malformed layer declarations return ``None``, which the runner treats as
    an unobservable metric rather than a silent pass.
    """

    layers = output.get("layers")
    if layers is None:
        return _record_text(output)
    if (
        isinstance(layers, (str, bytes))
        or not isinstance(layers, Sequence)
        or not layers
    ):
        return None
    texts_by_kind: list[tuple[str, str]] = []
    for layer in layers:
        if not isinstance(layer, Mapping):
            return None
        kind = layer.get("kind")
        text = layer.get("text")
        if not isinstance(kind, str) or not kind.strip() or not isinstance(text, str):
            return None
        texts_by_kind.append((kind, text))
    if gold.get("gold_scope") == _PRIMARY_SCOPE:
        return "\n".join(text for kind, text in texts_by_kind if kind == "primary")
    flat = _record_text(output)
    if flat is not None:
        return flat
    return "\n".join(text for _, text in texts_by_kind)


def _recognized_candidate_text(output: Mapping[str, object]) -> str | None:
    """Return the full-page candidate view for recognized-text metrics."""

    if output.get("layers") is not None:
        return _scoped_candidate_text(output, {})

    candidate = _record_text(output)
    if candidate is None:
        return None
    if "commentary" not in output:
        return candidate
    commentary = output["commentary"]
    if not isinstance(commentary, str):
        return None
    if commentary.strip():
        return f"{candidate}\n{commentary}"
    return candidate


def _score_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return normalized_character_error_rate(candidate, reference)


def _score_partial_gold_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return partial_gold_character_error_rate(candidate, reference)


def _score_han_variant_v1_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return han_variant_v1_character_error_rate(candidate, reference)


def _score_han_variant_v1_partial_gold_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return han_variant_v1_partial_gold_character_error_rate(candidate, reference)


def _score_recognized_text_v1_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    reference = recognized_reference_text(gold)
    candidate = _recognized_candidate_text(output)
    if candidate is None or reference is None:
        return None
    return recognized_text_v1_character_error_rate(candidate, reference)


def _score_recognized_text_v1_partial_gold_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    reference = recognized_reference_text(gold)
    candidate = _recognized_candidate_text(output)
    if candidate is None or reference is None:
        return None
    return recognized_text_v1_partial_gold_character_error_rate(candidate, reference)


def _score_invented_character_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return invented_character_rate(candidate, reference)


def _score_region_completeness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate_regions, reference_regions = output.get("regions"), gold.get("regions")
    if not isinstance(candidate_regions, Sequence) or isinstance(
        candidate_regions, (str, bytes)
    ):
        return None
    if not isinstance(reference_regions, Sequence) or isinstance(
        reference_regions, (str, bytes)
    ):
        return None
    return region_completeness(candidate_regions, reference_regions)


def _score_page_completeness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _scoped_candidate_text(output, gold), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return page_completeness(candidate, reference)


def _score_contamination_rate(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    candidate = _record_text(output)
    return None if candidate is None else contamination_rate(candidate)


def _score_repetition_rate(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    candidate = _record_text(output)
    return None if candidate is None else repetition_rate(candidate)


def _score_empty_output_rate(
    output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    candidate = _record_text(output)
    return None if candidate is None else empty_output_rate(candidate)


def _judge_metric_only(
    _output: Mapping[str, object], _gold: Mapping[str, object]
) -> float | None:
    """Keep judge evidence out of deterministic scorer execution."""
    return None


def register_read_metrics(registry: MetricRegistry) -> None:
    """Register the trusted metric names owned by the logical ``read`` station."""

    for metric in (
        Metric(
            "partial_gold_character_error_rate",
            MetricDirection.MINIMIZE,
            _score_partial_gold_character_error_rate,
        ),
        Metric(
            "han_variant_v1_partial_gold_character_error_rate",
            MetricDirection.MINIMIZE,
            _score_han_variant_v1_partial_gold_character_error_rate,
        ),
        Metric(
            "recognized_text_v1_partial_gold_character_error_rate",
            MetricDirection.MINIMIZE,
            _score_recognized_text_v1_partial_gold_character_error_rate,
        ),
        Metric("page_completeness", MetricDirection.MAXIMIZE, _score_page_completeness),
        Metric(
            "invented_character_rate",
            MetricDirection.MINIMIZE,
            _score_invented_character_rate,
        ),
        Metric(
            "contamination_rate", MetricDirection.MINIMIZE, _score_contamination_rate
        ),
        Metric("repetition_rate", MetricDirection.MINIMIZE, _score_repetition_rate),
        Metric("empty_output_rate", MetricDirection.MINIMIZE, _score_empty_output_rate),
        Metric("blind_image_pairwise", MetricDirection.MAXIMIZE, _judge_metric_only),
    ):
        registry.register(metric)
