from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
from typing import Any, Iterable, Optional

from palimpsest.models import (
    DiplomaticBook,
    DiplomaticBookPageRef,
    DiplomaticPage,
    DiplomaticSegment,
    EditorialNote,
    LayoutProjection,
    Page,
    ReviewInfo,
    SegmentAnchors,
    Span,
    Zone,
)

FOLIO_RE = re.compile(r"^f(\d+)([rv])$", re.IGNORECASE)
PAGE_RE = re.compile(r"^page_(\d+)$", re.IGNORECASE)

PREFERRED_TEXT_LAYERS = [
    "source_diplomatic",
    "la_diplomatic",
    "es_diplomatic",
    "source_normalized",
    "la_normalized",
    "es_normalized",
    "en_literal",
    "en_interpreted",
]
TEXTUAL_ZONE_TYPES = {
    "text_block",
    "line",
    "rubric",
    "initial",
    "marginalia",
    "interlinear",
    "diagram_label",
    "caption",
    "colophon",
    "catchword",
    "header",
    "footer",
    "page_number",
    "table_cell",
    "unknown",
}
ROLE_BY_ZONE_TYPE = {
    "text_block": "main_text",
    "line": "main_text",
    "rubric": "rubric",
    "initial": "initial",
    "marginalia": "marginalia",
    "interlinear": "interlinear",
    "diagram_label": "diagram_label",
    "caption": "caption",
    "colophon": "main_text",
    "catchword": "catchword",
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
    "table_cell": "table_cell",
    "unknown": "other",
}
ROLE_BY_REGION = {
    "main_text": "main_text",
    "margin_outer": "marginalia",
    "margin_inner": "marginalia",
    "header": "header",
    "footer": "footer",
    "interlinear": "interlinear",
    "paratext": "other",
    "illustration_label": "diagram_label",
    "table": "table_cell",
    "diagram": "diagram_label",
}
PLACEMENT_BY_REGION = {
    "margin_outer": "margin_outer",
    "margin_inner": "margin_inner",
    "header": "header",
    "footer": "footer",
    "interlinear": "interlinear",
}
BREAK_SUFFIX = {
    "none": "",
    "line": "\n",
    "paragraph": "\n\n",
    "column": "\n\n[column]\n",
    "page": "\n\n[page]\n",
}
PREFIX_BY_ROLE = {
    "rubric": "[rubric] ",
    "initial": "[initial] ",
    "marginalia": "[marginalia] ",
    "interlinear": "[interlinear] ",
    "header": "[header] ",
    "footer": "[footer] ",
    "page_number": "[page_number] ",
    "catchword": "[catchword] ",
    "caption": "[caption] ",
    "diagram_label": "[diagram_label] ",
    "table_cell": "[table_cell] ",
}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _page_sort_key(stem: str) -> tuple[int, int, int, str]:
    folio = FOLIO_RE.match(stem)
    if folio:
        side = 0 if folio.group(2).lower() == "r" else 1
        return (0, int(folio.group(1)), side, stem)
    page = PAGE_RE.match(stem)
    if page:
        return (1, int(page.group(1)), 0, stem)
    return (2, 0, 0, stem)


def _zone_text(zone: Zone, layer: str) -> Optional[str]:
    text = zone.text.get_layer(layer)
    if text:
        return text
    return zone.text.primary_text()


def _choose_basis_layer(page: Page) -> str:
    preferred = None
    if page.restoration and page.restoration.preferred_text_layer:
        preferred = page.restoration.preferred_text_layer
    if preferred and any(_zone_text(zone, preferred) for zone in page.zones):
        return preferred
    for layer in PREFERRED_TEXT_LAYERS:
        if any(zone.text.get_layer(layer) for zone in page.zones):
            return layer
    return "source_diplomatic"


