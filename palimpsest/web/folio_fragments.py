from __future__ import annotations

from html import escape

from palimpsest.models.folio_render import (
    FolioRender,
    FolioRenderCover,
    FolioRenderImagePanel,
    FolioRenderImageRegion,
    FolioRenderNavigation,
    FolioRenderSection,
    FolioRenderSpread,
    FolioRenderTextPanel,
    InterpretationContent,
    WitnessContent,
)
from palimpsest.models.packet import PagePacket

from .markup import FolioTemplateSection, render_template_sections as web_render_template_sections
from .structured_faces import render_structured_interpretation_face, render_structured_witness_face


def render_cover_piece(folio: FolioRender) -> str:
    return "\n".join(
        [
            f'<div class="cover-label">{escape(folio.cover.label)}</div>',
            f'<h1 class="cover-title">{escape(folio.cover.title)}</h1>',
            f'<div class="cover-subtitle">{escape(folio.cover.subtitle)}</div>',
            '<div class="cover-rule"></div>',
            f'<div class="cover-nav-hint"><span class="arrow">&rarr;</span> {escape(folio.cover.nav_hint or "")}</div>',
        ]
    )


def render_content_piece(folio: FolioRender) -> str:
    if folio.spread.content.witness_content:
        return render_structured_witness_face(folio)

    sections_html = web_render_template_sections(
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


def render_interpretation_piece(folio: FolioRender) -> str:
    if folio.spread.interpretation.interpretation_content:
        return render_structured_interpretation_face(folio)

    sections_html = web_render_template_sections(
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


def render_spread_piece(folio: FolioRender, *, content_piece: str, interpretation_piece: str) -> str:
    region_overlays = "\n".join(
        (
            f'<div class="image-region image-region--{escape(region.role)}" '
            f'data-region-id="{escape(region.region_id)}" '
            f'data-unit-id="{escape(region.unit_id or "")}" '
            f'title="{escape(region.label)}" '
            f'style="left:{region.bbox_norm[0] * 100:.3f}%;top:{region.bbox_norm[1] * 100:.3f}%;'
            f'width:{region.bbox_norm[2] * 100:.3f}%;height:{region.bbox_norm[3] * 100:.3f}%;">'
            f'<span class="image-region-label">{escape(region.label)}</span>'
            f'</div>'
        )
        for region in folio.spread.image.regions
    )
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
            '      <div class="image-overlay">',
            region_overlays,
            '      </div>',
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


def build_folio_render(
    *,
    packet: PagePacket,
    book_title: str,
    image_href: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
    content_sections: list[FolioTemplateSection],
    interpretation_sections: list[FolioTemplateSection],
    witness_content: WitnessContent | None = None,
    interpretation_content: InterpretationContent | None = None,
    image_regions: list[FolioRenderImageRegion] | None = None,
    page_label: str,
    created_at: str,
) -> FolioRender:
    content_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in content_sections
    ]
    interpretation_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in interpretation_sections
    ]

    return FolioRender(
        created_at=created_at,
        doc_id=packet.doc_id,
        page_id=packet.page_id,
        page_label=page_label,
        book_title=book_title,
        page_unit=packet.page_unit,
        source_image_path=packet.source_image_path,
        cover=FolioRenderCover(
            label="Palimpsest Edition",
            title=page_label,
            subtitle=book_title,
            nav_hint="Press arrow keys or click to open the folio",
        ),
        spread=FolioRenderSpread(
            image=FolioRenderImagePanel(
                folio_label=page_label,
                source_label=book_title,
                image_path=image_href,
                caption="Source witness / raw folio image",
                regions=image_regions or [],
            ),
            content=FolioRenderTextPanel(
                header_label="Witness & Translation",
                header_title=page_label,
                sections=content_render_sections,
                witness_content=witness_content,
            ),
            interpretation=FolioRenderTextPanel(
                header_label="Interpretation & Apparatus",
                header_title=page_label,
                sections=interpretation_render_sections,
                interpretation_content=interpretation_content,
            ),
        ),
        navigation=FolioRenderNavigation(
            home_href=home_href,
            prev_href=prev_href,
            next_href=next_href,
        ),
    )


__all__ = [
    "build_folio_render",
    "render_content_piece",
    "render_cover_piece",
    "render_interpretation_piece",
    "render_spread_piece",
]
