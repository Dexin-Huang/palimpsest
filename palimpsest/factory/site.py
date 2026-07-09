"""The hosted library: a static site rendered from every published book model.

Library-level derivation, not a station — rebuild any time with
``palimpsest factory site``. Output is host-agnostic static HTML (GitHub
Pages works): a shelf page plus a reader per book, with the EPUB alongside.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.workspace.io import atomic_write_text, read_json
from palimpsest.factory.workspace.layout import doc_artifact

DEFAULT_SITE_ROOT = PROJECT_ROOT / "site"

_CSS = """
:root { --ink:#1a1712; --muted:#6b6357; --paper:#faf7f2; --card:#fff; --rule:#e4ddd2; --accent:#8a4b2d; }
* { box-sizing:border-box; }
body { margin:0; font-family:Georgia,serif; background:var(--paper); color:var(--ink); line-height:1.6; }
main { max-width:46rem; margin:0 auto; padding:3rem 1.25rem 5rem; }
h1 { font-size:1.9rem; margin:0 0 .25rem; }
h2 { font-size:1.3rem; margin:2.5rem 0 .5rem; }
a { color:var(--accent); }
.muted { color:var(--muted); font-style:italic; }
.book { background:var(--card); border:1px solid var(--rule); border-radius:8px; padding:1rem 1.25rem; margin:1rem 0; }
.book h2 { margin:.1rem 0 .2rem; font-size:1.15rem; }
.folios { color:var(--muted); font-size:.9rem; font-style:italic; }
.original { display:none; margin-top:1.2rem; padding-top:.8rem; border-top:1px dashed var(--rule); }
body.show-original .original { display:block; }
.toggle { font:inherit; font-size:.85rem; padding:.3rem .8rem; border:1px solid var(--rule); border-radius:99px; background:var(--card); cursor:pointer; }
.colophon { font-size:.88rem; color:var(--muted); border-top:2px solid var(--rule); margin-top:3rem; padding-top:1rem; }
"""

_TOGGLE_JS = (
    "<script>function tgl(){document.body.classList.toggle('show-original')}</script>"
)


def build(library_root: Path = LIBRARY_ROOT, site_root: Path = DEFAULT_SITE_ROOT) -> list[str]:
    """Render the site from every doc that has a published book model.
    Returns the doc_ids shelved."""
    models = []
    for book_path in sorted(library_root.glob("*/book/book.json")):
        models.append(read_json(book_path))

    site_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(site_root / "style.css", _CSS)
    atomic_write_text(site_root / "index.html", _shelf_html(models))

    for model in models:
        doc_id = model["doc_id"]
        book_dir = site_root / doc_id
        book_dir.mkdir(exist_ok=True)
        atomic_write_text(book_dir / "index.html", _reader_html(model))
        epub_path = doc_artifact(doc_id, "book_epub", library_root)
        if epub_path.exists():
            shutil.copyfile(epub_path, book_dir / epub_path.name)
    return [model["doc_id"] for model in models]


def _page(title: str, body: str, *, css_prefix: str = "") -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        f"<link rel='stylesheet' href='{css_prefix}style.css'></head>"
        f"<body><main>{body}</main>{_TOGGLE_JS}</body></html>"
    )


def _shelf_html(models: list[dict]) -> str:
    cards = []
    for model in models:
        source = model.get("source", {})
        detail = " · ".join(
            html.escape(str(part)) for part in
            (source.get("shelfmark"), source.get("date"),
             model.get("language", {}).get("original")) if part
        )
        cards.append(
            f"<div class='book'><h2><a href='{model['doc_id']}/'>"
            f"{html.escape(model['title'])}</a></h2>"
            f"<div class='folios'>{detail}</div>"
            f"<p class='muted'>{html.escape(model.get('readers_note', '')[:220])}</p></div>"
        )
    body = (
        "<h1>The Palimpsest Library</h1>"
        "<p class='muted'>Manuscripts recovered, transcribed, and translated "
        "by the factory — with full provenance.</p>"
        + ("".join(cards) or "<p>No books published yet.</p>")
    )
    return _page("The Palimpsest Library", body)


def _reader_html(model: dict) -> str:
    source = model.get("source", {})
    colophon = model.get("colophon", {})
    parts = [
        "<p><a href='../'>← library</a></p>",
        f"<h1>{html.escape(model['title'])}</h1>",
    ]
    if model.get("author"):
        parts.append(f"<p class='muted'>{html.escape(model['author'])}</p>")
    detail = " · ".join(
        html.escape(str(p)) for p in (source.get("shelfmark"), source.get("date")) if p
    )
    if detail:
        parts.append(f"<p class='folios'>{detail}</p>")
    parts.append(
        f"<p><a href='{model['doc_id']}.epub'>Download EPUB</a> &nbsp; "
        "<button class='toggle' onclick='tgl()'>Show original text</button></p>"
    )
    if model.get("readers_note"):
        parts.append(f"<p class='muted'>{html.escape(model['readers_note'])}</p>")
    for chapter in model["chapters"]:
        parts.append(f"<h2>{html.escape(chapter['heading'])}</h2>")
        parts.append(
            f"<div class='folios'>ff. {html.escape(chapter['pages']['from'])}–"
            f"{html.escape(chapter['pages']['to'])}</div>"
        )
        parts.append(_paragraphs(chapter["translation"]))
        parts.append(
            "<div class='original'><h3>Original</h3>"
            + _paragraphs(chapter["original"]) + "</div>"
        )
    parts.append(
        "<div class='colophon'>"
        f"<p>Transcribed by {html.escape(str(colophon.get('transcribed_by')))} · "
        f"translated by {html.escape(str(colophon.get('translated_by')))} · "
        f"{colophon.get('pages', 0)} pages · "
        f"cost ${colophon.get('cost_usd_total', 0):.4f}.</p></div>"
    )
    return _page(model["title"], "".join(parts), css_prefix="../")


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return "".join(
        "<p>" + html.escape(block).replace("\n", "<br/>") + "</p>" for block in blocks
    )