def _is_textual_zone(zone: Zone, basis_layer: str) -> bool:
    if zone.type not in TEXTUAL_ZONE_TYPES:
        return False
    text = _zone_text(zone, basis_layer)
    return bool(text and text.strip())


def _role_for_zone(zone: Zone) -> str:
    zone_role = ROLE_BY_ZONE_TYPE.get(zone.type, "other")
    if zone.type not in {"text_block", "line", "unknown"}:
        return zone_role
    if zone.structure and zone.structure.region_role:
        role = ROLE_BY_REGION.get(zone.structure.region_role)
        if role:
            return role
    return zone_role


def _placement_for_zone(zone: Zone) -> str:
    if zone.structure and zone.structure.region_role:
        placement = PLACEMENT_BY_REGION.get(zone.structure.region_role)
        if placement:
            return placement
    if zone.type == "marginalia":
        return "margin_outer"
    if zone.type == "interlinear":
        return "interlinear"
    if zone.type in {"header", "page_number"}:
        return "header"
    if zone.type in {"footer", "catchword"}:
        return "footer"
    return "main_flow"


def _break_after_for_zone(zone: Zone, role: str) -> str:
    if zone.restoration and zone.restoration.preserve_line_break_after is False:
        return "none"
    if role == "initial" and zone.structure and zone.structure.parent_zone_id:
        return "none"
    if zone.type == "text_block":
        return "paragraph"
    return "line"


def _certainty_for_zone(zone: Zone, text: str) -> str:
    lowered = text.lower()
    if not lowered.strip():
        return "illegible"
    if "[illegible]" in lowered or "<illegible>" in lowered:
        return "illegible"
    if re.search(r"\[[/\s]{2,}\]", text):
        return "illegible"
    if zone.notes and any(note.type in {"supplied", "editorial_supply"} for note in zone.notes):
        return "supplied"
    score = None
    if zone.confidence:
        score = zone.confidence.transcription
    if score is not None and score < 0.45:
        return "damaged"
    if score is not None and score < 0.75:
        return "uncertain"
    if any(token in text for token in ["[?]", "???"]):
        return "uncertain"
    return "certain"


def _editorial_notes(zone: Zone) -> list[EditorialNote]:
    notes: list[EditorialNote] = []
    for note in zone.notes or []:
        notes.append(
            EditorialNote(
                type=note.type,
                note=note.description,
                from_text=note.from_text,
                to_text=note.to_text,
            )
        )
    return notes


def _segment_anchors(zone: Zone) -> SegmentAnchors:
    return SegmentAnchors(
        bbox_norm=zone.bbox_norm,
        baseline_norm=zone.baseline_norm,
    )


def _evidence_spans(zone: Zone, text: str, basis_layer: str) -> list[Span]:
    return [
        Span(
            zone_id=zone.zone_id,
            char_start=0,
            char_end=len(text),
            layer=basis_layer,
        )
    ]


def _layout_projection(page: Page) -> Optional[LayoutProjection]:
    if not page.layout and not page.restoration:
        return None
    return LayoutProjection(
        columns=page.layout.columns if page.layout else None,
        preserve_line_breaks=page.restoration.preserve_line_breaks if page.restoration else True,
        preserve_marginalia_positions=page.restoration.preserve_marginalia_positions if page.restoration else True,
        preserve_interlinear_insertions=page.restoration.preserve_interlinear_insertions if page.restoration else True,
        preserve_rubrication=page.restoration.preserve_rubrication if page.restoration else True,
        preserve_initials=page.restoration.preserve_initials if page.restoration else True,
    )


def _fidelity_flags(page: Page, segments: list[DiplomaticSegment]) -> list[str]:
    flags: list[str] = ["evidence_bound", "provenance_complete"]
    if page.restoration is None or page.restoration.preserve_line_breaks:
        flags.append("line_structure_preserved")
    if page.layout and page.layout.columns > 1:
        flags.append("column_structure_preserved")
    if any(segment.role == "marginalia" for segment in segments):
        flags.append("marginalia_retained")
    if any(segment.role == "interlinear" for segment in segments):
        flags.append("interlinear_retained")
    if any(segment.role == "rubric" for segment in segments):
        flags.append("rubric_distinction_retained")
    if any(segment.certainty != "certain" for segment in segments):
        flags.append("uncertainty_marked")
    return flags


