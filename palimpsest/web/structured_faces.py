from __future__ import annotations

from html import escape
import re

from palimpsest.models.folio_render import FolioRender


def render_lacuna(text: str) -> str:
    return re.sub(
        r"\[/{2,}\]",
        '<span class="lacuna">&thinsp;[////]&thinsp;</span>',
        escape(text),
    )


def render_translation_inline(text: str) -> str:
    html = escape(text)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", html)
    html = re.sub(
        r"\[([^\]]*(?:unclear|uncertain|continues)[^\]]*)\]",
        r'<span class="uncertain">[\1]</span>',
        html,
        flags=re.IGNORECASE,
    )
    # Some stored translations still contain mojibake for an em dash.
    html = html.replace("\u2014", "&mdash;")
    return html


def render_structured_witness_face(folio: FolioRender) -> str:
    wc = folio.spread.content.witness_content
    if not wc:
        return ""

    parts: list[str] = []
    parts.append('<div class="face face-witness">')
    parts.append('  <div class="page-text-inner">')
    parts.append('    <div class="page-header">')
    parts.append(f'      <div class="page-header-label">{escape(folio.spread.content.header_label)}</div>')
    parts.append(f'      <div class="page-header-title">{escape(folio.spread.content.header_title)}</div>')
    parts.append('    </div>')

    for col in wc.columns:
        region_attrs = ""
        if col.region_id:
            region_attrs += f' data-region-id="{escape(col.region_id)}"'
        if col.unit_id:
            region_attrs += f' data-unit-id="{escape(col.unit_id)}"'
        parts.append(f'    <section class="witness-unit"{region_attrs}>')
        parts.append('    <div class="column-header">')
        parts.append(f'      <div class="column-header-chinese">{escape(col.header_zh)}</div>')
        if col.header_en:
            parts.append(f'      <div class="column-header-english">{escape(col.header_en)}</div>')
        parts.append('      <div class="column-rule"></div>')
        parts.append('    </div>')

        for pair in col.pairs:
            source_html = render_lacuna(pair.source) if pair.source else ""
            trans_html = render_translation_inline(pair.translation) if pair.translation else ""
            pair_region_attrs = region_attrs
            if pair.region_id:
                pair_region_attrs = f' data-region-id="{escape(pair.region_id)}"'
                if pair.unit_id:
                    pair_region_attrs += f' data-unit-id="{escape(pair.unit_id)}"'
            parts.append(f'    <div class="pair"{pair_region_attrs}>')
            if source_html:
                parts.append(f'      <div class="pair-source">{source_html}</div>')
            if trans_html:
                parts.append(f'      <div class="pair-translation">{trans_html}</div>')
            parts.append('    </div>')
        parts.append('    </section>')

    if wc.marginalia:
        for marg in wc.marginalia:
            parts.append('    <div class="marginalia-section">')
            label = f"{escape(marg.script)} Marginalia &middot; {escape(marg.position)}"
            parts.append(f'      <div class="marginalia-label">{label}</div>')
            parts.append(f'      <div class="marginalia-text">{escape(marg.text)}</div>')
            if marg.note:
                parts.append(f'      <div class="marginalia-note">{escape(marg.note)}</div>')
            parts.append('    </div>')

    parts.append('  </div>')
    parts.append('</div>')
    return "\n".join(parts)


def render_structured_interpretation_face(folio: FolioRender) -> str:
    ic = folio.spread.interpretation.interpretation_content
    if not ic:
        return ""

    parts: list[str] = []
    parts.append('<div class="face face-interp">')
    parts.append('  <div class="page-text-inner">')
    parts.append('    <div class="page-header page-header-dark">')
    parts.append(f'      <div class="page-header-label">{escape(folio.spread.interpretation.header_label)}</div>')
    parts.append(f'      <div class="page-header-title">{escape(folio.spread.interpretation.header_title)}</div>')
    parts.append('    </div>')

    for block in ic.interpretations:
        parts.append(f'    <div class="interpretation-label">{escape(block.title)}</div>')
        for para in block.paragraphs:
            parts.append(f'    <p class="interpretation-text">{render_translation_inline(para)}</p>')

    if ic.terms:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Key Terms</div>')
        parts.append('      <div class="term-grid">')
        for term in ic.terms:
            rom = f" <em>{escape(term.romanization)}</em>" if term.romanization else ""
            parts.append(
                f'        <div class="term"><span class="term-zh">{escape(term.zh)}</span>{rom} &mdash; {escape(term.gloss)}</div>'
            )
        parts.append('      </div>')
        parts.append('    </div>')

    if ic.notes:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Notes</div>')
        for block in ic.notes:
            parts.append('      <div style="margin-bottom: 0.8rem;">')
            parts.append(
                f'        <div style="font-size: 0.6rem; letter-spacing: 0.15em; text-transform: uppercase; color: var(--faded); margin-bottom: 0.3rem;">{escape(block.title)}</div>'
            )
            for item in block.items:
                parts.append(
                    f'        <p style="font-size: 0.68rem; line-height: 1.7; color: var(--faded-light); margin-bottom: 0.35rem;">{render_translation_inline(item)}</p>'
                )
            parts.append('      </div>')
        parts.append('    </div>')

    if ic.questions:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Open Questions</div>')
        for q in ic.questions:
            parts.append(
                f'      <p style="font-size: 0.68rem; line-height: 1.7; color: var(--faded-light); margin-bottom: 0.5rem;">{render_translation_inline(q.text)}</p>'
            )
        parts.append('    </div>')

    parts.append('    <div class="colophon"><div class="colophon-text">Palimpsest Edition</div></div>')
    parts.append('  </div>')
    parts.append('</div>')
    return "\n".join(parts)


__all__ = [
    "render_lacuna",
    "render_structured_interpretation_face",
    "render_structured_witness_face",
    "render_translation_inline",
]
