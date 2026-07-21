"""Deterministic diplomatic-reading metrics.

Character metrics normalize both strings to Unicode NFC and normalize CRLF or
bare CR line endings to LF.  They deliberately do not case-fold, strip,
collapse whitespace, remove punctuation or diacritics, expand abbreviations,
or replace historic characters.  Those marks can all be diplomatically
meaningful.

``invented_character_rate`` is a conservative string observation, not a claim
about authorial intent: it reports characters inserted by a deterministic
minimum-edit alignment with scorer-only gold.  When several minimum-edit
alignments exist, the alignment with the fewest insertions is selected.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


def invented_character_rate(candidate: str, reference: str) -> float:
    """Return the fraction of candidate characters inserted relative to gold."""

    edits = character_edits(candidate, reference)
    if edits.candidate_characters == 0:
        return 0.0
    return edits.insertions / edits.candidate_characters


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


def _score_character_error_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _record_text(output), _record_text(gold)
    if candidate is None or reference is None:
        return None
    return normalized_character_error_rate(candidate, reference)


def _score_invented_character_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    candidate, reference = _record_text(output), _record_text(gold)
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
    candidate, reference = _record_text(output), _record_text(gold)
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
            "character_error_rate",
            MetricDirection.MINIMIZE,
            _score_character_error_rate,
        ),
        Metric(
            "region_completeness", MetricDirection.MAXIMIZE, _score_region_completeness
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
