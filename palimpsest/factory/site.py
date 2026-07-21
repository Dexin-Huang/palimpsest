"""The hosted library: a static site rendered from every published book model.

Library-level derivation, not a station — rebuild any time with
``palimpsest site``. Output is host-agnostic static HTML (GitHub
Pages works): a shelf page plus a reader per book, with the EPUB alongside.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from palimpsest.factory.core.artifact import content_fingerprint, read_provenance
from palimpsest.factory.core.ledger import fingerprint
from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.workspace.io import (
    atomic_write_json,
    atomic_write_text,
    read_json,
)
from palimpsest.factory.workspace.layout import artifact_path

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
.sources { margin-top:1rem; padding:.8rem 1rem; border-left:3px solid var(--rule); background:var(--card); }
.source-image { display:block; width:100%; height:auto; margin:1rem 0; }
"""

_TOGGLE_JS = (
    "<script>function tgl(){document.body.classList.toggle('show-original')}</script>"
)


def build(
    library_root: Path = LIBRARY_ROOT, site_root: Path = DEFAULT_SITE_ROOT
) -> list[str]:
    """Render the site from every doc that has a published book model.
    Returns the doc_ids shelved."""
    books = [
        (read_json(book_path), book_path)
        for book_path in sorted(library_root.glob("*/book/book.json"))
    ]
    models = [model for model, _ in books]

    site_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(site_root / "style.css", _CSS)
    atomic_write_text(site_root / "index.html", _shelf_html(models))

    for model, book_path in books:
        doc_id = model["doc_id"]
        book_dir = site_root / doc_id
        book_dir.mkdir(exist_ok=True)
        atomic_write_json(book_dir / "book.json", model)
        _publish_evidence(model, library_root, book_dir)
        epub_path = artifact_path(doc_id, "book_epub", None, library_root)
        epub_available = _epub_is_current(book_path, epub_path)
        published_epub = book_dir / epub_path.name
        atomic_write_text(
            book_dir / "index.html",
            _reader_html(model, epub_available=epub_available),
        )
        if epub_available:
            shutil.copyfile(epub_path, published_epub)
        else:
            published_epub.unlink(missing_ok=True)
    return [model["doc_id"] for model in models]


def _epub_is_current(book_path: Path, epub_path: Path) -> bool:
    if not epub_path.is_file():
        return False
    stamp = read_provenance(epub_path)
    return bool(
        stamp
        and stamp.get("station") == "render_epub"
        and stamp.get("input_fingerprint")
        == fingerprint(content_fingerprint(book_path))
    )


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
            html.escape(str(part))
            for part in (
                source.get("shelfmark"),
                source.get("date"),
                model.get("language", {}).get("original"),
            )
            if part
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


def _reader_html(model: dict, *, epub_available: bool = True) -> str:
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
    actions = []
    if epub_available:
        actions.append(f"<a href='{model['doc_id']}.epub'>Download EPUB</a>")
    actions.append("<button class='toggle' onclick='tgl()'>Show original text</button>")
    parts.append(f"<p>{' &nbsp; '.join(actions)}</p>")
    if model.get("readers_note"):
        parts.append(f"<p class='muted'>{html.escape(model['readers_note'])}</p>")
    for chapter in model["chapters"]:
        parts.append(f"<h2>{html.escape(chapter['heading'])}</h2>")
        parts.append(
            f"<div class='folios'>ff. {html.escape(chapter['pages']['from'])}–"
            f"{html.escape(chapter['pages']['to'])}</div>"
        )
        parts.append(_paragraphs(chapter["translation"]))
        label = "Original"
        original_text = chapter["original"]
        if chapter.get("reading"):
            label = "Original (emended reading)"
            original_text = chapter["reading"]
        parts.append(
            f"<div class='original'><h3>{label}</h3>"
            + _paragraphs(original_text)
            + "</div>"
        )
        source_pages = chapter.get("source_pages", [])
        if source_pages:
            links = " · ".join(
                f"<a href='evidence/{html.escape(page_id)}.html'>"
                f"source {html.escape(page_id)}</a>"
                for page_id in source_pages
            )
            parts.append(f"<div class='sources'>Source evidence: {links}</div>")
    if model.get("apparatus"):
        parts.append("<h2>Apparatus</h2>")
        for entry in model["apparatus"]:
            parts.append(
                f"<p class='muted'>{html.escape(entry['original'])} → "
                f"{html.escape(entry['emended'])} — "
                f"<i>{html.escape(entry['reason'])}</i></p>"
            )
    parts.append(
        "<div class='colophon'>"
        f"<p>{_production_credit(colophon)} · "
        f"{colophon.get('pages', 0)} pages · {_cost_text(colophon)}.</p></div>"
    )
    return _page(model["title"], "".join(parts), css_prefix="../")


