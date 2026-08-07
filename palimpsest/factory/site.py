"""The hosted library: a static site rendered from every published book model.

Library-level derivation, not a station — rebuild any time with
``palimpsest site``. Output is host-agnostic static HTML (GitHub
Pages works): a shelf page plus a reader per book, with the EPUB alongside.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from palimpsest.factory import brand
from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.publication_bundle import epub_is_current, load_book
from palimpsest.factory.workspace.io import atomic_write_json, atomic_write_text
from palimpsest.factory.workspace.layout import artifact_path

DEFAULT_SITE_ROOT = PROJECT_ROOT / "site"

# Brand palette (branding/brand.json) mapped onto the existing token names so
# page markup keeps working: ink_vault text on mineral_paper, parchment rules,
# seal_vermilion accents, oxidized_slate for secondary text. Type stacks lead
# with the brand faces and fall back to system serifs.
_CSS = """
:root { --ink:#17252C; --muted:#405054; --paper:#F2F0E9; --card:rgba(201,185,154,.22); --rule:#C9B99A; --accent:#9C3F35; --parchment:#C9B99A; --slate:#405054; --seal:#9C3F35; }
* { box-sizing:border-box; }
body { margin:0; font-family:'Libre Caslon Text','Libre Caslon Display',Georgia,serif; background:var(--paper); color:var(--ink); line-height:1.6; }
main { max-width:46rem; margin:0 auto; padding:3rem 1.25rem 5rem; }
h1 { font-size:1.9rem; margin:0 0 .25rem; font-family:'Libre Caslon Display','Libre Caslon Text',Georgia,serif; font-weight:400; }
h2 { font-size:1.3rem; margin:2.5rem 0 .5rem; font-family:'Libre Caslon Display','Libre Caslon Text',Georgia,serif; font-weight:400; }
h3 { font-family:'Libre Caslon Display','Libre Caslon Text',Georgia,serif; font-weight:400; }
a { color:var(--accent); }
.muted { color:var(--muted); font-style:italic; }
.brand { margin-bottom:2rem; }
.brand .mark { display:block; width:3.4rem; height:3.4rem; }
.brand .wordmark { font-family:'Libre Caslon Display','Libre Caslon Text',Georgia,serif; text-transform:uppercase; letter-spacing:.16em; font-size:1.15rem; margin-top:.6rem; }
.brand .promise { color:var(--muted); font-style:italic; font-size:.95rem; margin:.35rem 0 0; }
.brand.compact { display:flex; align-items:center; gap:.8rem; margin:.75rem 0 1.5rem; }
.brand.compact .mark { width:2rem; height:2rem; }
.brand.compact .wordmark { margin:0; font-size:.9rem; }
.brand.compact .promise { margin:0 0 0 auto; font-size:.85rem; }
.book { background:var(--card); border:1px solid var(--rule); border-radius:8px; padding:1rem 1.25rem; margin:1rem 0; box-shadow:0 1px 2px rgba(23,37,44,.08); }
.book h2 { margin:.1rem 0 .2rem; font-size:1.15rem; }
.folios { color:var(--muted); font-size:.9rem; font-style:italic; }
.original { display:none; margin-top:1.2rem; padding-top:.8rem; border-top:1px dashed var(--rule); }
body.show-original .original { display:block; }
.toggle { font-family:'Inter',system-ui,sans-serif; font-size:.85rem; padding:.3rem .8rem; border:1px solid var(--rule); border-radius:99px; background:var(--card); cursor:pointer; }
.colophon { font-size:.88rem; color:var(--muted); border-top:2px solid var(--rule); margin-top:3rem; padding-top:1rem; }
.sources { margin-top:1rem; padding:.8rem 1rem; border-left:3px solid var(--rule); background:var(--card); }
.source-image { display:block; width:100%; height:auto; margin:1rem 0; }
.footer { max-width:46rem; margin:0 auto; padding:0 1.25rem 2.5rem; border-top:1px solid var(--rule); color:var(--muted); font-size:.85rem; font-family:'Inter',system-ui,sans-serif; }
.footer p { margin:.5rem 0 0; }
.footer .promise { font-style:italic; }
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
        (load_book(book_path), book_path)
        for book_path in sorted(library_root.glob("*/book/book.json"))
    ]
    models = [model for model, _ in books]

    site_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(site_root / "style.css", _CSS)
    atomic_write_text(site_root / "favicon.svg", brand.favicon_svg())
    atomic_write_text(site_root / "index.html", _shelf_html(models))

    for model, book_path in books:
        doc_id = model["doc_id"]
        book_dir = site_root / doc_id
        book_dir.mkdir(exist_ok=True)
        atomic_write_json(book_dir / "book.json", model)
        _publish_evidence(model, library_root, book_dir)
        epub_path = artifact_path(doc_id, "book_epub", None, library_root)
        epub_available = epub_is_current(book_path, epub_path)
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


