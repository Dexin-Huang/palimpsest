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

BOOK_SCHEMA_VERSION = 2
BOOK_PROFILE = "facsimile-spread"

CATALOG_RECORD_ID_RE = re.compile(r"^source-record:[0-9a-f]{64}$")

TRANSCRIPTION_AUDIT_FIELDS = (
    "candidate_readings",
    "adjudication_status",
    "adjudication_requested_model",
    "adjudication_model",
    "adjudication_reasoning",
    "unresolved",
    "adjudication_error",
)


def transcription_audit(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the durable reader/adjudicator evidence from a transcription."""
    return {field: payload.get(field) for field in TRANSCRIPTION_AUDIT_FIELDS}


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
        required=("doc_id", "catalog_record_id"),
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
        required=(
            "doc_id",
            "page_id",
            "text",
            "route",
            "regions",
            "candidate_readings",
            "adjudication_status",
            "adjudication_requested_model",
            "adjudication_model",
            "adjudication_reasoning",
            "unresolved",
            "adjudication_error",
        ),
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
        "edition",
        "manuscript",
        "json",
        "Reader-facing prose reconciled against the final emended reading: "
        "one heading and translation per manuscript section, plus the "
        "manuscript-level reader's note.",
        required=("doc_id", "readers_note", "sections"),
        store="edition.json",
    ),
    ArtifactContract(
        "book",
        "manuscript",
        "json",
        "Book Model: normalized folios, reader-facing sections, explicit "
        "source descriptors, editorial apparatus, and production colophon.",
        required=(
            "schema_version",
            "profile",
            "doc_id",
            "catalog_record_id",
            "identity",
            "languages",
            "readers_note",
            "folios",
            "sections",
            "apparatus",
            "colophon",
        ),
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
    if "doc_id" in payload:
        doc_id = validate_doc_id(payload["doc_id"])
        if expected_doc_id is not None and doc_id != expected_doc_id:
            raise ValueError(
                f"{kind} doc_id {doc_id!r} does not match {expected_doc_id!r}"
            )
    if kind == "metadata":
        _validate_metadata(payload)
    elif kind == "page_list":
        _validate_pages(payload["pages"])
    elif kind == "book":
        _validate_book(payload)


def _validate_metadata(payload: Mapping[str, Any]) -> None:
    _validate_catalog_record_id(
        payload["catalog_record_id"], "metadata catalog_record_id"
    )


def _validate_catalog_record_id(value: object, label: str) -> str | None:
    if value is None:
        return None
    record_id = _string(value, label)
    if not CATALOG_RECORD_ID_RE.fullmatch(record_id):
        raise ValueError(
            f"{label} must be a source-record pointer "
            "(source-record:<64 hex chars>) or null"
        )
    return record_id


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


def _validate_book(payload: Mapping[str, Any]) -> None:
    root = _object(
        payload,
        "book",
        required=(
            "schema_version",
            "profile",
            "doc_id",
            "catalog_record_id",
            "identity",
            "languages",
            "readers_note",
            "folios",
            "sections",
            "apparatus",
            "colophon",
        ),
        optional=("provenance",),
    )
    if root["schema_version"] != BOOK_SCHEMA_VERSION:
        raise ValueError(
            f"book schema_version must be {BOOK_SCHEMA_VERSION}, "
            f"got {root['schema_version']!r}"
        )
    if root["profile"] != BOOK_PROFILE:
        raise ValueError(f"book profile must be {BOOK_PROFILE!r}")
    _validate_catalog_record_id(root["catalog_record_id"], "book catalog_record_id")

    identity = _object(
        root["identity"],
        "book identity",
        required=("title", "author", "archive", "shelfmark", "date"),
    )
    _nonempty_string(identity["title"], "book identity title")
    _nullable_string(identity["author"], "book identity author")
    _nonempty_string(identity["archive"], "book identity archive")
    _nullable_string(identity["shelfmark"], "book identity shelfmark")
    _nullable_string(identity["date"], "book identity date")

    languages = _object(
        root["languages"],
        "book languages",
        required=("original", "translation"),
    )
    _nonempty_string(languages["original"], "book original language")
    _nonempty_string(languages["translation"], "book translation language")
    _string(root["readers_note"], "book readers_note")

    folios = _nonempty_list(root["folios"], "book folios")
    folio_orders: dict[str, int] = {}
    previous_order: int | None = None
    for index, value in enumerate(folios):
        folio = _object(
            value,
            f"book folio {index}",
            required=("page_id", "order", "images", "evidence"),
        )
        page_id = _nonempty_string(folio["page_id"], f"book folio {index} page_id")
        if page_id in folio_orders:
            raise ValueError(f"book contains duplicate folio page_id {page_id!r}")
        order = _integer(folio["order"], f"book folio {page_id} order")
        if previous_order is not None and order <= previous_order:
            raise ValueError("book folios must be in strictly increasing source order")
        previous_order = order
        folio_orders[page_id] = order
        _validate_folio_images(folio["images"], page_id)
        _validate_folio_evidence(folio["evidence"], page_id)

    sections = _nonempty_list(root["sections"], "book sections")
    section_ids: set[str] = set()
    referenced_apparatus: list[str] = []
    for index, value in enumerate(sections, start=1):
        section = _object(
            value,
            f"book section {index}",
            required=(
                "id",
                "order",
                "heading",
                "folio_ids",
                "content",
                "apparatus_ids",
            ),
        )
        section_id = _nonempty_string(section["id"], f"book section {index} id")
        if section_id in section_ids:
            raise ValueError(f"book contains duplicate section id {section_id!r}")
        section_ids.add(section_id)
        if _integer(section["order"], f"book section {section_id} order") != index:
            raise ValueError("book section order must be contiguous and one-based")
        _nonempty_string(section["heading"], f"book section {section_id} heading")

        folio_ids = _nonempty_list(
            section["folio_ids"], f"book section {section_id} folio_ids"
        )
        section_orders: list[int] = []
        seen_folios: set[str] = set()
        for folio_id_value in folio_ids:
            folio_id = _nonempty_string(
                folio_id_value, f"book section {section_id} folio_id"
            )
            if folio_id in seen_folios:
                raise ValueError(
                    f"book section {section_id!r} cites folio {folio_id!r} twice"
                )
            if folio_id not in folio_orders:
                raise ValueError(
                    f"book section {section_id!r} cites unknown folio {folio_id!r}"
                )
            seen_folios.add(folio_id)
            section_orders.append(folio_orders[folio_id])
        if section_orders != sorted(section_orders):
            raise ValueError(
                f"book section {section_id!r} folios are not in source order"
            )

        _validate_section_content(section["content"], section_id)
        apparatus_ids = _list(
            section["apparatus_ids"], f"book section {section_id} apparatus_ids"
        )
        seen_apparatus: set[str] = set()
        for apparatus_id_value in apparatus_ids:
            apparatus_id = _nonempty_string(
                apparatus_id_value,
                f"book section {section_id} apparatus id",
            )
            if apparatus_id in seen_apparatus:
                raise ValueError(
                    f"book section {section_id!r} cites apparatus "
                    f"{apparatus_id!r} twice"
                )
            seen_apparatus.add(apparatus_id)
            referenced_apparatus.append(apparatus_id)

    apparatus = _list(root["apparatus"], "book apparatus")
    apparatus_ids: set[str] = set()
    for index, value in enumerate(apparatus, start=1):
        entry = _object(
            value,
            f"book apparatus {index}",
            required=(
                "id",
                "section_id",
                "original",
                "emended",
                "reason",
                "evidence",
            ),
        )
        apparatus_id = _nonempty_string(entry["id"], f"book apparatus {index} id")
        if apparatus_id in apparatus_ids:
            raise ValueError(f"book contains duplicate apparatus id {apparatus_id!r}")
        apparatus_ids.add(apparatus_id)
        section_id = _nonempty_string(
            entry["section_id"], f"book apparatus {apparatus_id} section_id"
        )
        if section_id not in section_ids:
            raise ValueError(
                f"book apparatus {apparatus_id!r} cites unknown section {section_id!r}"
            )
        for field in ("original", "emended"):
            _string(entry[field], f"book apparatus {apparatus_id} {field}")
        _nonempty_string(entry["reason"], f"book apparatus {apparatus_id} reason")
        _string(entry["evidence"], f"book apparatus {apparatus_id} evidence")

    if len(referenced_apparatus) != len(set(referenced_apparatus)):
        raise ValueError("book apparatus entries must be cited by exactly one section")
    if set(referenced_apparatus) != apparatus_ids:
        raise ValueError("book section apparatus_ids do not match book apparatus")

    _validate_colophon(root["colophon"], len(folios))


def _validate_folio_images(value: object, page_id: str) -> None:
    images = _object(
        value,
        f"book folio {page_id} images",
        required=("original",),
        optional=("enhanced",),
    )
    _validate_image_source(images["original"], page_id, "page_image", "original")
    if "enhanced" in images:
        _validate_image_source(
            images["enhanced"], page_id, "page_image_clean", "enhanced"
        )


def _validate_image_source(
    value: object, page_id: str, expected_kind: str, label: str
) -> None:
    source = _object(
        value,
        f"book folio {page_id} {label} image",
        required=("kind", "page_id", "fingerprint"),
        optional=("source_url",),
    )
    if source["kind"] != expected_kind:
        raise ValueError(
            f"book folio {page_id} {label} image kind must be {expected_kind!r}"
        )
    if source["page_id"] != page_id:
        raise ValueError(f"book folio {page_id} {label} image has a different page_id")
    _fingerprint(source["fingerprint"], f"book folio {page_id} {label} fingerprint")
    if label == "original":
        _nonempty_string(
            source.get("source_url"), f"book folio {page_id} original source_url"
        )
    elif "source_url" in source:
        _nonempty_string(
            source["source_url"], f"book folio {page_id} enhanced source_url"
        )


def _validate_folio_evidence(value: object, page_id: str) -> None:
    evidence = _object(
        value,
        f"book folio {page_id} evidence",
        required=("diplomatic", "translation"),
        optional=("alignment",),
    )
    diplomatic = _object(
        evidence["diplomatic"],
        f"book folio {page_id} diplomatic evidence",
        required=("text", "audit", "source"),
    )
    _nonempty_string(diplomatic["text"], f"book folio {page_id} diplomatic text")
    _object(
        diplomatic["audit"],
        f"book folio {page_id} transcription audit",
        required=TRANSCRIPTION_AUDIT_FIELDS,
        allow_extra=True,
    )
    _validate_source_ref(
        diplomatic["source"],
        f"book folio {page_id} diplomatic source",
        "page_transcription",
    )

    translation = _object(
        evidence["translation"],
        f"book folio {page_id} translation evidence",
        required=("text", "notes", "flags", "seam", "source"),
    )
    _nonempty_string(translation["text"], f"book folio {page_id} translation text")
    _string(translation["notes"], f"book folio {page_id} translation notes")
    _object(
        translation["flags"],
        f"book folio {page_id} translation flags",
        allow_extra=True,
    )
    if translation["seam"] is not None:
        _object(
            translation["seam"],
            f"book folio {page_id} translation seam",
            allow_extra=True,
        )
    _validate_source_ref(
        translation["source"],
        f"book folio {page_id} translation source",
        "page_translation",
    )

    if "alignment" in evidence:
        alignment = _object(
            evidence["alignment"],
            f"book folio {page_id} alignment",
            required=("columns", "stats", "source"),
        )
        _list(alignment["columns"], f"book folio {page_id} alignment columns")
        _object(
            alignment["stats"],
            f"book folio {page_id} alignment stats",
            allow_extra=True,
        )
        _validate_source_ref(
            alignment["source"],
            f"book folio {page_id} alignment source",
            "page_alignment",
        )


def _validate_section_content(value: object, section_id: str) -> None:
    content = _object(
        value,
        f"book section {section_id} content",
        required=(
            "translation",
            "emended_reading",
            "diplomatic_transcription",
        ),
    )
    expected_kinds = {
        "translation": "edition",
        "emended_reading": "emendations",
        "diplomatic_transcription": "manuscript",
    }
    for role, expected_kind in expected_kinds.items():
        layer = _object(
            content[role],
            f"book section {section_id} {role}",
            required=("text", "source"),
        )
        _nonempty_string(layer["text"], f"book section {section_id} {role} text")
        _validate_source_ref(
            layer["source"],
            f"book section {section_id} {role} source",
            expected_kind,
        )


def _validate_source_ref(
    value: object, label: str, expected_kind: str | None = None
) -> None:
    source = _object(
        value,
        label,
        required=("kind", "pointer", "fingerprint"),
    )
    kind = _nonempty_string(source["kind"], f"{label} kind")
    if expected_kind is not None and kind != expected_kind:
        raise ValueError(f"{label} kind must be {expected_kind!r}")
    pointer = _nonempty_string(source["pointer"], f"{label} pointer")
    if not pointer.startswith("/"):
        raise ValueError(f"{label} pointer must be a JSON Pointer")
    _fingerprint(source["fingerprint"], f"{label} fingerprint")


def _validate_colophon(value: object, folio_count: int) -> None:
    colophon = _object(
        value,
        "book colophon",
        required=(
            "pipeline",
            "cost_usd_total",
            "cost_usd_known",
            "cost_complete",
            "pages",
        ),
        optional=(
            "transcribed_by",
            "translated_by",
            "referenced_by",
            "emended_by",
            "finalized_by",
        ),
    )
    if _integer(colophon["pages"], "book colophon pages") != folio_count:
        raise ValueError("book colophon pages must equal the folio count")
    if not isinstance(colophon["cost_complete"], bool):
        raise ValueError("book colophon cost_complete must be a boolean")
    _nonnegative_number(colophon["cost_usd_known"], "book colophon cost_usd_known")
    if colophon["cost_complete"]:
        _nonnegative_number(colophon["cost_usd_total"], "book colophon cost_usd_total")
    elif colophon["cost_usd_total"] is not None:
        raise ValueError(
            "book colophon cost_usd_total must be null when cost is incomplete"
        )

    pipeline = _nonempty_list(colophon["pipeline"], "book colophon pipeline")
    seen_stations: set[str] = set()
    for index, value in enumerate(pipeline):
        stage = _object(
            value,
            f"book colophon pipeline stage {index}",
            required=(
                "station",
                "runs",
                "tokens_in",
                "tokens_out",
                "cost_usd",
                "cost_complete",
                "cost_usd_known",
                "configurations",
            ),
        )
        station = _nonempty_string(
            stage["station"], f"book colophon pipeline stage {index} station"
        )
        if station in seen_stations:
            raise ValueError(f"book colophon repeats pipeline station {station!r}")
        seen_stations.add(station)
        if _integer(stage["runs"], f"book colophon {station} runs") < 1:
            raise ValueError(f"book colophon {station} runs must be positive")
        _list(
            stage["configurations"],
            f"book colophon {station} configurations",
        )


def _object(
    value: object,
    label: str,
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    allow_extra: bool = False,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError(f"{label} is missing required fields {missing}")
    if not allow_extra:
        unexpected = set(value) - set(required) - set(optional)
        if unexpected:
            raise ValueError(f"{label} has unexpected fields {sorted(unexpected)}")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _nonempty_list(value: object, label: str) -> list[Any]:
    items = _list(value, label)
    if not items:
        raise ValueError(f"{label} must be a nonempty JSON array")
    return items


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _nonempty_string(value: object, label: str) -> str:
    text = _string(value, label)
    if not text.strip():
        raise ValueError(f"{label} must be nonempty")
    return text


def _nullable_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _nonnegative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return float(value)


def _fingerprint(value: object, label: str) -> str:
    fingerprint = _nonempty_string(value, label)
    if not re.fullmatch(r"[0-9a-f]{16}", fingerprint):
        raise ValueError(f"{label} must be a lowercase content fingerprint")
    return fingerprint
