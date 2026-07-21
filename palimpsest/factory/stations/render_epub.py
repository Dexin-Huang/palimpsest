"""render_epub: the book model → an EPUB 3 you can put on a device.

Pure presentation — all content comes from ``book/book.json``. Spine: title
page → reader's note → chapters (translation, then the original text) →
colophon. Rule 8 makes this its own station: publish writes the model, this
writes the .epub.
"""

from __future__ import annotations

import html
import os

from ebooklib import epub

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json

_STYLE = """
body { font-family: Georgia, serif; line-height: 1.55; margin: 5%; }
h1, h2 { font-weight: normal; }
.folios { color: #666; font-style: italic; font-size: .9em; }
.original { margin-top: 2em; padding-top: 1em; border-top: 1px solid #ccc; }
.original h3 { font-size: 1em; color: #666; }
.colophon { font-size: .9em; color: #444; }
.sources { margin-top: 2em; padding-top: 1em; border-top: 1px dotted #ccc; }
"""


class RenderEpub(Station):
    name = "render_epub"

    grain = "manuscript"
    consumes = ("book",)
    produces = "book_epub"

    def run(self, job: Job) -> StationResult:
        book_model = read_json(job.path_of("book"))
        out_path = self.output_path(job)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        book = epub.EpubBook()
        book.set_identifier(f"palimpsest:{job.doc_id}")
        book.set_title(book_model["title"])
        book.set_language(book_model["language"]["translation"])
        if book_model.get("author"):
            book.add_author(book_model["author"])

        css = epub.EpubItem(
            uid="style",
            file_name="style.css",
            media_type="text/css",
            content=_STYLE.encode("utf-8"),
        )
        book.add_item(css)

        pages = [_title_page(book_model)]
        if book_model.get("readers_note"):
            pages.append(
                _chapter_page(
                    "note",
                    "A Note to the Reader",
                    _paragraphs(book_model["readers_note"]),
                )
            )
        evidence_by_id = {
            page["page_id"]: page
            for page in book_model.get("evidence", {}).get("pages", [])
        }
        for chapter in book_model["chapters"]:
            original_heading = "Original text"
            original_text = chapter["original"]
            if chapter.get("reading"):
                original_heading = "Original text (emended reading)"
                original_text = chapter["reading"]
            pages.append(
                _chapter_page(
                    chapter["id"],
                    chapter["heading"],
                    f'<p class="folios">ff. {chapter["pages"]["from"]}–'
                    f"{chapter['pages']['to']}</p>"
                    + _paragraphs(chapter["translation"])
                    + f'<div class="original"><h3>{original_heading}</h3>'
                    f"{_paragraphs(original_text)}</div>"
                    + _source_evidence_html(chapter, evidence_by_id),
                )
            )
        if book_model.get("apparatus"):
            pages.append(
                _chapter_page(
                    "apparatus", "Apparatus", _apparatus_html(book_model["apparatus"])
                )
            )
        pages.append(_chapter_page("colophon", "Colophon", _colophon_html(book_model)))

        for page in pages:
            page.add_item(css)
            book.add_item(page)
        book.toc = pages[1:]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", *pages]

        tmp_path = out_path.with_suffix(".epub.tmp")
        epub.write_epub(str(tmp_path), book)
        os.replace(tmp_path, out_path)
        return StationResult()


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return "".join(
        "<p>" + html.escape(block).replace("\n", "<br/>") + "</p>" for block in blocks
    )


def _chapter_page(uid: str, heading: str, body_html: str) -> epub.EpubHtml:
    page = epub.EpubHtml(uid=uid, title=heading, file_name=f"{uid}.xhtml")
    page.content = f"<h2>{html.escape(heading)}</h2>{body_html}"
    return page


def _title_page(model: dict) -> epub.EpubHtml:
    source = model.get("source", {})
    lines = [f"<h1>{html.escape(model['title'])}</h1>"]
    if model.get("author"):
        lines.append(f"<p>{html.escape(model['author'])}</p>")
    detail = " · ".join(
        html.escape(str(part))
        for part in (source.get("shelfmark"), source.get("date"))
        if part
    )
    if detail:
        lines.append(f'<p class="folios">{detail}</p>')
    page = epub.EpubHtml(uid="title", title=model["title"], file_name="title.xhtml")
    page.content = "".join(lines)
    return page


def _apparatus_html(apparatus: list[dict]) -> str:
    entries = "".join(
        f"<p>{html.escape(entry['original'])} → "
        f"{html.escape(entry['emended'])}<br/>"
        f"<i>{html.escape(entry['reason'])}</i></p>"
        for entry in apparatus
    )
    return (
        '<div class="colophon"><p>Changes made by the editorial pass; the '
        "verbatim transcription is preserved in the library workspace.</p>"
        f"{entries}</div>"
    )


def _source_evidence_html(chapter: dict, evidence_by_id: dict[str, dict]) -> str:
    pages = [
        evidence_by_id[page_id]
        for page_id in chapter.get("source_pages", [])
        if page_id in evidence_by_id
    ]
    if not pages:
        return ""
    entries = []
    for page in pages:
        stats = (page.get("alignment") or {}).get("stats") or {}
        coverage = ""
        if stats:
            coverage = (
                f"<p class='folios'>{stats.get('boxed', 0)} of "
                f"{stats.get('transcribed', 0)} ink characters aligned.</p>"
            )
        entries.append(
            f"<h4>Folio {html.escape(page['page_id'])}</h4>"
            f"<p><a href='{html.escape(page['source_image_url'])}'>"
            "View archive image</a></p>"
            f"{coverage}{_paragraphs(page['diplomatic'])}"
        )
    return (
        '<div class="sources"><h3>Source evidence</h3>'
        "<p>The diplomatic readings below are tied to the cited folio images; "
        "coordinate alignment data is preserved in the book model.</p>"
        f"{''.join(entries)}</div>"
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


def _colophon_html(model: dict) -> str:
    colophon = model.get("colophon", {})
    rows = [
        f"<p>{_production_credit(colophon)}.</p>",
        f"<p>{colophon.get('pages', 0)} pages · {_cost_text(colophon)}.</p>",
        "<p>Produced by the Palimpsest factory. Every stage of this book — "
        "transcription, alignment, translation, reconstruction, reference, "
        "and emendation — is recorded with full provenance in the book model.</p>",
    ]
    return f'<div class="colophon">{"".join(rows)}</div>'


register(RenderEpub())
