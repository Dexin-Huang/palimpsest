"""The artifact contract registry: every kind that can flow between stations.

This is the factory's type system. A station's ``consumes``/``produces``
must name kinds defined here (checked at registration), a JSON artifact must
carry its kind's required fields (checked by the cell runtime at write time),
and the workspace path layout derives from the ``store`` templates — one
concept, one place.

``palimpsest graph`` renders this registry plus the live station
registry as the contract graph, so the documented graph is generated from
code and cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

DOC_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
FORMAT_SUFFIX = {"json": ".json", "jpeg": ".jpg", "epub": ".epub"}

# Source contracts enter through intake rather than a station. They still live
# in the same registry so path resolution, validation, and graph generation
# have one source of truth.
SOURCE_KINDS = ("metadata", "page_list")


@dataclass(frozen=True)
class ArtifactContract:
    kind: str
    grain: str  # "page" | "manuscript"
    format: str  # key of FORMAT_SUFFIX
    description: str
    required: tuple[str, ...] = ()  # top-level fields a JSON payload must carry
    store: str | None = None  # doc-grain filename template ({doc_id} ok)


_ALL = (
    ArtifactContract(
        "metadata",
        "manuscript",
        "json",
        "Catalog identity and immutable source provenance for one work order.",
        required=("doc_id",),
        store="metadata.json",
    ),
    ArtifactContract(
        "page_list",
        "manuscript",
        "json",
        "Ordered source canvases and image URLs consumed by the page line.",
        required=("doc_id", "pages"),
        store="page_list.json",
    ),
    ArtifactContract(
        "page_image", "page", "jpeg", "Page image exactly as the archive delivered it."
    ),
    ArtifactContract(
        "page_image_framed",
        "page",
        "jpeg",
        "Cropped to the detected parchment frame — backdrop/binding gone.",
    ),
    ArtifactContract(
        "page_image_unmarked",
        "page",
        "jpeg",
        "Digital overlays (watermarks, stamps) painted back to background.",
    ),
    ArtifactContract(
        "page_image_clean",
        "page",
        "jpeg",
        "Illumination-flattened study image; what segment and read consume.",
    ),
    ArtifactContract(
        "page_regions",
        "page",
        "json",
        "Polygon lassos + the routing decision (blank | full_page | segmented).",
        required=("doc_id", "page_id", "route", "image", "regions"),
    ),
    ArtifactContract(
        "page_transcription",
        "page",
        "json",
        "Diplomatic transcription; per-region texts when the page was segmented.",
        required=("doc_id", "page_id", "text", "route", "regions"),
    ),
    ArtifactContract(
        "page_translation",
        "page",
        "json",
        "English translation of one page, with continuity flags.",
        required=("doc_id", "page_id", "translation", "flags"),
    ),
    ArtifactContract(
        "page_alignment",
        "page",
        "json",
        "Forced alignment: per-character ink bounding boxes + count stats. "
        "Unbound characters are marked, never forced.",
        required=("doc_id", "page_id", "columns", "stats"),
    ),
    ArtifactContract(
        "page_assembled",
        "page",
        "json",
        "Deterministic page pair: diplomatic original and its translation.",
        required=("doc_id", "page_id", "original", "translation", "inputs"),
    ),
    ArtifactContract(
        "translation_brief",
        "manuscript",
        "json",
        "The jig: glossary, outline, entities, flags guiding every translate.",
        required=("document", "glossary", "outline"),
        store="translation_brief.json",
    ),
    ArtifactContract(
        "manuscript",
        "manuscript",
        "json",
        "Reconstruction: sections in both languages + auditable joins.",
        required=("doc_id", "sections", "joins", "readers_note"),
        store="manuscript.json",
    ),
    ArtifactContract(
        "reference",
        "manuscript",
        "json",
        "The reference dossier: document identification plus, per passage "
        "that tracks a transmitted text, the controlling received wording "
        "with citation, confidence, and verification source.",
        required=("doc_id", "identification", "reference_points"),
        store="reference.json",
    ),
    ArtifactContract(
        "emendations",
        "manuscript",
        "json",
        "The final editorial pass: an emended reading per section + the "
        "apparatus recording every change. The diplomatic layer is never "
        "edited; this sits beside it.",
        required=("doc_id", "sections", "apparatus"),
        store="emendations.json",
    ),
    ArtifactContract(
        "book",
        "manuscript",
        "json",
        "The book model: bilingual chapters, page-level source evidence, "
        "alignment geometry when available, and a provenance colophon.",
        required=("doc_id", "title", "language", "chapters", "evidence", "colophon"),
        store="book/book.json",
    ),
    ArtifactContract(
        "book_epub",
        "manuscript",
        "epub",
        "EPUB 3 rendering of the book model.",
        store="book/{doc_id}.epub",
    ),
)

CONTRACTS: dict[str, ArtifactContract] = {c.kind: c for c in _ALL}


def contract(kind: str) -> ArtifactContract:
    try:
        return CONTRACTS[kind]
    except KeyError:
        raise KeyError(
            f"Unknown artifact kind {kind!r}. Known: {sorted(CONTRACTS)}"
        ) from None


def validate_doc_id(doc_id: object) -> str:
    if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id):
        raise ValueError(
            "doc_id must contain lowercase ASCII letters, digits, and single underscores"
        )
    return doc_id


def validate_payload(
    kind: str,
    payload: Mapping[str, Any],
    *,
    expected_doc_id: str | None = None,
) -> None:
    """Raise if a JSON artifact violates its kind's output contract."""
    spec = contract(kind)
    if spec.format != "json":
        raise ValueError(f"Kind {kind!r} is {spec.format}, not a JSON payload")
    if not isinstance(payload, Mapping):
        raise ValueError(f"Artifact of kind {kind!r} must be a JSON object")
    missing = [field for field in spec.required if field not in payload]
    if missing:
        raise ValueError(
            f"Artifact of kind {kind!r} violates its contract: "
            f"missing required fields {missing}"
        )
    if kind not in SOURCE_KINDS:
        return

    doc_id = validate_doc_id(payload["doc_id"])
    if expected_doc_id is not None and doc_id != expected_doc_id:
        raise ValueError(f"{kind} doc_id {doc_id!r} does not match {expected_doc_id!r}")
    if kind == "page_list":
        _validate_pages(payload["pages"])


def _validate_pages(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("page_list pages must be a nonempty list")
    seen: set[str] = set()
    for index, page in enumerate(value):
        if not isinstance(page, Mapping):
            raise ValueError(f"page_list page {index} must be a JSON object")
        page_id = page.get("page_id")
        if not isinstance(page_id, str) or not page_id.strip():
            raise ValueError(f"page_list page {index} has an invalid page_id")
        if page_id in seen:
            raise ValueError(f"page_list contains duplicate page_id {page_id!r}")
        seen.add(page_id)
        url = page.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"page_list page {page_id!r} has an invalid url")
        order = page.get("order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"page_list page {page_id!r} has an invalid order")
