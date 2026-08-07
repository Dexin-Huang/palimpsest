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

from palimpsest.factory import brand
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import fsync_directory, read_json

# Brand palette hexes (branding/brand.json) applied at presentation level:
# ink_vault text on mineral_paper surfaces, parchment rules, seal_vermilion
# accent for the mark, oxidized_slate for secondary text.
_STYLE = """
body { font-family: 'Libre Caslon Text', 'Libre Caslon Display', Georgia, serif; color: #17252C; background: #F2F0E9; line-height: 1.55; margin: 5%; }
h1, h2 { font-weight: normal; color: #17252C; }
h1 { font-family: 'Libre Caslon Display', 'Libre Caslon Text', Georgia, serif; }
.brand { text-align: center; margin: 2.5em 0 3em; }
.brand .mark { width: 3.2em; height: 3.2em; }
.brand .wordmark { font-family: 'Libre Caslon Display', 'Libre Caslon Text', Georgia, serif; text-transform: uppercase; letter-spacing: .16em; font-size: 1.5em; margin-top: .7em; }
.brand .promise { font-style: italic; color: #405054; margin: .5em 0 0; }
.folios { color: #405054; font-style: italic; font-size: .9em; }
.original { margin-top: 2em; padding-top: 1em; border-top: 1px solid #C9B99A; }
.original h3 { font-size: 1em; color: #405054; }
.colophon { font-size: .9em; color: #405054; }
.sources { margin-top: 2em; padding-top: 1em; border-top: 1px dotted #C9B99A; }
"""


class RenderEpub(Station):
    name = "render_epub"

    grain = "manuscript"
    consumes = ("book",)
    produces = "book_epub"
    production_dependencies = ("factory/brand.py",)

    def run(self, job: Job) -> StationResult:
        book_model = read_json(job.path_of("book"))
        out_path = self.output_path(job)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        identity = book_model["identity"]
        book = epub.EpubBook()
        book.set_identifier(f"palimpsest:{job.doc_id}")
        book.set_title(identity["title"])
        book.set_language(book_model["languages"]["translation"])
        if identity["author"]:
            book.add_author(identity["author"])

        css = epub.EpubItem(
            uid="style",
            file_name="style.css",
            media_type="text/css",
            content=_STYLE.encode("utf-8"),
        )
        book.add_item(css)

        pages = [_title_page(book_model)]
        if book_model["readers_note"]:
            pages.append(
                _chapter_page(
                    "note",
                    "A Note to the Reader",
                    _paragraphs(book_model["readers_note"]),
                )
            )
        folios_by_id = {folio["page_id"]: folio for folio in book_model["folios"]}
        apparatus_by_id = {entry["id"]: entry for entry in book_model["apparatus"]}
        for section in book_model["sections"]:
            content = section["content"]
            section_apparatus = [
                apparatus_by_id[apparatus_id]
                for apparatus_id in section["apparatus_ids"]
            ]
            apparatus_html = (
                "<h3>Apparatus</h3>" + _apparatus_html(section_apparatus)
                if section_apparatus
                else ""
            )
            pages.append(
                _chapter_page(
                    section["id"],
                    section["heading"],
                    f'<p class="folios">ff. '
                    f"{html.escape(_folio_label(section['folio_ids']))}</p>"
                    + "<h3>Translation</h3>"
                    + _paragraphs(content["translation"]["text"])
                    + '<div class="original"><h3>Emended reading</h3>'
                    + _paragraphs(content["emended_reading"]["text"])
                    + "<h3>Diplomatic transcription</h3>"
                    + _paragraphs(content["diplomatic_transcription"]["text"])
                    + apparatus_html
                    + "</div>"
                    + _source_evidence_html(section, folios_by_id),
                )
            )
        if book_model["apparatus"]:
            pages.append(
                _chapter_page(
                    "apparatus",
                    "Complete Apparatus",
                    _apparatus_html(book_model["apparatus"]),
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
        fsync_directory(out_path.parent)
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
    identity = model["identity"]
    mark = brand.mark_svg(fill=brand.SEAL_VERMILION, width=64, height=64)
    lines = [
        f'<div class="brand">{mark}<div class="wordmark">Palimpsest</div>'
        f'<p class="promise">{html.escape(brand.PROMISE)}</p></div>',
        f"<h1>{html.escape(identity['title'])}</h1>",
    ]
    if identity["author"]:
        lines.append(f"<p>{html.escape(identity['author'])}</p>")
    detail = " · ".join(
        html.escape(str(part))
        for part in (identity["shelfmark"], identity["date"])
        if part
    )
    if detail:
        lines.append(f'<p class="folios">{detail}</p>')
    page = epub.EpubHtml(uid="title", title=identity["title"], file_name="title.xhtml")
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


def _source_evidence_html(section: dict, folios_by_id: dict[str, dict]) -> str:
    folios = [folios_by_id[page_id] for page_id in section["folio_ids"]]
    entries = []
    for folio in folios:
        page_id = folio["page_id"]
        evidence = folio["evidence"]
        stats = (evidence.get("alignment") or {}).get("stats") or {}
        coverage = ""
        if stats:
            coverage = (
                f"<p class='folios'>{stats.get('boxed', 0)} of "
                f"{stats.get('transcribed', 0)} ink characters aligned.</p>"
            )
        entries.append(
            f"<h4>Folio {html.escape(page_id)}</h4>"
            f"<p><a href='{html.escape(folio['images']['original']['source_url'])}'>"
            "View archive image</a></p>"
            f"{coverage}"
            f"{_paragraphs(evidence['diplomatic']['text'])}"
        )
    alignment_note = ""
    if any(folio["evidence"].get("alignment") for folio in folios):
        alignment_note = " Available coordinate-alignment coverage is summarized below."
    return (
        '<div class="sources"><h3>Source evidence</h3>'
        "<p>The diplomatic readings below are tied to the cited folio images."
        f"{alignment_note}</p>{''.join(entries)}</div>"
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


def _colophon_html(model: dict) -> str:
    colophon = model.get("colophon", {})
    rows = [
        f"<p>{_production_credit(colophon)}.</p>",
        f"<p>{colophon.get('pages', 0)} pages · {_cost_text(colophon)}.</p>",
        "<p>Produced by the Palimpsest factory. The book model records "
        "provenance for its contributing editorial stages, including "
        "final-edition review; the EPUB renderer records its own provenance "
        "beside the file in the library workspace.</p>",
    ]
    return f'<div class="colophon">{"".join(rows)}</div>'


register(RenderEpub())