def _brand_lockup(*, compact: bool = False) -> str:
    size = 32 if compact else 54
    mark = brand.mark_svg(fill=brand.INK_VAULT, width=size, height=size)
    klass = "brand compact" if compact else "brand"
    return (
        f"<div class='{klass}'>{mark}"
        "<div class='wordmark'>Palimpsest</div>"
        f"<p class='promise'>{html.escape(brand.PROMISE)}</p></div>"
    )


def _footer() -> str:
    return (
        "<footer class='footer'>"
        f"<p class='promise'>{html.escape(brand.PROMISE)}</p>"
        "<p><a href='https://github.com/Dexin-Huang/palimpsest'>"
        "GitHub repository</a> · "
        "<a href='https://github.com/Dexin-Huang/palimpsest/tree/master/docs'>"
        "Documentation</a></p></footer>"
    )


def _page(title: str, body: str, *, css_prefix: str = "") -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        f"<link rel='icon' type='image/svg+xml' href='{css_prefix}favicon.svg'>"
        f"<link rel='stylesheet' href='{css_prefix}style.css'></head>"
        f"<body><main>{body}</main>{_footer()}{_TOGGLE_JS}</body></html>"
    )


def _shelf_html(models: list[dict]) -> str:
    cards = []
    for model in models:
        identity = model["identity"]
        note = model["readers_note"]
        if len(note) > 220:
            boundary = note.rfind(" ", 0, 220)
            excerpt = note[: boundary if boundary > 0 else 219].rstrip() + "…"
        else:
            excerpt = note
        detail = " · ".join(
            html.escape(str(part))
            for part in (
                identity["shelfmark"],
                identity["date"],
                model["languages"]["original"],
            )
            if part
        )
        cards.append(
            f"<div class='book'><h2><a href='{model['doc_id']}/'>"
            f"{html.escape(identity['title'])}</a></h2>"
            f"<div class='folios'>{detail}</div>"
            f"<p class='muted'>{html.escape(excerpt)}</p></div>"
        )
    body = (
        _brand_lockup()
        + "<h1>The Palimpsest Library</h1>"
        + "<p class='muted'>Manuscripts recovered, transcribed, and translated "
        "by the factory — with full provenance.</p>"
        + ("".join(cards) or "<p>No books published yet.</p>")
    )
    return _page("The Palimpsest Library", body)


def _reader_html(model: dict, *, epub_available: bool = True) -> str:
    identity = model["identity"]
    colophon = model["colophon"]
    parts = [
        "<p><a href='../'>← library</a></p>",
        _brand_lockup(compact=True),
        f"<h1>{html.escape(identity['title'])}</h1>",
    ]
    if identity["author"]:
        parts.append(f"<p class='muted'>{html.escape(identity['author'])}</p>")
    detail = " · ".join(
        html.escape(str(part))
        for part in (identity["shelfmark"], identity["date"])
        if part
    )
    if detail:
        parts.append(f"<p class='folios'>{detail}</p>")
    actions = []
    if epub_available:
        actions.append(f"<a href='{model['doc_id']}.epub'>Download EPUB</a>")
    actions.append(
        "<button class='toggle' onclick='tgl()'>Show editorial layers</button>"
    )
    parts.append(f"<p>{' &nbsp; '.join(actions)}</p>")
    if model["readers_note"]:
        parts.append(f"<p class='muted'>{html.escape(model['readers_note'])}</p>")

    apparatus_by_id = {entry["id"]: entry for entry in model["apparatus"]}
    for section in model["sections"]:
        content = section["content"]
        parts.append(f"<h2>{html.escape(section['heading'])}</h2>")
        parts.append(
            f"<div class='folios'>ff. "
            f"{html.escape(_folio_label(section['folio_ids']))}</div>"
        )
        parts.append("<h3>Translation</h3>")
        parts.append(_paragraphs(content["translation"]["text"]))
        parts.append("<div class='original'><h3>Emended reading</h3>")
        parts.append(_paragraphs(content["emended_reading"]["text"]))
        parts.append("<h3>Diplomatic transcription</h3>")
        parts.append(_paragraphs(content["diplomatic_transcription"]["text"]))
        section_apparatus = [
            apparatus_by_id[apparatus_id] for apparatus_id in section["apparatus_ids"]
        ]
        if section_apparatus:
            parts.append("<h3>Apparatus</h3>")
            for entry in section_apparatus:
                parts.append(
                    f"<p class='muted'>{html.escape(entry['original'])} → "
                    f"{html.escape(entry['emended'])} — "
                    f"<i>{html.escape(entry['reason'])}</i></p>"
                )
        parts.append("</div>")
        links = " · ".join(
            f"<a href='evidence/{html.escape(page_id)}.html'>"
            f"source {html.escape(page_id)}</a>"
            for page_id in section["folio_ids"]
        )
        parts.append(f"<div class='sources'>Source evidence: {links}</div>")

    parts.append(
        "<div class='colophon'>"
        f"<p>{_production_credit(colophon)} · "
        f"{colophon['pages']} pages · {_cost_text(colophon)}.</p></div>"
    )
    return _page(identity["title"], "".join(parts), css_prefix="../")


