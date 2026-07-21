"""Deterministic conformance metrics for language-level stations.

These metrics are deliberately structural and reference-backed.  They do not
claim to replace expert judgments of translation adequacy or manuscript genre.
The scorer-only gold records name the passages, terms, entities, scripts, and
source-page relationships that a human adjudicator established for each case.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from ..metrics import Metric, MetricDirection, MetricRegistry

_SPACE = re.compile(r"\s+")


def _normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", value).casefold()).strip()


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _record_text(record: Mapping[str, object]) -> str | None:
    translation = record.get("translation")
    if isinstance(translation, str):
        return translation
    text = record.get("text")
    return text if isinstance(text, str) else None


def _contains_any(haystack: str, acceptable: object) -> bool:
    choices = (_normalized(item) for item in _sequence(acceptable))
    return any(choice and choice in haystack for choice in choices)


def _ratio(passed: int, total: int) -> float | None:
    return passed / total if total else None


def _translation_passage_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    text = _record_text(output)
    passages = _mapping_sequence(gold.get("passages"))
    if text is None or not passages:
        return None
    normalized = _normalized(text)
    covered = sum(
        _contains_any(normalized, passage.get("acceptable")) for passage in passages
    )
    return covered / len(passages)


def _translation_omission_rate(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    coverage = _translation_passage_coverage(output, gold)
    return None if coverage is None else 1.0 - coverage


def _translation_uncertainty_retention(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    text = _record_text(output)
    uncertainties = _mapping_sequence(gold.get("uncertainties"))
    if text is None or not uncertainties:
        return None
    flags = output.get("flags")
    searchable = _normalized(text) + " " + _normalized_json_text(flags)
    retained = sum(
        _contains_any(searchable, uncertainty.get("acceptable_markers"))
        for uncertainty in uncertainties
    )
    return retained / len(uncertainties)


def _translation_terminology_consistency(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    text = _record_text(output)
    terms = _mapping_sequence(gold.get("terms"))
    if text is None or not terms:
        return None
    normalized = _normalized(text)
    term_scores = []
    for term in terms:
        minimum = term.get("minimum_occurrences")
        if type(minimum) is not int or minimum <= 0:
            continue
        targets = [
            target
            for item in _sequence(term.get("acceptable_targets"))
            if (target := _normalized(item))
        ]
        if not targets:
            continue
        observed = sum(
            len(re.findall(rf"(?<!\w){re.escape(target)}(?!\w)", normalized))
            for target in targets
        )
        term_scores.append(min(observed / minimum, 1.0))
    return sum(term_scores) / len(term_scores) if term_scores else None


def _normalized_json_text(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            filter(None, (_normalized_json_text(item) for item in value.values()))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return " ".join(filter(None, (_normalized_json_text(item) for item in value)))
    return _normalized(value)


def _survey_structural_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = _mapping_sequence(gold.get("outline"))
    actual = _mapping_sequence(output.get("outline"))
    if not expected:
        return None
    covered = 0
    for item in expected:
        start_page = item.get("start_page")
        keywords = item.get("acceptable_descriptions")
        covered += any(
            section.get("start_page") == start_page
            and _contains_any(_normalized(section.get("description")), keywords)
            for section in actual
        )
    return covered / len(expected)


def _survey_terminology_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = _mapping_sequence(gold.get("terms"))
    glossary = _mapping_sequence(output.get("glossary"))
    if not expected:
        return None
    covered = 0
    for item in expected:
        source = _normalized(item.get("source"))
        targets = item.get("acceptable_targets")
        covered += any(
            _normalized(entry.get("term")) == source
            and _contains_any(_normalized(entry.get("translation")), targets)
            for entry in glossary
        )
    return covered / len(expected)


def _survey_entity_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = _mapping_sequence(gold.get("entities"))
    entities = _mapping_sequence(output.get("entities"))
    if not expected:
        return None
    covered = 0
    for item in expected:
        name = _normalized(item.get("name"))
        translations = item.get("acceptable_translations")
        covered += any(
            _normalized(entity.get("name")) == name
            and _contains_any(_normalized(entity.get("translation")), translations)
            for entity in entities
        )
    return covered / len(expected)


def _survey_language_script_identification(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = gold.get("identification")
    if not isinstance(expected, Mapping):
        return None
    searchable = (
        _normalized_json_text(output.get("identification"))
        + " "
        + _normalized_json_text(output.get("style_notes"))
    )
    checks = [
        _contains_any(searchable, expected.get(field))
        for field in ("languages", "scripts")
        if _sequence(expected.get(field))
    ]
    return _ratio(sum(checks), len(checks))


def _survey_downstream_brief_utility(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    """Score fixed downstream products, not the prose polish of the brief.

    Conformance fixtures record the translation produced with the brief and the
    page IDs retained by a reconstruction probe.  Gold names only the observable
    terminology and page-retention requirements for those fixed products.
    """

    probe = output.get("downstream_probe")
    expected = gold.get("downstream_expectations")
    if not isinstance(probe, Mapping) or not isinstance(expected, Mapping):
        return None
    checks: list[bool] = []
    translation = _normalized(probe.get("translation"))
    for term in _sequence(expected.get("translation_terms")):
        normalized = _normalized(term)
        if normalized:
            checks.append(normalized in translation)
    observed_pages = {
        page_id
        for item in _sequence(probe.get("reconstruction_pages"))
        if isinstance(item, str)
        for page_id in (item,)
    }
    for item in _sequence(expected.get("reconstruction_pages")):
        if isinstance(item, str):
            checks.append(item in observed_pages)
    return _ratio(sum(checks), len(checks))


def _section_signature(section: Mapping[str, object]) -> tuple[str, str, str] | None:
    pages = section.get("pages")
    if not isinstance(pages, Mapping):
        return None
    heading = section.get("heading")
    start = pages.get("from")
    end = pages.get("to")
    if not all(isinstance(item, str) and item for item in (heading, start, end)):
        return None
    return (_normalized(heading), start, end)


def _reconstruction_section_order(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = [
        signature
        for section in _mapping_sequence(gold.get("sections"))
        if (signature := _section_signature(section)) is not None
    ]
    if not expected:
        return None
    actual = [
        signature
        for section in _mapping_sequence(output.get("sections"))
        if (signature := _section_signature(section)) is not None
    ]
    matching_positions = sum(
        index < len(actual) and actual[index] == signature
        for index, signature in enumerate(expected)
    )
    return matching_positions / len(expected)


def _span(order: Sequence[object], start: object, end: object) -> tuple[str, ...]:
    if not isinstance(start, str) or not isinstance(end, str):
        return ()
    pages = tuple(item for item in order if isinstance(item, str))
    try:
        first = pages.index(start)
        last = pages.index(end)
    except ValueError:
        return ()
    return pages[first : last + 1] if first <= last else ()


def _reconstruction_page_source_linkage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    order = tuple(
        item for item in _sequence(gold.get("page_order")) if isinstance(item, str)
    )
    if not order:
        return None
    linked: list[str] = []
    for section in _mapping_sequence(output.get("sections")):
        pages = section.get("pages")
        if isinstance(pages, Mapping):
            linked.extend(_span(order, pages.get("from"), pages.get("to")))
    correctly_linked = sum(linked.count(page_id) == 1 for page_id in order)
    return correctly_linked / len(order)


def _reconstruction_no_invented_sections(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    sections = _mapping_sequence(output.get("sections"))
    if not sections:
        return None
    allowed = {
        signature
        for section in _mapping_sequence(gold.get("sections"))
        if (signature := _section_signature(section)) is not None
    }
    if not allowed:
        return None
    supported = sum(_section_signature(section) in allowed for section in sections)
    return supported / len(sections)


def _reconstruction_traceability(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    expected = {
        signature: section
        for section in _mapping_sequence(gold.get("sections"))
        if (signature := _section_signature(section)) is not None
    }
    if not expected:
        return None
    checks: list[bool] = []
    for section in _mapping_sequence(output.get("sections")):
        signature = _section_signature(section)
        reference = expected.get(signature)
        if reference is None:
            checks.extend((False, False))
            continue
        for side in ("original", "translation"):
            checks.append(
                _normalized(section.get(side)) == _normalized(reference.get(side))
            )
    return _ratio(sum(checks), len(checks))


def register_language_metrics(registry: MetricRegistry) -> None:
    """Register deterministic metrics for translate, survey, and reconstruct."""

    for metric in (
        Metric(
            "translation_passage_coverage",
            MetricDirection.MAXIMIZE,
            _translation_passage_coverage,
        ),
        Metric(
            "translation_omission_rate",
            MetricDirection.MINIMIZE,
            _translation_omission_rate,
        ),
        Metric(
            "translation_uncertainty_retention",
            MetricDirection.MAXIMIZE,
            _translation_uncertainty_retention,
        ),
        Metric(
            "translation_terminology_consistency",
            MetricDirection.MAXIMIZE,
            _translation_terminology_consistency,
        ),
        Metric(
            "survey_structural_coverage",
            MetricDirection.MAXIMIZE,
            _survey_structural_coverage,
        ),
        Metric(
            "survey_terminology_coverage",
            MetricDirection.MAXIMIZE,
            _survey_terminology_coverage,
        ),
        Metric(
            "survey_entity_coverage", MetricDirection.MAXIMIZE, _survey_entity_coverage
        ),
        Metric(
            "survey_language_script_identification",
            MetricDirection.MAXIMIZE,
            _survey_language_script_identification,
        ),
        Metric(
            "survey_downstream_brief_utility",
            MetricDirection.MAXIMIZE,
            _survey_downstream_brief_utility,
        ),
        Metric(
            "reconstruction_section_order",
            MetricDirection.MAXIMIZE,
            _reconstruction_section_order,
        ),
        Metric(
            "reconstruction_page_source_linkage",
            MetricDirection.MAXIMIZE,
            _reconstruction_page_source_linkage,
        ),
        Metric(
            "reconstruction_no_invented_sections",
            MetricDirection.MAXIMIZE,
            _reconstruction_no_invented_sections,
        ),
        Metric(
            "reconstruction_traceability",
            MetricDirection.MAXIMIZE,
            _reconstruction_traceability,
        ),
    ):
        registry.register(metric)
