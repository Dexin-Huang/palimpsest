from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import shutil

from palimpsest.models import (
    FolioRender,
    FolioRenderCover,
    FolioRenderImagePanel,
    FolioRenderNavigation,
    FolioRenderSection,
    FolioRenderSpread,
    FolioRenderTextPanel,
    PagePacket,
)
from palimpsest.packet_scholar import repair_packet_json


@dataclass
class MarkdownSection:
    level: int
    title: str
    body: str


@dataclass
class MarkdownDocument:
    title: str | None
    preamble: str
    sections: list[MarkdownSection]


@dataclass
class RenderedPacketHtmlArtifact:
    packet_path: Path
    html_path: Path
    folio_render_path: Path
    meta_path: Path


@dataclass
class RenderedPacketSiteArtifact:
    doc_id: str
    index_path: Path
    folio_paths: list[Path]
    meta_path: Path


@dataclass
class FolioTemplateSection:
    kind: str
    title: str
    body_html: str
    wide: bool = False


@dataclass
class MarkdownSectionGroup:
    title: str
    level: int
    body: str
    children: list[MarkdownSection]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relpath(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), start=from_dir.resolve())).as_posix()


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "section"


def _page_sort_key(page_id: str) -> tuple[int, int, int, str]:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        side = 0 if match.group(2).lower() == "r" else 1
        return (0, int(match.group(1)), side, page_id)
    match = re.match(r"^page_(\d+)$", page_id, re.IGNORECASE)
    if match:
        return (1, int(match.group(1)), 0, page_id)
    return (2, 0, 0, page_id)


def _display_page_id(page_id: str) -> str:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        return f"Folio {int(match.group(1))}{match.group(2).lower()}"
    return page_id.replace("_", " ")


def _read_text(path: str | Path | None) -> str:
    if not path:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8")


def _parse_markdown_document(text: str) -> MarkdownDocument:
    lines = text.splitlines()
    title: str | None = None
    preamble_lines: list[str] = []
    sections: list[MarkdownSection] = []
    current_title: str | None = None
    current_level: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_level, current_lines
        if current_title is not None and current_level is not None:
            sections.append(
                MarkdownSection(
                    level=current_level,
                    title=current_title.strip(),
                    body="\n".join(current_lines).strip(),
                )
            )
        current_title = None
        current_level = None
        current_lines = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            if level == 1 and title is None and current_title is None and not preamble_lines and not sections:
                title = heading
                continue
            flush()
            current_title = heading
            current_level = level
            continue
        if current_title is None:
            preamble_lines.append(line)
        else:
            current_lines.append(line)
    flush()

    return MarkdownDocument(
        title=title,
        preamble="\n".join(preamble_lines).strip(),
        sections=sections,
    )


def _render_inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _render_markdown_body(text: str, *, preserve_linebreaks: bool = False) -> str:
    lines = text.strip().splitlines()
    html_parts: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped == "---":
            html_parts.append("<hr>")
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            html_parts.append(f"<pre>{escape(chr(10).join(code_lines))}</pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            html_parts.append(f"<h{level}>{_render_inline_markdown(heading.group(2).strip())}</h{level}>")
            i += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines):
                item_line = lines[i].strip()
                if not re.match(r"^[-*]\s+", item_line):
                    break
                items.append(f"<li>{_render_inline_markdown(re.sub(r'^[-*]\s+', '', item_line))}</li>")
                i += 1
            html_parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines):
                item_line = lines[i].strip()
                if not re.match(r"^\d+\.\s+", item_line):
                    break
                items.append(f"<li>{_render_inline_markdown(re.sub(r'^\d+\.\s+', '', item_line))}</li>")
                i += 1
            html_parts.append("<ol>" + "".join(items) + "</ol>")
            continue

        paragraph_lines = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            nxt_stripped = nxt.strip()
            if not nxt_stripped or nxt_stripped == "---" or nxt_stripped.startswith("```"):
                break
            if re.match(r"^(#{1,6})\s+", nxt_stripped) or re.match(r"^[-*]\s+", nxt_stripped) or re.match(r"^\d+\.\s+", nxt_stripped):
                break
            paragraph_lines.append(nxt)
            i += 1

        rendered = "<br>".join(_render_inline_markdown(item) for item in paragraph_lines) if preserve_linebreaks else _render_inline_markdown(" ".join(item.strip() for item in paragraph_lines))
        html_parts.append(f"<p>{rendered}</p>")

    return "\n".join(html_parts)


