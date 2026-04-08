from __future__ import annotations

from html import escape
import json

from palimpsest.models.folio_render import FolioRender

from .folio_fragments import (
    render_content_piece,
    render_cover_piece,
    render_interpretation_piece,
    render_spread_piece,
)
from .theme import html_shell


def render_folio_navigation_links(folio: FolioRender) -> str:
    return "\n".join(
        link
        for link in [
            f'<a class="folio-link" href="{escape(folio.navigation.home_href)}">Contents</a>' if folio.navigation.home_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.prev_href)}">&larr; Previous Folio</a>' if folio.navigation.prev_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.next_href)}">Next Folio &rarr;</a>' if folio.navigation.next_href else "",
        ]
        if link
    )


def _render_folio_page_script(*, prev_href: str | None, next_href: str | None) -> str:
    return "\n".join(
        [
            "<script>",
            "  (function() {",
            "    const cover = document.querySelector('.cover');",
            "    const spread = document.querySelector('[data-page=\"1\"]');",
            "    const panel = document.getElementById('right-panel');",
            "    const symbol = document.getElementById('flip-symbol');",
            "    const symbolSvg = symbol ? symbol.querySelector('svg') : null;",
            "    let rotation = 0;",
            "",
            "    function openSpread() {",
            "      if (!cover || !spread) {",
            "        return;",
            "      }",
            "      cover.classList.remove('active');",
            "      spread.classList.add('active');",
            "    }",
            "",
            "    if (cover) {",
            "      cover.addEventListener('click', openSpread);",
            "    }",
            "",
            "    if (symbol && panel) {",
            "      symbol.addEventListener('click', (event) => {",
            "        event.stopPropagation();",
            "        panel.classList.toggle('flipped');",
            "        rotation += 180;",
            "        if (symbolSvg) {",
            "          symbolSvg.style.transform = `rotate(${rotation}deg)`;",
            "        }",
            "        const face = panel.querySelector(panel.classList.contains('flipped') ? '.face-interp' : '.face-witness');",
            "        if (face) {",
            "          face.scrollTop = 0;",
            "        }",
            "      });",
            "    }",
            "",
            "    const linkedNodes = Array.from(document.querySelectorAll('[data-region-id]'));",
            "    function setLinkedActive(regionId, active) {",
            "      if (!regionId) {",
            "        return;",
            "      }",
            "      linkedNodes.forEach((node) => {",
            "        if (node.dataset.regionId === regionId) {",
            "          node.classList.toggle('is-linked-active', active);",
            "        }",
            "      });",
            "    }",
            "",
            "    linkedNodes.forEach((node) => {",
            "      node.addEventListener('mouseenter', () => setLinkedActive(node.dataset.regionId, true));",
            "      node.addEventListener('mouseleave', () => setLinkedActive(node.dataset.regionId, false));",
            "      node.addEventListener('focus', () => setLinkedActive(node.dataset.regionId, true));",
            "      node.addEventListener('blur', () => setLinkedActive(node.dataset.regionId, false));",
            "    });",
            "",
            "    window.addEventListener('keydown', (event) => {",
            "      if (cover && cover.classList.contains('active') && (event.key === 'ArrowRight' || event.key === ' ')) {",
            "        event.preventDefault();",
            "        openSpread();",
            "        return;",
            "      }",
            f"      if (event.key === 'ArrowLeft' && spread && spread.classList.contains('active') && {str(bool(prev_href)).lower()}) {{",
            f"        window.location.href = {json.dumps(prev_href or '')};",
            f"      }} else if (event.key === 'ArrowRight' && spread && spread.classList.contains('active') && {str(bool(next_href)).lower()}) {{",
            f"        window.location.href = {json.dumps(next_href or '')};",
            "      }",
            "    });",
            "  })();",
            "</script>",
        ]
    )


def render_folio_page_html(
    *,
    title: str,
    folio: FolioRender,
    cover_piece: str,
    spread_piece: str,
    include_cover: bool = True,
) -> str:
    body = "\n".join(
        [
            '<div class="book">',
            f"  {'<div class=\"page cover active\" data-page=\"0\">' + cover_piece + '</div>' if include_cover else ''}",
            f"  <div class=\"page{' active' if not include_cover else ''}\" data-page=\"1\">",
            f"    {spread_piece}",
            "  </div>",
            "</div>",
            '<div class="folio-links">',
            f"  {render_folio_navigation_links(folio)}",
            "</div>",
            _render_folio_page_script(
                prev_href=folio.navigation.prev_href,
                next_href=folio.navigation.next_href,
            ),
        ]
    )
    return html_shell(title=title, body=body)


def render_folio_document_html(
    *,
    title: str,
    folio: FolioRender,
    include_cover: bool = True,
) -> str:
    cover_piece = render_cover_piece(folio)
    content_piece = render_content_piece(folio)
    interpretation_piece = render_interpretation_piece(folio)
    spread_piece = render_spread_piece(
        folio,
        content_piece=content_piece,
        interpretation_piece=interpretation_piece,
    )
    return render_folio_page_html(
        title=title,
        folio=folio,
        cover_piece=cover_piece,
        spread_piece=spread_piece,
        include_cover=include_cover,
    )


__all__ = [
    "render_folio_document_html",
    "render_folio_navigation_links",
    "render_folio_page_html",
]