def _publish_evidence(model: dict, library_root: Path, book_dir: Path) -> None:
    evidence_dir = book_dir / "evidence"
    shutil.rmtree(evidence_dir, ignore_errors=True)
    evidence_dir.mkdir()
    for folio in model["folios"]:
        page_id = folio["page_id"]
        images = folio["images"]
        original = images["original"]
        original_path = artifact_path(
            model["doc_id"], original["kind"], page_id, library_root
        )
        published_original = evidence_dir / f"{page_id}{original_path.suffix}"
        shutil.copyfile(original_path, published_original)

        enhanced = images.get("enhanced")
        published_enhanced = None
        if enhanced:
            enhanced_path = artifact_path(
                model["doc_id"], enhanced["kind"], page_id, library_root
            )
            published_enhanced = (
                evidence_dir / f"{page_id}.enhanced{enhanced_path.suffix}"
            )
            shutil.copyfile(enhanced_path, published_enhanced)

        evidence = folio["evidence"]
        alignment = evidence.get("alignment")
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
                folio,
                original_image_name=published_original.name,
                enhanced_image_name=(
                    published_enhanced.name if published_enhanced else None
                ),
                has_alignment=bool(alignment),
            ),
        )


def _evidence_page_html(
    model: dict,
    folio: dict,
    *,
    original_image_name: str,
    enhanced_image_name: str | None,
    has_alignment: bool,
) -> str:
    identity = model["identity"]
    page_id = folio["page_id"]
    evidence = folio["evidence"]
    alignment = evidence.get("alignment") or {}
    stats = alignment.get("stats") or {}
    links = [
        f"<a href='{html.escape(folio['images']['original']['source_url'])}'>"
        "Archive image</a>"
    ]
    if enhanced_image_name:
        links.append(f"<a href='{html.escape(enhanced_image_name)}'>Enhanced image</a>")
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
        f"<h1>{html.escape(identity['title'])}: {html.escape(page_id)}</h1>"
        f"<p>{' · '.join(links)}</p>"
        f"<img class='source-image' src='{html.escape(original_image_name)}' "
        f"alt='Original manuscript page {html.escape(page_id)}'>"
        f"{coverage}<h2>Diplomatic transcription</h2>"
        f"{_paragraphs(evidence['diplomatic']['text'])}"
    )
    return _page(
        f"{identity['title']}: {page_id}",
        body,
        css_prefix="../../",
    )


def _folio_label(folio_ids: list[str]) -> str:
    if len(folio_ids) == 1:
        return folio_ids[0]
    return f"{folio_ids[0]}–{folio_ids[-1]}"


def _production_credit(colophon: dict) -> str:
    credits = [
        ("Transcribed", colophon.get("transcribed_by")),
        ("translated", colophon.get("translated_by")),
        ("referenced", colophon.get("referenced_by")),
        ("emended", colophon.get("emended_by")),
        ("finalized", colophon.get("finalized_by")),
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