def _group_document_sections(doc: MarkdownDocument) -> list[MarkdownSectionGroup]:
    if not doc.sections:
        return []
    top_level = min(section.level for section in doc.sections)
    groups: list[MarkdownSectionGroup] = []
    current: MarkdownSectionGroup | None = None
    for section in doc.sections:
        if section.level == top_level:
            current = MarkdownSectionGroup(
                title=section.title,
                level=section.level,
                body=section.body,
                children=[],
            )
            groups.append(current)
            continue
        if current is None:
            current = MarkdownSectionGroup(
                title=section.title,
                level=section.level,
                body="",
                children=[],
            )
            groups.append(current)
        else:
            current.children.append(section)
    return groups


def _render_section_group_body(group: MarkdownSectionGroup, *, preserve_linebreaks: bool = False) -> str:
    parts: list[str] = []
    if group.body.strip():
        parts.append(_render_markdown_body(group.body, preserve_linebreaks=preserve_linebreaks))
    for child in group.children:
        child_body = _render_markdown_body(child.body, preserve_linebreaks=preserve_linebreaks)
        parts.append(
            "\n".join(
                [
                    '<section class="subsection-card">',
                    f'  <div class="subsection-card-title">{escape(child.title)}</div>',
                    f'  <div class="subsection-card-body">{child_body}</div>',
                    "</section>",
                ]
            )
        )
    return "\n".join(part for part in parts if part.strip())


def _render_template_sections(
    sections: list[FolioTemplateSection],
    *,
    article_class: str,
    title_class: str,
) -> str:
    rendered: list[str] = []
    for section in sections:
        wide_class = f" {article_class}-wide" if section.wide else ""
        kind_slug = _slugify(section.kind)
        rendered.append(
            "\n".join(
                [
                    f'<article class="{article_class}{wide_class} {article_class}--{kind_slug}" data-kind="{escape(section.kind)}">',
                    f'  <div class="{title_class}">{escape(section.title)}</div>',
                    f"  {section.body_html}",
                    "</article>",
                ]
            )
        )
    return "\n".join(rendered)


def _wide_section(kind: str, title: str) -> bool:
    lowered = title.lower()
    return kind in {"interpretation", "questions"} or lowered in {
        "what this page is doing",
        "open questions",
        "interpretation",
    }


def _group_to_template_section(
    group: MarkdownSectionGroup,
    *,
    kind: str,
    preserve_linebreaks: bool = False,
) -> FolioTemplateSection:
    body_html = _render_section_group_body(group, preserve_linebreaks=preserve_linebreaks)
    return FolioTemplateSection(
        kind=kind,
        title=group.title,
        body_html=body_html or '<p class="empty-note">No content yet.</p>',
        wide=_wide_section(kind, group.title),
    )


def _groups_to_template_sections(
    groups: list[MarkdownSectionGroup],
    *,
    kind: str,
    preserve_linebreaks: bool = False,
) -> list[FolioTemplateSection]:
    return [
        _group_to_template_section(
            group,
            kind=kind,
            preserve_linebreaks=preserve_linebreaks,
        )
        for group in groups
    ]


