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
"""


class RenderEpub(Station):
    name = "render_epub"
    version = "render_epub/v1"
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
            uid="style", file_name="style.css", media_type="text/css",
            content=_STYLE.encode("utf-8"),
        )
        book.add_item(css)

        pages = [_title_page(book_model)]
        if book_model.get("readers_note"):
            pages.append(_chapter_page(
                "note", "A Note to the Reader",
                _paragraphs(book_model["readers_note"])))
        for chapter in book_model["chapters"]:
            pages.append(_chapter_page(
                chapter["id"], chapter["heading"],
                f'<p class="folios">ff. {chapter["pages"]["from"]}–'
                f'{chapter["pages"]["to"]}</p>'
                + _paragraphs(chapter["translation"])
                + f'<div class="original"><h3>Original text</h3>'
                  f'{_paragraphs(chapter["original"])}</div>'))
        pages.append(_chapter_page(
            "colophon", "Colophon", _colophon_html(book_model)))

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
        "<p>" + html.escape(block).replace("\n", "<br/>") + "</p>"
        for block in blocks
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
        html.escape(str(part)) for part in
        (source.get("shelfmark"), source.get("date")) if part
    )
    if detail:
        lines.append(f'<p class="folios">{detail}</p>')
    page = epub.EpubHtml(uid="title", title=model["title"], file_name="title.xhtml")
    page.content = "".join(lines)
    return page


def _colophon_html(model: dict) -> str:
    colophon = model.get("colophon", {})
    rows = [
        f"<p>Transcribed by {html.escape(str(colophon.get('transcribed_by')))} · "
        f"translated by {html.escape(str(colophon.get('translated_by')))}.</p>",
        f"<p>{colophon.get('pages', 0)} pages · "
        f"production cost ${colophon.get('cost_usd_total', 0):.4f}.</p>",
        "<p>Produced by the Palimpsest factory. Every stage of this book — "
        "transcription, translation, reconstruction — is recorded with full "
        "provenance in its library workspace.</p>",
    ]
    return f'<div class="colophon">{"".join(rows)}</div>'


register(RenderEpub())
