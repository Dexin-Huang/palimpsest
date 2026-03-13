from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import shutil

from palimpsest.web import (
    parse_markdown_document as web_parse_markdown_document,
    render_markdown_body as web_render_markdown_body,
    site_css as web_site_css,
)

from .common import display_page_id, page_sort_key, read_json, read_text, relpath, utc_now


@dataclass
class WitnessReadingArtifact:
    page_id: str
    image_path: Path
    reading_path: Path
    meta_path: Path
    model: str
    finish_reason: str
    char_count: int
    generated_at: str


@dataclass
class RenderedWitnessSiteArtifact:
    doc_id: str
    index_path: Path
    contents_path: Path
    ending_path: Path
    folio_paths: list[Path]
    meta_path: Path


def _reading_rank(artifact: WitnessReadingArtifact) -> tuple[int, int, int, str]:
    stop = 1 if artifact.finish_reason == "FinishReason.STOP" else 0
    full_model = 1 if "flash-lite" not in artifact.model else 0
    return (stop, full_model, artifact.char_count, artifact.generated_at)


def _collect_best_readings(doc_dir: Path) -> dict[str, WitnessReadingArtifact]:
    readings: dict[str, WitnessReadingArtifact] = {}
    for meta_path in doc_dir.glob("experiments/*_reading*/reading_meta.json"):
        try:
            meta = read_json(meta_path)
        except Exception:
            continue
        image_path = Path(meta.get("image_path", ""))
        reading_path = Path(meta.get("output_path", ""))
        page_id = image_path.stem
        if not page_id or not reading_path.exists():
            continue
        artifact = WitnessReadingArtifact(
            page_id=page_id,
            image_path=image_path,
            reading_path=reading_path,
            meta_path=meta_path,
            model=str(meta.get("model", "")),
            finish_reason=str(meta.get("finish_reason", "")),
            char_count=int(meta.get("char_count", 0) or 0),
            generated_at=str(meta.get("generated_at", "")),
        )
        existing = readings.get(page_id)
        if existing is None or _reading_rank(artifact) > _reading_rank(existing):
            readings[page_id] = artifact
    return readings


def _render_reading_html(reading_path: Path | None) -> str:
    if reading_path is None or not reading_path.exists():
        return '<div class="reader-empty">Pending witness read.</div>'
    doc = web_parse_markdown_document(read_text(reading_path))
    body_parts: list[str] = []
    if doc.title:
        body_parts.append(f'<div class="reader-doc-title">{escape(doc.title)}</div>')
    if doc.preamble.strip():
        body_parts.append(web_render_markdown_body(doc.preamble, preserve_linebreaks=True))
    for section in doc.sections:
        rendered_body = web_render_markdown_body(section.body, preserve_linebreaks=True)
        body_parts.append(
            "\n".join(
                [
                    '<section class="reader-section">',
                    f'  <div class="reader-section-title">{escape(section.title)}</div>',
                    f'  <div class="reader-section-body">{rendered_body}</div>',
                    "</section>",
                ]
            )
        )
    return "\n".join(body_parts)