def _open_questions(page: Page, basis_layer: str, segments: list[DiplomaticSegment]) -> list[str]:
    questions: list[str] = []
    if basis_layer not in {"source_diplomatic", "la_diplomatic", "es_diplomatic"}:
        questions.append(f"Restoration fell back to {basis_layer} because no diplomatic layer was present.")
    for segment in segments:
        if segment.certainty in {"uncertain", "damaged", "illegible"}:
            questions.append(f"Review {segment.segment_id} on {page.page_id}: certainty={segment.certainty}.")
    return questions


def render_diplomatic_page_text(page: DiplomaticPage) -> str:
    parts: list[str] = [f"[{page.page_id}]\n"]
    for segment in page.segments:
        prefix = PREFIX_BY_ROLE.get(segment.role, "")
        parts.append(f"{prefix}{segment.text}{BREAK_SUFFIX[segment.break_after]}")
    return "".join(parts).rstrip() + "\n"


def render_diplomatic_book_text(pages: Iterable[DiplomaticPage]) -> str:
    ordered_pages = sorted(pages, key=lambda item: _page_sort_key(item.page_id))
    return "\n".join(render_diplomatic_page_text(page).rstrip() for page in ordered_pages) + "\n"


def render_diplomatic_page_html(page: DiplomaticPage) -> str:
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "  <meta charset=\"utf-8\">",
        f"  <title>{escape(page.doc_id)} {escape(page.page_id)} diplomatic restoration</title>",
        "  <style>",
        "    body { font-family: Georgia, serif; margin: 2rem; line-height: 1.45; }",
        "    .segment { display: block; margin-bottom: 0.1rem; }",
        "    .label { color: #666; font-size: 0.85rem; margin-right: 0.5rem; }",
        "    .marginalia, .interlinear, .header, .footer, .page_number, .catchword { color: #7a1f1f; }",
        "    .rubric { color: #8b0000; }",
        "    .uncertain { background: #fff6bf; }",
        "    .damaged, .illegible { background: #f9d6d5; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>{escape(page.doc_id)} {escape(page.page_id)}</h1>",
    ]
    for segment in page.segments:
        label = PREFIX_BY_ROLE.get(segment.role, "").strip(" []")
        classes = f"segment {escape(segment.role)} {escape(segment.certainty)}"
        text = escape(segment.text)
        if label:
            lines.append(f"  <div class=\"{classes}\"><span class=\"label\">{escape(label)}</span>{text}</div>")
        else:
            lines.append(f"  <div class=\"{classes}\">{text}</div>")
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines) + "\n"


def render_diplomatic_book_html(book: DiplomaticBook, pages: Iterable[DiplomaticPage]) -> str:
    ordered_pages = sorted(pages, key=lambda item: _page_sort_key(item.page_id))
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "  <meta charset=\"utf-8\">",
        f"  <title>{escape(book.doc_id)} diplomatic restoration</title>",
        "  <style>",
        "    body { font-family: Georgia, serif; margin: 2rem; line-height: 1.45; }",
        "    section { margin-bottom: 2.5rem; }",
        "    .segment { display: block; margin-bottom: 0.1rem; }",
        "    .label { color: #666; font-size: 0.85rem; margin-right: 0.5rem; }",
        "    .marginalia, .interlinear, .header, .footer, .page_number, .catchword { color: #7a1f1f; }",
        "    .rubric { color: #8b0000; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>{escape(book.doc_id)} diplomatic restoration</h1>",
    ]
    for page in ordered_pages:
        lines.append(f"  <section id=\"{escape(page.page_id)}\">")
        lines.append(f"    <h2>{escape(page.page_id)}</h2>")
        for segment in page.segments:
            label = PREFIX_BY_ROLE.get(segment.role, "").strip(" []")
            text = escape(segment.text)
            classes = f"segment {escape(segment.role)} {escape(segment.certainty)}"
            if label:
                lines.append(f"    <div class=\"{classes}\"><span class=\"label\">{escape(label)}</span>{text}</div>")
            else:
                lines.append(f"    <div class=\"{classes}\">{text}</div>")
        lines.append("  </section>")
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines) + "\n"


