from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .common import display_page_id


@dataclass(frozen=True)
class BookSitePageEntry:
    page_id: str
    href: str


def book_site_page_css() -> str:
    return "\n".join(
        [
            "body { overflow: auto; }",
            ".contents-page { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 4rem 2rem; position: relative; }",
            ".contents-page::before { content: ''; position: absolute; inset: 0; background: radial-gradient(ellipse at 50% 30%, rgba(138,75,42,0.05) 0%, transparent 70%); pointer-events: none; }",
            ".contents-inner { width: min(480px, 100%); position: relative; }",
            ".contents-label { font-size: 0.65rem; letter-spacing: 0.35em; text-transform: uppercase; color: var(--faded); margin-bottom: 1.2rem; }",
            ".contents-title { font-size: 1.8rem; font-weight: 300; color: var(--parchment); letter-spacing: 0.04em; margin-bottom: 0.4rem; }",
            ".contents-subtitle { font-size: 0.75rem; color: var(--faded); letter-spacing: 0.12em; margin-bottom: 2.5rem; }",
            ".contents-rule { width: 36px; height: 1px; background: var(--accent); margin-bottom: 2rem; }",
            ".folio-list { display: grid; gap: 0; }",
            ".folio-item { border-top: 1px solid rgba(200,191,168,0.12); }",
            ".folio-item a { display: flex; justify-content: space-between; align-items: center; text-decoration: none; padding: 0.9rem 0; color: var(--panel-fg); transition: color 0.2s ease; }",
            ".folio-item a:hover { color: var(--parchment); }",
            ".folio-item .folio-name { font-size: 0.95rem; letter-spacing: 0.02em; }",
            ".folio-item .folio-arrow { font-size: 0.7rem; color: var(--faded); letter-spacing: 0.18em; text-transform: uppercase; transition: color 0.2s ease; }",
            ".folio-item a:hover .folio-arrow { color: var(--accent); }",
            ".folio-item:last-child { border-bottom: 1px solid rgba(200,191,168,0.12); }",
            ".contents-inner--centered { text-align: center; }",
            ".contents-rule--centered { margin-left: auto; margin-right: auto; }",
            ".contents-back-link { font-size: 0.7rem; letter-spacing: 0.2em; text-transform: uppercase; color: var(--faded); text-decoration: none; transition: color 0.2s ease; }",
            ".contents-back-link:hover { color: var(--parchment); }",
        ]
    )


def render_book_cover_page(*, book_title: str, folio_count: int) -> str:
    return "\n".join(
        [
            '<div class="book">',
            '  <div class="page cover active" data-page="0">',
            '    <div class="cover-label">Palimpsest Codex</div>',
            f'    <h1 class="cover-title">{escape(book_title)}</h1>',
            f'    <div class="cover-subtitle">Assembled set of {folio_count} folios</div>',
            '    <div class="cover-rule"></div>',
            '    <div class="cover-nav-hint"><span class="arrow">&rarr;</span> Press to open</div>',
            '  </div>',
            '</div>',
            '<script>',
            '  (function() {',
            '    const cover = document.querySelector(".cover");',
            '    function openContents() { window.location.href = "contents.html"; }',
            '    if (cover) { cover.addEventListener("click", openContents); }',
            '    window.addEventListener("keydown", (event) => {',
            '      if (event.key === "ArrowRight" || event.key === " ") {',
            '        event.preventDefault();',
            '        openContents();',
            '      }',
            '    });',
            '  })();',
            '</script>',
        ]
    )


def render_book_contents_page(*, book_title: str, page_entries: list[BookSitePageEntry]) -> str:
    return "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner">',
            '    <div class="contents-label">Contents</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{len(page_entries)} folios</div>',
            '    <div class="contents-rule"></div>',
            '    <div class="folio-list">',
            *[
                f'      <div class="folio-item"><a href="{escape(entry.href)}"><span class="folio-name">{escape(display_page_id(entry.page_id))}</span><span class="folio-arrow">&rarr;</span></a></div>'
                for entry in page_entries
            ],
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )


def render_book_ending_page(*, book_title: str, folio_count: int) -> str:
    return "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner contents-inner--centered">',
            '    <div class="contents-label">End of Codex</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{folio_count} folios assembled</div>',
            '    <div class="contents-rule contents-rule--centered"></div>',
            '    <a href="contents.html" class="contents-back-link">&larr; Return to contents</a>',
            "  </div>",
            "</div>",
        ]
    )


__all__ = [
    "BookSitePageEntry",
    "book_site_page_css",
    "render_book_cover_page",
    "render_book_contents_page",
    "render_book_ending_page",
]