def _reader_css() -> str:
    return "\n".join(
        [
            web_site_css(),
            "body { overflow: auto; }",
            ".reader-shell { min-height: 100vh; display: grid; grid-template-columns: minmax(320px, 46vw) 1fr; }",
            ".reader-image-panel { position: sticky; top: 0; height: 100vh; background: linear-gradient(180deg, #18120e 0%, #241813 100%); display: flex; align-items: center; justify-content: center; padding: 2rem; }",
            ".reader-image-panel img { max-width: 100%; max-height: 100%; object-fit: contain; box-shadow: 0 24px 80px rgba(0,0,0,0.45); }",
            ".reader-text-panel { min-height: 100vh; background: rgba(240,235,224,0.98); color: #1a1714; padding: 2.4rem 2.5rem 3rem; }",
            ".reader-topbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 1.5rem; margin-bottom: 1.6rem; }",
            ".reader-book-label { font-size: 0.7rem; letter-spacing: 0.22em; text-transform: uppercase; color: #8a4b2a; margin-bottom: 0.5rem; }",
            ".reader-page-title { font-size: clamp(1.8rem, 3vw, 3rem); margin: 0; }",
            ".reader-nav { display: flex; gap: 0.8rem; flex-wrap: wrap; }",
            ".reader-nav a { text-decoration: none; color: #8a4b2a; border-bottom: 1px solid rgba(138,75,42,0.35); }",
            ".reader-meta { color: #5d5146; font-size: 0.9rem; margin-bottom: 1.6rem; }",
            ".reader-doc-title { font-size: 1rem; letter-spacing: 0.14em; text-transform: uppercase; color: #8a4b2a; margin-bottom: 1rem; }",
            ".reader-section { border-top: 1px solid #d9ccb5; padding-top: 1rem; margin-top: 1rem; }",
            ".reader-section-title { font-size: 1.05rem; font-weight: 700; margin-bottom: 0.75rem; }",
            ".reader-section-body { line-height: 1.8; }",
            ".reader-empty { border: 1px dashed #c7b6a0; padding: 1rem 1.1rem; color: #6b6054; background: rgba(255,255,255,0.45); }",
            ".reader-index-shell { min-height: 100vh; padding: 4rem 2rem; display: flex; align-items: center; justify-content: center; }",
            ".reader-index-card { width: min(920px, 100%); background: rgba(240,235,224,0.96); color: #1a1714; padding: 2.4rem 2.6rem; box-shadow: 0 18px 70px rgba(0,0,0,0.18); }",
            ".reader-index-list { margin-top: 2rem; display: grid; gap: 0.7rem; }",
            ".reader-index-item { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #d9ccb5; padding-top: 0.8rem; gap: 1rem; }",
            ".reader-index-item a { text-decoration: none; color: #1a1714; font-size: 1.1rem; }",
            ".reader-index-item span { color: #8a4b2a; font-size: 0.74rem; letter-spacing: 0.18em; text-transform: uppercase; }",
            ".reader-hero-shell { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 2rem; }",
            ".reader-hero-card { width: min(980px, 100%); background: linear-gradient(180deg, rgba(240,235,224,0.97) 0%, rgba(233,225,212,0.98) 100%); color: #1a1714; padding: 3rem; box-shadow: 0 24px 80px rgba(0,0,0,0.22); }",
            ".reader-hero-kicker { font-size: 0.72rem; letter-spacing: 0.28em; text-transform: uppercase; color: #8a4b2a; margin-bottom: 1rem; }",
            ".reader-hero-title { font-size: clamp(2.4rem, 5vw, 4.8rem); line-height: 0.95; margin: 0 0 1rem; }",
            ".reader-hero-subtitle { color: #5d5146; line-height: 1.85; max-width: 50rem; font-size: 1.02rem; }",
            ".reader-hero-actions { display: flex; gap: 0.9rem; flex-wrap: wrap; margin-top: 2rem; }",
            ".reader-hero-actions a { text-decoration: none; color: #1a1714; padding: 0.85rem 1rem; border: 1px solid #cdbda6; background: rgba(255,255,255,0.45); }",
            ".reader-hero-actions a.primary { background: #1a1714; color: #f3ecdf; border-color: #1a1714; }",
            "@media (max-width: 980px) { .reader-shell { grid-template-columns: 1fr; } .reader-image-panel { position: relative; height: auto; min-height: 46vh; } }",
        ]
    )


def _html_page(*, title: str, body: str) -> str:
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f"  <title>{escape(title)}</title>",
            "  <style>",
            _reader_css(),
            "  </style>",
            "</head>",
            "<body>",
            body,
            "</body>",
            "</html>",
        ]
    )