def _build_folio_render(
    *,
    packet: PagePacket,
    book_title: str,
    image_href: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
    content_sections: list[FolioTemplateSection],
    interpretation_sections: list[FolioTemplateSection],
) -> FolioRender:
    display_page = _display_page_id(packet.page_id)
    content_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in content_sections
    ]
    interpretation_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in interpretation_sections
    ]

    return FolioRender(
        created_at=_utc_now(),
        doc_id=packet.doc_id,
        page_id=packet.page_id,
        page_label=display_page,
        book_title=book_title,
        page_unit=packet.page_unit,
        source_image_path=packet.source_image_path,
        cover=FolioRenderCover(
            label="Palimpsest Edition",
            title=display_page,
            subtitle=book_title,
            nav_hint="Press arrow keys or click to open the folio",
        ),
        spread=FolioRenderSpread(
            image=FolioRenderImagePanel(
                folio_label=display_page,
                source_label=book_title,
                image_path=image_href,
                caption="Source witness / raw folio image",
            ),
            content=FolioRenderTextPanel(
                header_label="Witness & Translation",
                header_title=display_page,
                sections=content_render_sections,
            ),
            interpretation=FolioRenderTextPanel(
                header_label="Interpretation & Apparatus",
                header_title=display_page,
                sections=interpretation_render_sections,
            ),
        ),
        navigation=FolioRenderNavigation(
            home_href=home_href,
            prev_href=prev_href,
            next_href=next_href,
        ),
    )


def _render_cover_piece(folio: FolioRender) -> str:
    return "\n".join(
        [
            f'<div class="cover-label">{escape(folio.cover.label)}</div>',
            f'<h1 class="cover-title">{escape(folio.cover.title)}</h1>',
            f'<div class="cover-subtitle">{escape(folio.cover.subtitle)}</div>',
            '<div class="cover-rule"></div>',
            f'<div class="cover-nav-hint"><span class="arrow">&rarr;</span> {escape(folio.cover.nav_hint or "")}</div>',
        ]
    )


def _render_content_piece(folio: FolioRender) -> str:
    sections_html = _render_template_sections(
        [
            FolioTemplateSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
            for section in folio.spread.content.sections
        ],
        article_class="content-block",
        title_class="content-block-title",
    )
    return "\n".join(
        [
            '<div class="face face-witness">',
            '  <div class="page-text-inner">',
            '    <div class="page-header">',
            f'      <div class="page-header-label">{escape(folio.spread.content.header_label)}</div>',
            f'      <div class="page-header-title">{escape(folio.spread.content.header_title)}</div>',
            '    </div>',
            sections_html,
            '  </div>',
            '</div>',
        ]
    )


def _render_interpretation_piece(
    folio: FolioRender,
) -> str:
    sections_html = _render_template_sections(
        [
            FolioTemplateSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
            for section in folio.spread.interpretation.sections
        ],
        article_class="apparatus-block",
        title_class="apparatus-block-title",
    )
    return "\n".join(
        [
            '<div class="face face-interp">',
            '  <div class="page-text-inner">',
            '    <div class="page-header page-header-dark">',
            f'      <div class="page-header-label">{escape(folio.spread.interpretation.header_label)}</div>',
            f'      <div class="page-header-title">{escape(folio.spread.interpretation.header_title)}</div>',
            '    </div>',
            sections_html,
            '    <div class="colophon"><div class="colophon-text">Palimpsest Edition</div></div>',
            '  </div>',
            '</div>',
        ]
    )


def _render_spread_piece(folio: FolioRender, *, content_piece: str, interpretation_piece: str) -> str:
    return "\n".join(
        [
            '<div class="spread">',
            '  <div class="page-image">',
            '    <div class="image-header">',
            f'      <span class="image-header-folio">{escape(folio.spread.image.folio_label)}</span>',
            f'      <span class="image-header-source">{escape(folio.spread.image.source_label)}</span>',
            '    </div>',
            '    <div class="image-frame">',
            f'      <img src="{escape(folio.spread.image.image_path)}" alt="{escape(folio.spread.image.folio_label)} source image">',
            '    </div>',
            f'    <span class="image-caption">{escape(folio.spread.image.caption)}</span>',
            '  </div>',
            '  <div class="right-panel" id="right-panel">',
            content_piece,
            interpretation_piece,
            '    <button class="flip-symbol" id="flip-symbol" type="button" title="Toggle witness / interpretation" aria-label="Toggle witness / interpretation">',
            '      <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">',
            '        <circle cx="50" cy="50" r="49" fill="none" stroke="var(--ink)" stroke-width="0.5" opacity="0.4"/>',
            '        <circle cx="50" cy="50" r="47" fill="none" stroke="var(--ink)" stroke-width="1.2"/>',
            '        <circle cx="50" cy="50" r="45" fill="var(--parchment)"/>',
            '        <path d="M 50,5 A 45,45 0 0 1 50,95 A 22.5,22.5 0 0 1 50,50 A 22.5,22.5 0 0 0 50,5 Z" fill="var(--ink)"/>',
            '        <circle cx="50" cy="72.5" r="6.5" fill="var(--ink)"/>',
            '        <circle cx="50" cy="27.5" r="6.5" fill="var(--parchment)"/>',
            '      </svg>',
            '    </button>',
            '  </div>',
            '</div>',
        ]
    )


