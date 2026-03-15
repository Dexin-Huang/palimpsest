from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from palimpsest.contracts import folio_render_path, render_html_path, render_meta_path
from palimpsest.models.folio_render import FolioRender
from palimpsest.models.packet import PagePacket
from palimpsest.packets.state import load_packet_json
from palimpsest.web.folio_fragments import build_folio_render as web_build_folio_render
from palimpsest.web.folio_page import render_folio_document_html as web_render_folio_document_html
from palimpsest.web.markup import (
    MarkdownDocument,
    group_document_sections as web_group_document_sections,
    groups_to_template_sections as web_groups_to_template_sections,
    parse_markdown_document as web_parse_markdown_document,
)

from .common import display_page_id, read_text, relpath, utc_now
from .content import (
    build_image_regions_from_assembly,
    build_interpretation_content,
    build_witness_content_from_assembly,
    load_page_assembly,
    pick_translation_sections,
    pick_witness_sections,
)


@dataclass
class RenderedPacketHtmlArtifact:
    packet_path: Path
    html_path: Path
    folio_render_path: Path
    meta_path: Path


def render_folio_html(
    *,
    packet: PagePacket,
    image_href: str,
    book_title: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
    include_cover: bool = True,
) -> tuple[str, FolioRender]:
    witness_doc = web_parse_markdown_document(read_text(packet.files.get("witness").path if packet.files.get("witness") else None))
    translation_doc = web_parse_markdown_document(read_text(packet.files.get("translation").path if packet.files.get("translation") else None))
    interpretation_doc = web_parse_markdown_document(read_text(packet.files.get("interpretation").path if packet.files.get("interpretation") else None))
    notes_doc = web_parse_markdown_document(read_text(packet.files.get("notes").path if packet.files.get("notes") else None))
    terms_doc = web_parse_markdown_document(read_text(packet.files.get("terms").path if packet.files.get("terms") else None))
    questions_doc = web_parse_markdown_document(read_text(packet.files.get("questions").path if packet.files.get("questions") else None))

    witness_main, witness_extra = pick_witness_sections(witness_doc)
    translation_main, translation_extra = pick_translation_sections(translation_doc)
    witness_main_doc = MarkdownDocument(title=witness_doc.title, preamble=witness_doc.preamble, sections=witness_main)
    witness_extra_doc = MarkdownDocument(title="Witness Notes", preamble="", sections=witness_extra)
    translation_main_doc = MarkdownDocument(title=translation_doc.title, preamble=translation_doc.preamble, sections=translation_main)
    translation_extra_doc = MarkdownDocument(title="Translation Notes", preamble="", sections=translation_extra)

    display_page = display_page_id(packet.page_id)
    title = f"{display_page} - {book_title}"

    content_sections = [
        *web_groups_to_template_sections(
            web_group_document_sections(witness_main_doc),
            kind="witness",
            preserve_linebreaks=True,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(translation_main_doc),
            kind="translation",
            preserve_linebreaks=False,
        ),
    ]
    interpretation_sections = [
        *web_groups_to_template_sections(
            web_group_document_sections(interpretation_doc),
            kind="interpretation",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(notes_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(witness_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(translation_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(terms_doc),
            kind="terms",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(questions_doc),
            kind="questions",
            preserve_linebreaks=False,
        ),
    ]

    page_assembly = load_page_assembly(packet)
    witness_content = build_witness_content_from_assembly(page_assembly)
    image_regions = build_image_regions_from_assembly(page_assembly)
    interpretation_content = build_interpretation_content(
        interpretation_doc,
        notes_doc,
        terms_doc,
        questions_doc,
    )

    folio = web_build_folio_render(
        packet=packet,
        book_title=book_title,
        image_href=image_href,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        content_sections=content_sections,
        interpretation_sections=interpretation_sections,
        witness_content=witness_content,
        interpretation_content=interpretation_content,
        image_regions=image_regions,
        page_label=display_page,
        created_at=utc_now(),
    )
    html = web_render_folio_document_html(
        title=title,
        folio=folio,
        include_cover=include_cover,
    )
    return html, folio


def render_packet_folio_html(
    packet_path: Path,
    *,
    packet: PagePacket | None = None,
    out_dir: Path | None = None,
    book_title: str | None = None,
    image_href: str | None = None,
    prev_href: str | None = None,
    next_href: str | None = None,
    home_href: str | None = None,
    include_cover: bool = True,
) -> RenderedPacketHtmlArtifact:
    packet_path = Path(packet_path).resolve()
    packet = packet or load_packet_json(packet_path)
    packet_dir = packet_path.parent
    target_dir = out_dir.resolve() if out_dir else packet_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    html_path = render_html_path(target_dir)
    meta_path = render_meta_path(target_dir)
    resolved_folio_render_path = folio_render_path(target_dir)
    packet_workspace_render = target_dir == packet_dir
    image_href_value = image_href or relpath(target_dir, Path(packet.source_image_path))
    resolved_book_title = book_title or packet.doc_id.replace("_", " ")

    html, folio_render = render_folio_html(
        packet=packet,
        image_href=image_href_value,
        book_title=resolved_book_title,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        include_cover=include_cover,
    )
    html_path.write_text(html, encoding="utf-8")
    resolved_folio_render_path.write_text(folio_render.model_dump_json(indent=2), encoding="utf-8")

    meta = {
        "rendered_at": utc_now(),
        "packet_path": str(packet_path),
        "html_path": str(html_path),
        "folio_render_path": str(resolved_folio_render_path),
        "source_image_path": packet.source_image_path,
        "image_href": image_href_value,
        "book_title": resolved_book_title,
        "previous_href": prev_href,
        "next_href": next_href,
        "home_href": home_href,
        "include_cover": include_cover,
        "packet_workspace_render": packet_workspace_render,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketHtmlArtifact(
        packet_path=packet_path,
        html_path=html_path,
        folio_render_path=resolved_folio_render_path,
        meta_path=meta_path,
    )