def _publish_evidence(model: dict, library_root: Path, book_dir: Path) -> None:
    evidence_dir = book_dir / "evidence"
    shutil.rmtree(evidence_dir, ignore_errors=True)
    evidence_dir.mkdir()
    for page in model.get("evidence", {}).get("pages", []):
        page_id = page["page_id"]
        image_path = artifact_path(
            model["doc_id"], "page_image_clean", page_id, library_root
        )
        published_image = evidence_dir / f"{page_id}{image_path.suffix}"
        shutil.copyfile(image_path, published_image)
        alignment = page.get("alignment")
        if alignment:
            atomic_write_json(
                evidence_dir / f"{page_id}.alignment.json",
                {
                    "doc_id": model["doc_id"],
                    "page_id": page_id,
                    **alignment,
                },
            )
        atomic_write_text(
            evidence_dir / f"{page_id}.html",
            _evidence_page_html(
                model,
                page,
                image_name=published_image.name,
                has_alignment=bool(alignment),
            ),
        )


def _evidence_page_html(
    model: dict, page: dict, *, image_name: str, has_alignment: bool
) -> str:
    page_id = page["page_id"]
    alignment = page.get("alignment") or {}
    stats = alignment.get("stats") or {}
    links = [f"<a href='{html.escape(page['source_image_url'])}'>Archive image</a>"]
    if has_alignment:
        links.append(
            f"<a href='{html.escape(page_id)}.alignment.json'>Alignment JSON</a>"
        )
    coverage = ""
    if stats:
        coverage = (
            f"<p class='folios'>{stats.get('boxed', 0)} of "
            f"{stats.get('transcribed', 0)} ink characters mapped to image coordinates.</p>"
        )
    body = (
        "<p><a href='../'>← book</a></p>"
        f"<h1>{html.escape(model['title'])}: {html.escape(page_id)}</h1>"
        f"<p>{' · '.join(links)}</p>"
        f"<img class='source-image' src='{html.escape(image_name)}' "
        f"alt='Cleaned manuscript page {html.escape(page_id)}'>"
        f"{coverage}<h2>Diplomatic transcription</h2>"
        f"{_paragraphs(page['diplomatic'])}"
    )
    return _page(
        f"{model['title']}: {page_id}",
        body,
        css_prefix="../../",
    )


def _production_credit(colophon: dict) -> str:
    credits = [
        ("Transcribed", colophon.get("transcribed_by")),
        ("translated", colophon.get("translated_by")),
        ("referenced", colophon.get("referenced_by")),
        ("emended", colophon.get("emended_by")),
    ]
    return " · ".join(
        f"{label} by {html.escape(str(model))}" for label, model in credits if model
    )


def _cost_text(colophon: dict) -> str:
    if colophon.get("cost_complete"):
        return f"production cost ${colophon.get('cost_usd_total', 0):.4f}"
    return (
        f"known production cost ${colophon.get('cost_usd_known', 0):.4f}; "
        "unpriced agent work excluded"
    )


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return "".join(
        "<p>" + html.escape(block).replace("\n", "<br/>") + "</p>" for block in blocks
    )