def _pick_witness_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        if section.title.lower() == "layout notes":
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus


def _pick_translation_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        lowered = section.title.lower()
        if lowered in {"translation notes", "interpretive restraint"}:
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus


def _site_css() -> str:
    return """
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --parchment: #F0EBE0;
    --parchment-deep: #E8E1D2;
    --ink: #1A1714;
    --ink-soft: #3D3630;
    --faded: #8A7E6E;
    --faded-light: #B5AA96;
    --accent: #8A4B2A;
    --rule: #D0C4AE;
    --rule-light: #E0D8C8;
    --panel-bg: #141210;
    --panel-fg: #C8BFA8;
    --glow: rgba(138,75,42,0.08);
  }
  html { font-size: 16px; }
  body {
    margin: 0;
    background: var(--panel-bg);
    color: var(--ink);
    font-family: 'Noto Serif', 'Georgia', serif;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    height: 100vh;
    width: 100vw;
  }
  a { color: inherit; }
  .book {
    position: fixed;
    inset: 0;
  }
  .page {
    position: absolute;
    inset: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.5s ease;
  }
  .page.active {
    opacity: 1;
    pointer-events: auto;
  }
  .cover {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: var(--panel-bg);
    overflow: hidden;
    position: relative;
  }
  .cover::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 50% 40%, rgba(138,75,42,0.07) 0%, transparent 70%);
    pointer-events: none;
  }
  .cover-label {
    font-size: 0.65rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 1.5rem;
    opacity: 0;
    animation: fadeUp 1.2s 0.3s ease forwards;
  }
  .cover-title {
    font-size: 2.8rem;
    font-weight: 300;
    color: var(--parchment);
    letter-spacing: 0.04em;
    opacity: 0;
    animation: fadeUp 1.2s 0.6s ease forwards;
  }
  .cover-subtitle {
    font-size: 0.85rem;
    color: var(--faded);
    margin-top: 0.75rem;
    letter-spacing: 0.15em;
    opacity: 0;
    animation: fadeUp 1.2s 0.9s ease forwards;
  }
  .cover-rule {
    width: 60px;
    height: 1px;
    background: var(--accent);
    margin-top: 2rem;
    opacity: 0;
    animation: fadeUp 1.2s 1.1s ease forwards;
  }
  .cover-nav-hint {
    position: absolute;
    bottom: 2.5rem;
    font-size: 0.6rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded-light);
    opacity: 0;
    animation: fadeUp 1.2s 1.8s ease forwards, pulse 3s 3s ease-in-out infinite;
    margin-top: 1rem;
    display: flex;
    align-items: center;
    gap: 0.8em;
  }
  .cover-nav-hint .arrow {
    font-size: 0.8rem;
    color: var(--accent);
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }
  .spread-page {
    height: 100%;
  }
  .spread {
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: var(--parchment);
    height: 100%;
    position: relative;
  }
  .spread::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 3px;
    background: linear-gradient(to bottom, transparent 0%, var(--rule) 5%, var(--rule) 95%, transparent 100%);
    transform: translateX(-1.5px);
    z-index: 10;
    box-shadow: -4px 0 12px rgba(0,0,0,0.03), 4px 0 12px rgba(0,0,0,0.03);
  }
  .page-image {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.8rem 2rem;
    background: linear-gradient(160deg, var(--parchment-deep) 0%, var(--parchment) 40%, var(--parchment-deep) 100%);
    position: relative;
    overflow: hidden;
  }
  .page-image::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 55% 45%, var(--glow), transparent 65%);
    pointer-events: none;
  }
  .image-header {
    position: absolute;
    top: 1.2rem;
    left: 1.5rem;
    right: 1.5rem;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .image-header-folio {
    font-size: 0.6rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
  }
  .image-header-source {
    font-size: 0.55rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .image-frame {
    position: relative;
    max-width: 90%;
    max-height: 85vh;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06), 0 12px 32px rgba(0,0,0,0.08), 0 32px 72px rgba(0,0,0,0.05);
    line-height: 0;
    border-radius: 1px;
  }
  .image-frame img {
    display: block;
    width: 100%;
    height: auto;
    max-height: 85vh;
    object-fit: contain;
  }
  .image-frame::after {
    content: '';
    position: absolute;
    inset: 0;
    border: 1px solid rgba(0,0,0,0.05);
    pointer-events: none;
  }
  .image-caption {
    position: absolute;
    bottom: 1rem;
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .right-panel {
    position: relative;
    overflow: hidden;
  }
  .face {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 2rem 2.5rem 3rem 2rem;
    transition: opacity 0.4s ease, transform 0.4s ease;
    scrollbar-width: thin;
    scrollbar-color: var(--rule) transparent;
  }
  .face::-webkit-scrollbar { width: 5px; }
  .face::-webkit-scrollbar-track { background: transparent; }
  .face::-webkit-scrollbar-thumb { background: var(--rule); border-radius: 3px; }
  .face-witness {
    background: var(--parchment);
    box-shadow: inset 6px 0 16px -8px rgba(0,0,0,0.04);
  }
  .face-interp {
    background: var(--panel-bg);
    opacity: 0;
    pointer-events: none;
    transform: translateY(8px);
  }
  .right-panel.flipped .face-witness {
    opacity: 0;
    pointer-events: none;
    transform: translateY(-8px);
  }
  .right-panel.flipped .face-interp {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }
  .page-text-inner { max-width: 500px; }
  .page-header {
    margin-bottom: 1.6rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--rule);
  }
  .page-header-dark {
    border-color: rgba(200,191,168,0.15);
  }
  .page-header-label {
    font-size: 0.5rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.2rem;
  }
  .page-header-title {
    font-size: 1rem;
    font-weight: 400;
    color: var(--ink);
    letter-spacing: 0.02em;
  }
  .page-header-dark .page-header-title {
    color: var(--panel-fg);
  }
  .content-block {
    margin-top: 2rem;
  }
  .content-block:first-of-type {
    margin-top: 0;
  }
  .content-block-title,
  .apparatus-block-title {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.9rem;
  }
  .apparatus-block {
    margin-top: 1.8rem;
  }
  .apparatus-block:first-of-type {
    margin-top: 0;
  }
  .apparatus-block-wide {
    margin-top: 2rem;
  }
  .section-card {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px solid var(--rule-light);
  }
  .section-card:first-child {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .section-card-title {
    font-size: 1rem;
    color: var(--ink);
    margin-bottom: 0.5rem;
  }
  .section-card-body p, .apparatus-card-body p {
    margin: 0 0 0.7rem 0;
    line-height: 1.72;
    color: var(--ink-soft);
  }
  .section-card-body ul, .section-card-body ol,
  .apparatus-card-body ul, .apparatus-card-body ol {
    margin: 0 0 0.8rem 1.2rem;
    padding: 0;
    line-height: 1.68;
    color: var(--ink-soft);
  }
  .section-card-body li, .apparatus-card-body li { margin-bottom: 0.35rem; }
  .section-card-body pre, .apparatus-card-body pre {
    margin: 0.8rem 0;
    padding: 0.9rem 1rem;
    background: #efe7d8;
    border-left: 3px solid var(--accent);
    overflow-x: auto;
    font-size: 0.9rem;
    line-height: 1.55;
    color: var(--ink);
    white-space: pre-wrap;
  }
  .section-card-body hr, .apparatus-card-body hr {
    border: 0;
    height: 1px;
    background: var(--rule);
    margin: 1rem 0;
  }
  .subsection-card {
    margin-top: 0.9rem;
    padding: 0.8rem 0 0 0.9rem;
    border-left: 2px solid var(--rule);
  }
  .subsection-card-title {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.45rem;
  }
  .subsection-card-body p,
  .subsection-card-body ul,
  .subsection-card-body ol,
  .subsection-card-body li {
    color: var(--ink-soft);
    line-height: 1.68;
  }
  .content-block--witness .content-block-title {
    color: var(--accent);
  }
  .content-block--translation .content-block-title {
    color: var(--faded);
  }
  .apparatus-block--interpretation .apparatus-block-title,
  .apparatus-block--questions .apparatus-block-title {
    color: var(--parchment);
  }
  .section-card-body code, .apparatus-card-body code {
    background: rgba(20,18,16,0.06);
    padding: 0.08rem 0.28rem;
    border-radius: 2px;
    font-size: 0.95em;
  }
  .face-interp .section-card {
    border-top-color: rgba(200,191,168,0.12);
  }
  .face-interp .section-card-title {
    color: var(--panel-fg);
  }
  .face-interp .section-card-body p,
  .face-interp .section-card-body ul,
  .face-interp .section-card-body ol,
  .face-interp .section-card-body li {
    color: var(--faded-light);
  }
  .face-interp .subsection-card {
    border-left-color: rgba(200,191,168,0.16);
  }
  .face-interp .subsection-card-title {
    color: var(--parchment);
  }
  .face-interp .subsection-card-body p,
  .face-interp .subsection-card-body ul,
  .face-interp .subsection-card-body ol,
  .face-interp .subsection-card-body li {
    color: var(--faded-light);
  }
  .face-interp .section-card-body code {
    background: rgba(240,235,224,0.08);
  }
  .colophon {
    margin-top: 1.5rem;
    text-align: center;
    padding: 0.8rem 0 0.5rem;
  }
  .colophon-text {
    font-size: 0.45rem;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .flip-symbol {
    position: absolute;
    top: 1.1rem;
    right: 1.2rem;
    z-index: 20;
    width: 18px;
    height: 18px;
    cursor: pointer;
    user-select: none;
    opacity: 0.35;
    transition: opacity 0.3s ease, transform 0.4s ease;
    background: transparent;
    border: none;
    padding: 0;
  }
  .flip-symbol:hover {
    opacity: 0.7;
  }
  .flip-symbol svg {
    width: 100%;
    height: 100%;
    transition: transform 0.4s ease;
  }
  .folio-links {
    position: fixed;
    right: 1.2rem;
    bottom: 1.2rem;
    display: flex;
    gap: 0.7rem;
    z-index: 100;
  }
  .folio-link {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    text-decoration: none;
    padding: 0.65rem 0.8rem;
    border-radius: 999px;
    background: rgba(20,18,16,0.85);
    color: var(--faded-light);
    font-size: 0.62rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }
  .folio-link:hover {
    color: var(--parchment);
  }
  @media (max-width: 1120px) {
    .spread {
      grid-template-columns: 1fr;
    }
    .spread::after {
      display: none;
    }
    .page-image {
      height: 50vh;
      padding: 1.5rem;
    }
  }
  .empty-note {
    margin: 0;
    color: var(--faded-light);
    line-height: 1.7;
  }
"""


