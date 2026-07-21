"""Deterministic evidence metrics for the reference and emend stations.

The scorers deliberately use exact, human-authored claim/source and correction
mappings.  They do not infer truth from a model's confidence or from lexical
similarity.  This makes the metrics conservative approximations: an unlisted
citation or wording receives no credit rather than being guessed correct.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from palimpsest.factory.apparatus import coverage_failures

from ..metrics import Metric, MetricDirection, MetricRegistry


def _records(value: object) -> list[Mapping[str, object]] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if not all(isinstance(item, Mapping) for item in value):
        return None
    return list(value)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _reference_gold(
    gold: Mapping[str, object],
) -> (
    tuple[dict[tuple[str, str], Mapping[str, object]], list[Mapping[str, object]]]
    | None
):
    claims = _records(gold.get("claims"))
    sources = _records(gold.get("sources"))
    if claims is None or sources is None:
        return None
    by_anchor: dict[tuple[str, str], Mapping[str, object]] = {}
    for claim in claims:
        section, anchor = _text(claim.get("section")), _text(claim.get("anchor"))
        claim_id = _text(claim.get("claim_id"))
        if section is None or anchor is None or claim_id is None:
            return None
        key = (section, anchor)
        if key in by_anchor:
            return None
        by_anchor[key] = claim
    return by_anchor, sources


def _reference_rows(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> (
    tuple[
        list[
            tuple[
                Mapping[str, object],
                Mapping[str, object] | None,
                Mapping[str, object] | None,
            ]
        ],
        dict[tuple[str, str], Mapping[str, object]],
        list[Mapping[str, object]],
    ]
    | None
):
    points = _records(output.get("reference_points"))
    parsed = _reference_gold(gold)
    if points is None or parsed is None:
        return None
    claims, sources = parsed
    rows = []
    for point in points:
        key = (_text(point.get("section")) or "", _text(point.get("anchor")) or "")
        claim = claims.get(key)
        work, chapter = _text(point.get("work")), _text(point.get("chapter"))
        source = next(
            (
                item
                for item in sources
                if _text(item.get("work")) == work
                and _text(item.get("chapter")) == chapter
            ),
            None,
        )
        rows.append((point, claim, source))
    return rows, claims, sources


def _claim_id(claim: Mapping[str, object] | None) -> str | None:
    return None if claim is None else _text(claim.get("claim_id"))


def _string_set(value: object) -> set[str] | None:
    records = value
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        return None
    if not all(isinstance(item, str) and item.strip() for item in records):
        return None
    return {item.strip() for item in records}


def _source_supports(
    source: Mapping[str, object] | None, claim: Mapping[str, object] | None
) -> bool:
    claim_id = _claim_id(claim)
    supported = (
        None if source is None else _string_set(source.get("supports_claim_ids"))
    )
    return claim_id is not None and supported is not None and claim_id in supported


def _score_reference_citation_precision(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _reference_rows(output, gold)
    if parsed is None:
        return None
    rows, _, _ = parsed
    if not rows:
        return 0.0
    resolvable = sum(
        claim is not None and source is not None for _, claim, source in rows
    )
    return resolvable / len(rows)


def _score_reference_source_support(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _reference_rows(output, gold)
    if parsed is None:
        return None
    rows, _, _ = parsed
    if not rows:
        return 0.0
    return sum(_source_supports(source, claim) for _, claim, source in rows) / len(rows)


def _score_reference_bibliographic_correctness(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _reference_rows(output, gold)
    if parsed is None:
        return None
    rows, _, _ = parsed
    if not rows:
        return 0.0
    correct = 0
    for point, claim, source in rows:
        expected = None if source is None else _text(source.get("verification_label"))
        if (
            claim is not None
            and expected is not None
            and _text(point.get("verified")) == expected
        ):
            correct += 1
    return correct / len(rows)


def _score_reference_primary_source_preference(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _reference_rows(output, gold)
    if parsed is None:
        return None
    rows, _, sources = parsed
    eligible = []
    for point, claim, source in rows:
        claim_id = _claim_id(claim)
        if claim_id is None:
            continue
        primary_available = any(
            item.get("primary") is True
            and claim_id in (_string_set(item.get("supports_claim_ids")) or set())
            for item in sources
        )
        if primary_available:
            eligible.append(source)
    if not eligible:
        return None
    return sum(
        source is not None and source.get("primary") is True for source in eligible
    ) / len(eligible)


def _score_reference_claim_source_entailment(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _reference_rows(output, gold)
    if parsed is None:
        return None
    rows, _, _ = parsed
    if not rows:
        return 0.0
    entailed = 0
    for point, claim, source in rows:
        accepted = (
            None
            if source is None
            else _string_set(source.get("entailed_received_texts"))
        )
        received = _text(point.get("received_text"))
        if (
            _source_supports(source, claim)
            and accepted is not None
            and received in accepted
        ):
            entailed += 1
    return entailed / len(rows)


def _emend_gold(
    gold: Mapping[str, object],
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]] | None:
    diplomatic = _records(gold.get("diplomatic_sections"))
    corrections = _records(gold.get("corrections"))
    if diplomatic is None or corrections is None:
        return None
    required_diplomatic = all(
        _text(section.get("heading")) is not None
        and isinstance(section.get("original"), str)
        for section in diplomatic
    )
    required_corrections = all(
        all(
            _text(correction.get(field)) is not None
            for field in ("correction_id", "section", "original", "emended")
        )
        and _string_set(correction.get("accepted_evidence")) is not None
        for correction in corrections
    )
    return (
        (diplomatic, corrections)
        if required_diplomatic and required_corrections
        else None
    )


def _emend_artifact(output: Mapping[str, object]) -> dict[str, object] | None:
    sections = _records(output.get("sections"))
    apparatus = _records(output.get("apparatus"))
    if sections is None or apparatus is None:
        return None
    if not all(
        _text(section.get("heading")) and isinstance(section.get("reading"), str)
        for section in sections
    ):
        return None
    return {"sections": sections, "apparatus": apparatus}


def _correction_key(
    record: Mapping[str, object],
) -> tuple[str | None, str | None, str | None]:
    return (
        _text(record.get("section")),
        _text(record.get("original")),
        _text(record.get("emended")),
    )


def _emend_matches(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> (
    tuple[
        dict[str, object],
        list[Mapping[str, object]],
        list[Mapping[str, object]],
        dict[tuple[str | None, str | None, str | None], Mapping[str, object]],
    ]
    | None
):
    parsed_gold = _emend_gold(gold)
    artifact = _emend_artifact(output)
    if parsed_gold is None or artifact is None:
        return None
    diplomatic, corrections = parsed_gold
    by_key = {_correction_key(correction): correction for correction in corrections}
    if len(by_key) != len(corrections):
        return None
    return artifact, diplomatic, corrections, by_key


def _coverage_failure_count(
    diplomatic: list[Mapping[str, object]], artifact: dict[str, object]
) -> int:
    failures = coverage_failures(
        [dict(section) for section in diplomatic],
        artifact,
    )
    return len(failures)


def _score_emend_correction_precision(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    if parsed is None:
        return None
    artifact, diplomatic, _, by_key = parsed
    apparatus = artifact["apparatus"]
    assert isinstance(apparatus, list)
    failures = _coverage_failure_count(diplomatic, artifact)
    denominator = len(apparatus) + failures
    if denominator == 0:
        return 1.0 if not by_key else 0.0
    justified = sum(_correction_key(entry) in by_key for entry in apparatus)
    return justified / denominator


def _recalled_ids(
    artifact: dict[str, object], corrections: list[Mapping[str, object]]
) -> set[str]:
    apparatus = artifact["apparatus"]
    sections = artifact["sections"]
    assert isinstance(apparatus, list) and isinstance(sections, list)
    reading_by_heading = {
        _text(section.get("heading")): section.get("reading") for section in sections
    }
    apparatus_keys = {_correction_key(entry) for entry in apparatus}
    recalled: set[str] = set()
    for correction in corrections:
        correction_id = _text(correction.get("correction_id"))
        section = _text(correction.get("section"))
        emended = _text(correction.get("emended"))
        reading = reading_by_heading.get(section)
        if (
            correction_id
            and _correction_key(correction) in apparatus_keys
            and isinstance(reading, str)
            and emended in reading
        ):
            recalled.add(correction_id)
    return recalled


def _score_emend_correction_recall(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    if parsed is None:
        return None
    artifact, _, corrections, _ = parsed
    if not corrections:
        return None
    return len(_recalled_ids(artifact, corrections)) / len(corrections)


def _score_emend_apparatus_coverage(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    if parsed is None:
        return None
    artifact, diplomatic, _, _ = parsed
    return float(_coverage_failure_count(diplomatic, artifact) == 0)


def _score_emend_source_support(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    if parsed is None:
        return None
    artifact, diplomatic, _, by_key = parsed
    apparatus = artifact["apparatus"]
    assert isinstance(apparatus, list)
    failures = _coverage_failure_count(diplomatic, artifact)
    denominator = len(apparatus) + failures
    if denominator == 0:
        return None
    supported = 0
    for entry in apparatus:
        correction = by_key.get(_correction_key(entry))
        accepted = (
            None
            if correction is None
            else _string_set(correction.get("accepted_evidence"))
        )
        if accepted is not None and _text(entry.get("evidence")) in accepted:
            supported += 1
    return supported / denominator


def _score_emend_systematic_variant_detection(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    groups = _records(gold.get("systematic_groups"))
    if parsed is None or groups is None:
        return None
    artifact, _, corrections, _ = parsed
    if not groups:
        return None
    recalled = _recalled_ids(artifact, corrections)
    complete = 0
    for group in groups:
        correction_ids = _string_set(group.get("correction_ids"))
        if not correction_ids:
            return None
        complete += correction_ids <= recalled
    return complete / len(groups)


def _score_emend_diplomatic_unchanged(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    parsed = _emend_matches(output, gold)
    if parsed is None:
        return None
    artifact, diplomatic, _, _ = parsed
    sections = artifact["sections"]
    assert isinstance(sections, list)
    if any("original" in section or "diplomatic" in section for section in sections):
        return 0.0
    emitted = output.get("diplomatic_sections")
    if emitted is None:
        return 1.0
    emitted_sections = _records(emitted)
    return 0.0 if emitted_sections is None else float(emitted_sections == diplomatic)


def _score_emend_uncertainty_explicit(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> float | None:
    artifact = _emend_artifact(output)
    uncertainties = _records(gold.get("uncertainties"))
    if artifact is None or uncertainties is None:
        return None
    if not uncertainties:
        return None
    sections = artifact["sections"]
    assert isinstance(sections, list)
    readings = {
        _text(section.get("heading")): section.get("reading") for section in sections
    }
    explicit = 0
    for uncertainty in uncertainties:
        section = _text(uncertainty.get("section"))
        marker = _text(uncertainty.get("marker"))
        if section is None or marker is None:
            return None
        reading = readings.get(section)
        explicit += isinstance(reading, str) and marker in reading
    return explicit / len(uncertainties)


def register_reference_metrics(registry: MetricRegistry) -> None:
    """Register deterministic metrics owned by the ``reference`` station."""

    for metric in (
        Metric(
            "reference_citation_precision",
            MetricDirection.MAXIMIZE,
            _score_reference_citation_precision,
        ),
        Metric(
            "reference_source_support",
            MetricDirection.MAXIMIZE,
            _score_reference_source_support,
        ),
        Metric(
            "reference_bibliographic_correctness",
            MetricDirection.MAXIMIZE,
            _score_reference_bibliographic_correctness,
        ),
        Metric(
            "reference_primary_source_preference",
            MetricDirection.MAXIMIZE,
            _score_reference_primary_source_preference,
        ),
        Metric(
            "reference_claim_source_entailment",
            MetricDirection.MAXIMIZE,
            _score_reference_claim_source_entailment,
        ),
    ):
        registry.register(metric)


def register_emend_metrics(registry: MetricRegistry) -> None:
    """Register deterministic metrics and hard constraints for ``emend``."""

    for metric in (
        Metric(
            "emend_correction_precision",
            MetricDirection.MAXIMIZE,
            _score_emend_correction_precision,
        ),
        Metric(
            "emend_correction_recall",
            MetricDirection.MAXIMIZE,
            _score_emend_correction_recall,
        ),
        Metric(
            "emend_apparatus_coverage",
            MetricDirection.MAXIMIZE,
            _score_emend_apparatus_coverage,
        ),
        Metric(
            "emend_source_support",
            MetricDirection.MAXIMIZE,
            _score_emend_source_support,
        ),
        Metric(
            "emend_systematic_variant_detection",
            MetricDirection.MAXIMIZE,
            _score_emend_systematic_variant_detection,
        ),
        Metric(
            "emend_diplomatic_unchanged",
            MetricDirection.MAXIMIZE,
            _score_emend_diplomatic_unchanged,
        ),
        Metric(
            "emend_uncertainty_explicit",
            MetricDirection.MAXIMIZE,
            _score_emend_uncertainty_explicit,
        ),
    ):
        registry.register(metric)


def register_editorial_metrics(registry: MetricRegistry) -> None:
    """Register all reference and emend evidence metrics."""

    register_reference_metrics(registry)
    register_emend_metrics(registry)