def build_witness_reader_site(
    doc_dir: Path,
    *,
    out_dir: Path,
    title: str | None = None,
) -> RenderedWitnessSiteArtifact:
    doc_dir = doc_dir.resolve()
    out_dir = out_dir.resolve()
    page_list = read_json(doc_dir / "page_list.json")
    metadata = read_json(doc_dir / "metadata.json")
    readings = _collect_best_readings(doc_dir)

    book_title = title or metadata.get("title") or metadata.get("doc_id") or doc_dir.name
    doc_id = metadata.get("doc_id") or doc_dir.name

    title_path = out_dir / "index.html"
    contents_path = out_dir / "contents.html"
    ending_path = out_dir / "ending.html"
    folio_dir = out_dir / "folios"
    image_dir = out_dir / "assets" / "images"
    folio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    pages = sorted(page_list.get("pages", []), key=lambda page: page_sort_key(page.get("page_id", "")))
    folio_paths: list[Path] = []
    page_entries: list[dict[str, str]] = []
    ready_count = 0

    for index, page in enumerate(pages):
        page_id = page["page_id"]
        source_image = (doc_dir / "images" / page.get("filename", f"{page_id}.jpg")).resolve()
        image_target = image_dir / source_image.name
        if source_image.exists() and not image_target.exists():
            shutil.copy2(source_image, image_target)

        page_dir = folio_dir / page_id
        page_dir.mkdir(parents=True, exist_ok=True)

        prev_href = f"../{pages[index - 1]['page_id']}/index.html" if index > 0 else "../../contents.html"
        next_href = f"../{pages[index + 1]['page_id']}/index.html" if index < len(pages) - 1 else "../../ending.html"
        contents_href = "../../contents.html"
        reading = readings.get(page_id)
        model_note = f"{reading.model} | {reading.finish_reason}" if reading else "pending"
        if reading:
            ready_count += 1

        page_body = "\n".join(
            [
                '<div class="reader-shell">',
                '  <div class="reader-image-panel">',
                f'    <img src="{escape(relpath(page_dir, image_target))}" alt="{escape(page_id)}">',
                "  </div>",
                '  <div class="reader-text-panel">',
                '    <div class="reader-topbar">',
                "      <div>",
                '        <div class="reader-book-label">Palimpsest Reader</div>',
                f'        <h1 class="reader-page-title">{escape(display_page_id(page_id))}</h1>',
                "      </div>",
                '      <div class="reader-nav">',
                f'        <a href="{escape(contents_href)}">Contents</a>',
                f'        <a href="{escape(prev_href)}">Previous</a>',
                f'        <a href="{escape(next_href)}">Next</a>',
                "      </div>",
                "    </div>",
                f'    <div class="reader-meta">{escape(book_title)} · {escape(page_id)} · {escape(model_note)}</div>',
                _render_reading_html(reading.reading_path if reading else None),
                "  </div>",
                "</div>",
            ]
        )

        page_path = page_dir / "index.html"
        page_path.write_text(
            _html_page(
                title=f"{book_title} - {display_page_id(page_id)}",
                body=page_body,
            ),
            encoding="utf-8",
        )
        folio_paths.append(page_path)
        page_entries.append(
            {
                "page_id": page_id,
                "href": f"folios/{page_id}/index.html",
                "status": "ready" if reading else "pending",
            }
        )

    title_body = "\n".join(
        [
            '<div class="reader-hero-shell">',
            '  <div class="reader-hero-card">',
            '    <div class="reader-hero-kicker">Palimpsest Reader</div>',
            f'    <h1 class="reader-hero-title">{escape(book_title)}</h1>',
            f'    <div class="reader-hero-subtitle">A contiguous witness reader for {escape(doc_id)}. This edition opens with a title page, then a table of contents, then the folio sequence itself, and finally a closing page.</div>',
            f'    <div class="reader-meta">{ready_count} / {len(page_entries)} folios currently have witness reads.</div>',
            '    <div class="reader-hero-actions">',
            '      <a class="primary" href="contents.html">Open Contents</a>',
            *( [f'      <a href="{escape(page_entries[0]["href"])}">Begin Reading</a>'] if page_entries else [] ),
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )
    title_path.write_text(_html_page(title=book_title, body=title_body), encoding="utf-8")

    contents_body = "\n".join(
        [
            '<div class="reader-index-shell">',
            '  <div class="reader-index-card">',
            '    <div class="reader-book-label">Contents</div>',
            f'    <h1 class="reader-page-title">{escape(book_title)}</h1>',
            f'    <div class="reader-meta">{ready_count} / {len(page_entries)} folios currently have witness reads.</div>',
            '    <div class="reader-nav">',
            '      <a href="index.html">Title Page</a>',
            '      <a href="ending.html">Ending</a>',
            "    </div>",
            '    <div class="reader-index-list">',
            *[
                f'      <div class="reader-index-item"><a href="{escape(entry["href"])}">{escape(display_page_id(entry["page_id"]))}</a><span>{"Ready" if entry["status"] == "ready" else "Pending"}</span></div>'
                for entry in page_entries
            ],
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )
    contents_path.write_text(
        _html_page(title=f"{book_title} - Contents", body=contents_body),
        encoding="utf-8",
    )

    ending_body = "\n".join(
        [
            '<div class="reader-hero-shell">',
            '  <div class="reader-hero-card">',
            '    <div class="reader-hero-kicker">End Matter</div>',
            f'    <h1 class="reader-hero-title">End of {escape(book_title)}</h1>',
            '    <div class="reader-hero-subtitle">You have reached the end of the current witness reader. Return to the contents to revisit a folio or continue into a richer packet-based edition later.</div>',
            '    <div class="reader-hero-actions">',
            '      <a class="primary" href="contents.html">Back to Contents</a>',
            '      <a href="index.html">Title Page</a>',
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )
    ending_path.write_text(
        _html_page(title=f"{book_title} - End", body=ending_body),
        encoding="utf-8",
    )

    meta_path = out_dir / "site_meta.json"
    meta = {
        "generated_at": utc_now(),
        "doc_id": doc_id,
        "title": book_title,
        "doc_dir": str(doc_dir),
        "index_path": str(title_path),
        "contents_path": str(contents_path),
        "ending_path": str(ending_path),
        "folio_paths": [str(path) for path in folio_paths],
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedWitnessSiteArtifact(
        doc_id=doc_id,
        index_path=title_path,
        contents_path=contents_path,
        ending_path=ending_path,
        folio_paths=folio_paths,
        meta_path=meta_path,
    )