def _render_folio_html(
    *,
    packet: PagePacket,
    image_href: str,
    book_title: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
) -> tuple[str, FolioRender]:
    witness_doc = _parse_markdown_document(_read_text(packet.files.get("witness").path if packet.files.get("witness") else None))
    translation_doc = _parse_markdown_document(_read_text(packet.files.get("translation").path if packet.files.get("translation") else None))
    interpretation_doc = _parse_markdown_document(_read_text(packet.files.get("interpretation").path if packet.files.get("interpretation") else None))
    notes_doc = _parse_markdown_document(_read_text(packet.files.get("notes").path if packet.files.get("notes") else None))
    terms_doc = _parse_markdown_document(_read_text(packet.files.get("terms").path if packet.files.get("terms") else None))
    questions_doc = _parse_markdown_document(_read_text(packet.files.get("questions").path if packet.files.get("questions") else None))

    witness_main, witness_extra = _pick_witness_sections(witness_doc)
    translation_main, translation_extra = _pick_translation_sections(translation_doc)
    witness_main_doc = MarkdownDocument(title=witness_doc.title, preamble=witness_doc.preamble, sections=witness_main)
    witness_extra_doc = MarkdownDocument(title="Witness Notes", preamble="", sections=witness_extra)
    translation_main_doc = MarkdownDocument(title=translation_doc.title, preamble=translation_doc.preamble, sections=translation_main)
    translation_extra_doc = MarkdownDocument(title="Translation Notes", preamble="", sections=translation_extra)

    display_page = _display_page_id(packet.page_id)
    title = f"{display_page} - {book_title}"

    content_sections = [
        *_groups_to_template_sections(
            _group_document_sections(witness_main_doc),
            kind="witness",
            preserve_linebreaks=True,
        ),
        *_groups_to_template_sections(
            _group_document_sections(translation_main_doc),
            kind="translation",
            preserve_linebreaks=False,
        ),
    ]
    interpretation_sections = [
        *_groups_to_template_sections(
            _group_document_sections(interpretation_doc),
            kind="interpretation",
            preserve_linebreaks=False,
        ),
        *_groups_to_template_sections(
            _group_document_sections(notes_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *_groups_to_template_sections(
            _group_document_sections(witness_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *_groups_to_template_sections(
            _group_document_sections(translation_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *_groups_to_template_sections(
            _group_document_sections(terms_doc),
            kind="terms",
            preserve_linebreaks=False,
        ),
        *_groups_to_template_sections(
            _group_document_sections(questions_doc),
            kind="questions",
            preserve_linebreaks=False,
        ),
    ]

    folio = _build_folio_render(
        packet=packet,
        book_title=book_title,
        image_href=image_href,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        content_sections=content_sections,
        interpretation_sections=interpretation_sections,
    )
    cover_piece = _render_cover_piece(folio)
    content_piece = _render_content_piece(folio)
    interpretation_piece = _render_interpretation_piece(folio)
    spread_piece = _render_spread_piece(folio, content_piece=content_piece, interpretation_piece=interpretation_piece)
    folio_links = "\n".join(
        link
        for link in [
            f'<a class="folio-link" href="{escape(folio.navigation.home_href)}">Book Index</a>' if folio.navigation.home_href else "",
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
{_site_css()}
</style>
</head>
<body>
<div class="book">
  <div class="page cover active" data-page="0">
    {cover_piece}
  </div>
  <div class="page" data-page="1">
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
) -> RenderedPacketHtmlArtifact:
    packet = repair_packet_json(Path(packet_path))
    packet_path = Path(packet_path).resolve()
    packet_dir = packet_path.parent
    target_dir = out_dir.resolve() if out_dir else packet_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    html_path = target_dir / "edition_elegant.html"
    meta_path = target_dir / "edition_elegant_meta.json"
    folio_render_path = target_dir / "folio_render.json"
    image_href_value = image_href or _relpath(target_dir, Path(packet.source_image_path))
    resolved_book_title = book_title or packet.doc_id.replace("_", " ")

    html, folio_render = _render_folio_html(
        packet=packet,
        image_href=image_href_value,
        book_title=resolved_book_title,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
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
        "rendered_at": _utc_now(),
        "packet_path": str(packet_path),
        "html_path": str(html_path),
        "folio_render_path": str(folio_render_path),
        "source_image_path": packet.source_image_path,
        "image_href": image_href_value,
        "book_title": resolved_book_title,
        "previous_href": prev_href,
        "next_href": next_href,
        "home_href": home_href,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketHtmlArtifact(
        packet_path=packet_path,
        html_path=html_path,
        folio_render_path=folio_render_path,
        meta_path=meta_path,
    )


def build_packet_book_site(
    packet_paths: list[Path],
    *,
    out_dir: Path,
    title: str | None = None,
) -> RenderedPacketSiteArtifact:
    if not packet_paths:
        raise ValueError("At least one packet path is required")

    resolved_packets = sorted((Path(path).resolve() for path in packet_paths), key=lambda path: _page_sort_key(repair_packet_json(path).page_id))
    first_packet = repair_packet_json(resolved_packets[0])
    doc_id = first_packet.doc_id
    book_title = title or doc_id.replace("_", " ")

    out_dir = out_dir.resolve()
    folio_dir = out_dir / "folios"
    image_dir = out_dir / "assets" / "images"
    folio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    copied_images: dict[str, str] = {}
    folio_paths: list[Path] = []
    page_entries: list[dict[str, str]] = []

    packets = [repair_packet_json(path) for path in resolved_packets]
    for packet in packets:
        source_image = Path(packet.source_image_path).resolve()
        image_target = image_dir / source_image.name
        if not image_target.exists():
            shutil.copy2(source_image, image_target)
        copied_images[packet.page_id] = _relpath(folio_dir / packet.page_id, image_target)

    for index, (packet_path, packet) in enumerate(zip(resolved_packets, packets)):
        page_out_dir = folio_dir / packet.page_id
        page_out_dir.mkdir(parents=True, exist_ok=True)
        prev_href = f"../{packets[index - 1].page_id}/edition_elegant.html" if index > 0 else None
        next_href = f"../{packets[index + 1].page_id}/edition_elegant.html" if index < len(packets) - 1 else None
        home_href = "../index.html"
        artifact = render_packet_folio_html(
            packet_path,
            out_dir=page_out_dir,
            book_title=book_title,
            image_href=copied_images[packet.page_id],
            prev_href=prev_href,
            next_href=next_href,
            home_href=home_href,
        )
        folio_paths.append(artifact.html_path)
        page_entries.append(
            {
                "page_id": packet.page_id,
                "href": f"folios/{packet.page_id}/edition_elegant.html",
            }
        )

    index_html = "\n".join(
        [
            "<!DOCTYPE html>",
            "<html lang=\"en\">",
            "<head>",
            "  <meta charset=\"UTF-8\">",
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
            f"  <title>{escape(book_title)}</title>",
            "  <style>",
            _site_css(),
            "  body { overflow: auto; }",
            "  .index-shell { min-height: 100vh; padding: 4rem 2rem; display: flex; align-items: center; justify-content: center; }",
            "  .index-card { width: min(920px, 100%); background: rgba(240,235,224,0.96); color: #1a1714; padding: 2.4rem 2.6rem; box-shadow: 0 18px 70px rgba(0,0,0,0.18); }",
            "  .index-label { font-size: 0.7rem; letter-spacing: 0.28em; text-transform: uppercase; color: #8a4b2a; margin-bottom: 0.8rem; }",
            "  .index-title { font-size: clamp(2rem, 4vw, 3.8rem); margin-bottom: 0.8rem; }",
            "  .index-subtitle { color: #5c5044; line-height: 1.75; max-width: 52rem; }",
            "  .folio-list { margin-top: 2rem; display: grid; gap: 0.7rem; }",
            "  .folio-item { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #d9ccb5; padding-top: 0.8rem; }",
            "  .folio-item a { text-decoration: none; color: #1a1714; font-size: 1.1rem; }",
            "  .folio-item span { color: #8a4b2a; font-size: 0.74rem; letter-spacing: 0.18em; text-transform: uppercase; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <div class=\"index-shell\">",
            "    <div class=\"index-card\">",
            "      <div class=\"index-label\">Palimpsest Book</div>",
            f"      <div class=\"index-title\">{escape(book_title)}</div>",
            f"      <div class=\"index-subtitle\">Generated folio site for {escape(doc_id)}. Each folio keeps the raw source image fixed on the left, makes witness plus direct translation the default scrollable reading view, and moves interpretation into a right-side toggle.</div>",
            "      <div class=\"folio-list\">",
            *[
                f'        <div class="folio-item"><a href="{escape(entry["href"])}">{escape(entry["page_id"])}</a><span>Open Folio</span></div>'
                for entry in page_entries
            ],
            "      </div>",
            "    </div>",
            "  </div>",
            "</body>",
            "</html>",
        ]
    )
    index_path = out_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    meta_path = out_dir / "site_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "doc_id": doc_id,
        "title": book_title,
        "packet_paths": [str(path) for path in resolved_packets],
        "folio_paths": [str(path) for path in folio_paths],
        "index_path": str(index_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketSiteArtifact(
        doc_id=doc_id,
        index_path=index_path,
        folio_paths=folio_paths,
        meta_path=meta_path,
    )