def build_diplomatic_page(page: Page) -> DiplomaticPage:
    basis_layer = _choose_basis_layer(page)
    source_zones = sorted(page.zones, key=lambda zone: zone.order)

    segments: list[DiplomaticSegment] = []
    for zone in source_zones:
        if not _is_textual_zone(zone, basis_layer):
            continue
        text = _zone_text(zone, basis_layer)
        if text is None:
            continue
        role = _role_for_zone(zone)
        segment = DiplomaticSegment(
            segment_id=f"seg_{len(segments) + 1:04d}",
            zone_id=zone.zone_id,
            role=role,
            placement=_placement_for_zone(zone),
            sequence_index=len(segments),
            column_index=zone.structure.column_index if zone.structure else None,
            line_index=zone.structure.line_index if zone.structure else None,
            text=text,
            break_after=_break_after_for_zone(zone, role),
            certainty=_certainty_for_zone(zone, text),
            anchors=_segment_anchors(zone),
            confidence=zone.confidence.transcription if zone.confidence else None,
            evidence_spans=_evidence_spans(zone, text, basis_layer),
            editorial=_editorial_notes(zone) or None,
        )
        segments.append(segment)

    linear_parts: list[str] = []
    for segment in segments:
        prefix = PREFIX_BY_ROLE.get(segment.role, "")
        linear_parts.append(f"{prefix}{segment.text}{BREAK_SUFFIX[segment.break_after]}")
    linear_text = "".join(linear_parts)

    return DiplomaticPage(
        doc_id=page.doc_id,
        page_id=page.page_id,
        created_at=_utc_now(),
        source_image_path=page.image.path,
        basis_layer=basis_layer,
        layout_projection=_layout_projection(page),
        segments=segments,
        linear_text=linear_text,
        fidelity_flags=_fidelity_flags(page, segments),
        open_questions=_open_questions(page, basis_layer, segments) or None,
        review=ReviewInfo(status="machine_only"),
        render_hints={
            "preferred_output_order": ["json", "txt", "html"],
            "preserve_role_labels_in_linear_view": True,
        },
    )


def build_diplomatic_book(pages: Iterable[DiplomaticPage], doc_id: Optional[str] = None) -> DiplomaticBook:
    ordered_pages = sorted(pages, key=lambda item: _page_sort_key(item.page_id))
    if not ordered_pages:
        raise ValueError("Cannot build diplomatic book with no pages")
    resolved_doc_id = doc_id or ordered_pages[0].doc_id
    page_refs = [
        DiplomaticBookPageRef(
            page_id=page.page_id,
            segment_count=len(page.segments),
            line_count=page.linear_text.count("\n"),
        )
        for page in ordered_pages
    ]
    return DiplomaticBook(
        doc_id=resolved_doc_id,
        created_at=_utc_now(),
        pages=page_refs,
        book_text=render_diplomatic_book_text(ordered_pages),
        review=ReviewInfo(status="machine_only"),
    )


