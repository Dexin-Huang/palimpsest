from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path

from palimpsest.models import FolioRender, PagePacket
from palimpsest.packets.scholar import repair_packet_json
from palimpsest.web import (
    MarkdownDocument,
    build_folio_render as web_build_folio_render,
    group_document_sections as web_group_document_sections,
    groups_to_template_sections as web_groups_to_template_sections,
    parse_markdown_document as web_parse_markdown_document,
    render_content_piece as web_render_content_piece,
    render_cover_piece as web_render_cover_piece,
    render_interpretation_piece as web_render_interpretation_piece,
    render_spread_piece as web_render_spread_piece,
    site_css as web_site_css,
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
    cover_piece = web_render_cover_piece(folio)
    content_piece = web_render_content_piece(folio)
    interpretation_piece = web_render_interpretation_piece(folio)
    spread_piece = web_render_spread_piece(
        folio,
        content_piece=content_piece,
        interpretation_piece=interpretation_piece,
    )
    folio_links = "\n".join(
        link
        for link in [
            f'<a class="folio-link" href="{escape(folio.navigation.home_href)}">Contents</a>' if folio.navigation.home_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.prev_href)}">&larr; Previous Folio</a>' if folio.navigation.prev_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.next_href)}">Next Folio &rarr;</a>' if folio.navigation.next_href else "",
        ]
        if link
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>
{web_site_css()}
</style>
</head>
<body>
<div class="book">
  {f'<div class="page cover active" data-page="0">{cover_piece}</div>' if include_cover else ''}
  <div class="page{' active' if not include_cover else ''}" data-page="1">
    {spread_piece}
  </div>
</div>
<div class="folio-links">
  {folio_links}
</div>

<script>
  (function() {{
    const cover = document.querySelector('.cover');
    const spread = document.querySelector('[data-page="1"]');
    const panel = document.getElementById('right-panel');
    const symbol = document.getElementById('flip-symbol');
    const symbolSvg = symbol ? symbol.querySelector('svg') : null;
    let rotation = 0;

    function openSpread() {{
      if (!cover || !spread) {{
        return;
      }}
      cover.classList.remove('active');
      spread.classList.add('active');
    }}

    if (cover) {{
      cover.addEventListener('click', openSpread);
    }}

    if (symbol && panel) {{
      symbol.addEventListener('click', (event) => {{
        event.stopPropagation();
        panel.classList.toggle('flipped');
        rotation += 180;
        if (symbolSvg) {{
          symbolSvg.style.transform = `rotate(${{rotation}}deg)`;
        }}
        const face = panel.querySelector(panel.classList.contains('flipped') ? '.face-interp' : '.face-witness');
        if (face) {{
          face.scrollTop = 0;
        }}
      }});
    }}

    const linkedNodes = Array.from(document.querySelectorAll('[data-region-id]'));
    function setLinkedActive(regionId, active) {{
      if (!regionId) {{
        return;
      }}
      linkedNodes.forEach((node) => {{
        if (node.dataset.regionId === regionId) {{
          node.classList.toggle('is-linked-active', active);
        }}
      }});
    }}

    linkedNodes.forEach((node) => {{
      node.addEventListener('mouseenter', () => setLinkedActive(node.dataset.regionId, true));
      node.addEventListener('mouseleave', () => setLinkedActive(node.dataset.regionId, false));
      node.addEventListener('focus', () => setLinkedActive(node.dataset.regionId, true));
      node.addEventListener('blur', () => setLinkedActive(node.dataset.regionId, false));
    }});

    window.addEventListener('keydown', (event) => {{
      if (cover && cover.classList.contains('active') && (event.key === 'ArrowRight' || event.key === ' ')) {{
        event.preventDefault();
        openSpread();
        return;
      }}
      if (event.key === 'ArrowLeft' && spread && spread.classList.contains('active') && {str(bool(prev_href)).lower()}) {{
        window.location.href = {json.dumps(prev_href or "")};
      }} else if (event.key === 'ArrowRight' && spread && spread.classList.contains('active') && {str(bool(next_href)).lower()}) {{
        window.location.href = {json.dumps(next_href or "")};
      }}
    }});
  }})();
</script>
</body>
</html>
"""
    return html, folio


def render_packet_folio_html(
    packet_path: Path,
    *,
    out_dir: Path | None = None,
    book_title: str | None = None,
    image_href: str | None = None,
    prev_href: str | None = None,
    next_href: str | None = None,
    home_href: str | None = None,
    include_cover: bool = True,
) -> RenderedPacketHtmlArtifact:
    packet = repair_packet_json(Path(packet_path))
    packet_path = Path(packet_path).resolve()
    packet_dir = packet_path.parent
    target_dir = out_dir.resolve() if out_dir else packet_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    html_path = target_dir / "index.html"
    meta_path = target_dir / "render_meta.json"
    folio_render_path = target_dir / "render.json"
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
    folio_render_path.write_text(folio_render.model_dump_json(indent=2), encoding="utf-8")

    packet.files["edition_html"].status = "draft"
    packet.files["edition_html"].note = "Rendered HTML folio edition"
    if "folio_render" in packet.files:
        packet.files["folio_render"].status = "draft"
        packet.files["folio_render"].note = "Structured folio.render JSON artifact"
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    meta = {
        "rendered_at": utc_now(),
        "packet_path": str(packet_path),
        "html_path": str(html_path),
        "folio_render_path": str(folio_render_path),
        "source_image_path": packet.source_image_path,
        "image_href": image_href_value,
        "book_title": resolved_book_title,
        "previous_href": prev_href,
        "next_href": next_href,
        "home_href": home_href,
        "include_cover": include_cover,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketHtmlArtifact(
        packet_path=packet_path,
        html_path=html_path,
        folio_render_path=folio_render_path,
        meta_path=meta_path,
    )
