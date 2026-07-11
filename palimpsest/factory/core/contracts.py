"""The artifact contract registry: every kind that can flow between stations.

This is the factory's type system. A station's ``consumes``/``produces``
must name kinds defined here (checked at registration), a JSON artifact must
carry its kind's required fields (checked by the conductor at write time),
and the workspace path layout derives from the ``store`` templates — one
concept, one place.

``palimpsest factory graph`` renders this registry plus the live station
registry as the contract graph, so the documented graph is generated from
code and cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

FORMAT_SUFFIX = {"json": ".json", "jpeg": ".jpg", "epub": ".epub"}

# Pseudo-kind: the work order's page list, produced by intake/promote rather
# than a station. Stations may consume it; nothing produces it on the line.
SOURCE_KINDS = ("page_list",)


@dataclass(frozen=True)
class ArtifactContract:
    kind: str
    grain: str                      # "page" | "manuscript"
    format: str                     # key of FORMAT_SUFFIX
    description: str
    required: tuple[str, ...] = ()  # top-level fields a JSON payload must carry
    store: str | None = None        # doc-grain filename template ({doc_id} ok)


_ALL = (
    ArtifactContract(
        "page_image", "page", "jpeg",
        "Page image exactly as the archive delivered it."),
    ArtifactContract(
        "page_image_framed", "page", "jpeg",
        "Cropped to the detected parchment frame — backdrop/binding gone."),
    ArtifactContract(
        "page_image_unmarked", "page", "jpeg",
        "Digital overlays (watermarks, stamps) painted back to background."),
    ArtifactContract(
        "page_image_clean", "page", "jpeg",
        "Illumination-flattened study image; what segment and read consume."),
    ArtifactContract(
        "page_regions", "page", "json",
        "Polygon lassos + the routing decision (blank | full_page | segmented).",
        required=("doc_id", "page_id", "route", "image", "regions")),
    ArtifactContract(
        "page_transcription", "page", "json",
        "Diplomatic transcription; per-region texts when the page was segmented.",
        required=("doc_id", "page_id", "text", "route", "regions")),
    ArtifactContract(
        "page_translation", "page", "json",
        "English translation of one page, with continuity flags.",
        required=("doc_id", "page_id", "translation", "flags")),
    ArtifactContract(
        "page_assembled", "page", "json",
        "The small loop's finished part: original ∥ translation, aligned.",
        required=("doc_id", "page_id", "original", "translation", "inputs")),
    ArtifactContract(
        "translation_brief", "manuscript", "json",
        "The jig: glossary, outline, entities, flags guiding every translate.",
        required=("version", "document", "glossary", "outline"),
        store="translation_brief.json"),
    ArtifactContract(
        "manuscript", "manuscript", "json",
        "Reconstruction: sections in both languages + auditable joins.",
        required=("doc_id", "sections", "joins", "readers_note"),
        store="manuscript.json"),
    ArtifactContract(
        "reference", "manuscript", "json",
        "The reference dossier: document identification plus, per passage "
        "that tracks a transmitted text, the controlling received wording "
        "with citation, confidence, and verification source.",
        required=("doc_id", "identification", "reference_points"),
        store="reference.json"),
    ArtifactContract(
        "emendations", "manuscript", "json",
        "The final editorial pass: an emended reading per section + the "
        "apparatus recording every change. The diplomatic layer is never "
        "edited; this sits beside it.",
        required=("doc_id", "sections", "apparatus"),
        store="emendations.json"),
    ArtifactContract(
        "book", "manuscript", "json",
        "The book model: bilingual chapters + provenance colophon.",
        required=("doc_id", "title", "language", "chapters", "colophon"),
        store="book/book.json"),
    ArtifactContract(
        "book_epub", "manuscript", "epub",
        "EPUB 3 rendering of the book model.",
        store="book/{doc_id}.epub"),
)

CONTRACTS: dict[str, ArtifactContract] = {c.kind: c for c in _ALL}


def contract(kind: str) -> ArtifactContract:
    try:
        return CONTRACTS[kind]
    except KeyError:
        raise KeyError(
            f"Unknown artifact kind {kind!r}. Known: {sorted(CONTRACTS)}"
        ) from None


def validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    """Raise if a JSON artifact violates its kind's output contract."""
    spec = contract(kind)
    if spec.format != "json":
        raise ValueError(f"Kind {kind!r} is {spec.format}, not a JSON payload")
    missing = [field for field in spec.required if field not in payload]
    if missing:
        raise ValueError(
            f"Artifact of kind {kind!r} violates its contract: "
            f"missing required fields {missing}"
        )