def export_diplomatic_page(page: DiplomaticPage, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{page.page_id}_diplomatic.json"
    txt_path = out_dir / f"{page.page_id}_diplomatic.txt"
    html_path = out_dir / f"{page.page_id}_diplomatic.html"
    page.save(str(json_path))
    txt_path.write_text(render_diplomatic_page_text(page), encoding="utf-8")
    html_path.write_text(render_diplomatic_page_html(page), encoding="utf-8")
    return {
        "json": str(json_path),
        "txt": str(txt_path),
        "html": str(html_path),
    }


def export_diplomatic_book(book: DiplomaticBook, pages: Iterable[DiplomaticPage], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered_pages = sorted(pages, key=lambda item: _page_sort_key(item.page_id))
    json_path = out_dir / "book_diplomatic.json"
    txt_path = out_dir / "book_diplomatic.txt"
    html_path = out_dir / "book_diplomatic.html"
    book.save(str(json_path))
    txt_path.write_text(render_diplomatic_book_text(ordered_pages), encoding="utf-8")
    html_path.write_text(render_diplomatic_book_html(book, ordered_pages), encoding="utf-8")
    return {
        "json": str(json_path),
        "txt": str(txt_path),
        "html": str(html_path),
    }


def _load_canonical_pages(page_files: list[Path]) -> list[Page]:
    pages: list[Page] = []
    for path in page_files:
        try:
            page = Page.from_file(str(path))
        except Exception:
            continue
        if page.schema_version == "canonical.page":
            pages.append(page)
    return pages


def assemble_diplomatic_book(pages_dir: Path, out_dir: Optional[Path] = None) -> dict[str, Any]:
    pages_dir = pages_dir.resolve()
    resolved_out_dir = (out_dir or pages_dir.parent / "restoration").resolve()
    page_files = sorted(pages_dir.glob("*.json"), key=lambda path: _page_sort_key(path.stem))
    pages = _load_canonical_pages(page_files)
    if not pages:
        raise ValueError(f"No canonical.page JSON files found in {pages_dir}")

    diplomatic_pages = [build_diplomatic_page(page) for page in pages]
    book = build_diplomatic_book(diplomatic_pages, doc_id=pages[0].doc_id)
    page_outputs_dir = resolved_out_dir / "pages"
    book_outputs_dir = resolved_out_dir / "book"

    page_outputs = {page.page_id: export_diplomatic_page(page, page_outputs_dir) for page in diplomatic_pages}
    page_refs = [
        DiplomaticBookPageRef(
            page_id=page.page_id,
            path=str(Path("..") / "pages" / Path(page_outputs[page.page_id]["json"]).name),
            segment_count=len(page.segments),
            line_count=page.linear_text.count("\n"),
        )
        for page in sorted(diplomatic_pages, key=lambda item: _page_sort_key(item.page_id))
    ]
    exported_book = book.model_copy(update={"pages": page_refs})
    book_outputs = export_diplomatic_book(exported_book, diplomatic_pages, book_outputs_dir)

    index = {
        "doc_id": exported_book.doc_id,
        "generated_at": exported_book.created_at,
        "total_pages": len(diplomatic_pages),
        "pages_dir": str(pages_dir),
        "outputs": {
            "pages": str(page_outputs_dir),
            "book": book_outputs,
        },
        "pages": [
            {
                "page_id": page.page_id,
                "segment_count": len(page.segments),
                "basis_layer": page.basis_layer,
                "outputs": page_outputs[page.page_id],
            }
            for page in sorted(diplomatic_pages, key=lambda item: _page_sort_key(item.page_id))
        ],
    }
    book_index = {
        "doc_id": exported_book.doc_id,
        "generated_at": exported_book.created_at,
        "total_pages": len(exported_book.pages),
        "pages": [page.model_dump(exclude_none=True) for page in exported_book.pages],
        "outputs": book_outputs,
    }
    (book_outputs_dir / "restoration_index.json").write_text(
        json.dumps(book_index, indent=2),
        encoding="utf-8",
    )
    (resolved_out_dir / "restoration_manifest.json").write_text(
        json.dumps(index, indent=2),
        encoding="utf-8",
    )
    return index
