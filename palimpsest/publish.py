"""Static HTML book-site publisher.

Reads transcriptions.jsonl (or enriched.jsonl) and generates a browsable
static site: one HTML page per folio plus an index/table-of-contents page.

Works with both raw transcription records (text only) and enriched records
that also carry a ``translation`` field.
"""

from __future__ import annotations

import json
import os
import shutil
from html import escape
from pathlib import Path

from palimpsest.web.theme import html_shell, site_css


# ---------------------------------------------------------------------------
# Record loading
# ---------------------------------------------------------------------------

def _load_records(input_path: Path) -> list[dict]:
    """Load JSONL records, preserving file order."""
    records: list[dict] = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# ---------------------------------------------------------------------------
# Image resolution
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")


def _resolve_image(page_id: str, image_dir: Path) -> Path | None:
    """Find the image file matching *page_id* inside *image_dir*."""
    for ext in _IMAGE_EXTENSIONS:
        candidate = image_dir / f"{page_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _copy_images(
    records: list[dict],
    image_dir: Path,
    out_dir: Path,
) -> dict[str, str]:
    """Copy images into ``out_dir/images/`` and return page_id -> relative path map."""
    images_out = out_dir / "images"
    images_out.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for rec in records:
        page_id = rec["page_id"]
        src = _resolve_image(page_id, image_dir)
        if src is None:
            continue
        dest = images_out / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        mapping[page_id] = f"images/{src.name}"
    return mapping


# ---------------------------------------------------------------------------
# CSS overrides for the static publisher
# ---------------------------------------------------------------------------

def _publisher_css() -> str:
    """Extra CSS layered on top of the theme to make pages work as
    normal scrollable documents rather than fixed full-viewport spreads."""
    return """
  /* -- publisher overrides -- */
  body {
    overflow: auto;
    height: auto;
    width: auto;
    background: var(--parchment);
  }
  .pub-nav {
    position: sticky;
    top: 0;
    z-index: 100;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 1.5rem;
    background: var(--panel-bg);
    color: var(--panel-fg);
    font-size: 0.65rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }
  .pub-nav a {
    color: var(--faded-light);
    text-decoration: none;
  }
  .pub-nav a:hover { color: var(--parchment); }
  .pub-spread {
    display: grid;
    grid-template-columns: 1fr 1fr;
    min-height: 80vh;
    background: var(--parchment);
    border-bottom: 1px solid var(--rule);
  }
  .pub-spread.no-image {
    grid-template-columns: 1fr;
    max-width: 720px;
    margin: 0 auto;
  }
  .pub-image-panel {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 2rem;
    background: linear-gradient(160deg, var(--parchment-deep) 0%, var(--parchment) 40%, var(--parchment-deep) 100%);
  }
  .pub-image-panel img {
    max-width: 100%;
    max-height: 85vh;
    object-fit: contain;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06), 0 12px 32px rgba(0,0,0,0.08);
    border-radius: 1px;
  }
  .pub-text-panel {
    padding: 2.5rem 2.5rem 3rem 2rem;
    overflow-y: auto;
    box-shadow: inset 6px 0 16px -8px rgba(0,0,0,0.04);
  }
  .pub-text-panel h2 {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin: 2rem 0 0.9rem 0;
  }
  .pub-text-panel h2:first-child {
    margin-top: 0;
  }
  .pub-folio-label {
    font-size: 0.6rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 1rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--rule);
  }
  .pub-text {
    font-size: 0.95rem;
    line-height: 1.72;
    color: var(--ink-soft);
    white-space: pre-wrap;
    max-width: 520px;
  }
  .pub-translation {
    font-size: 0.82rem;
    line-height: 1.68;
    color: var(--faded);
    font-style: italic;
    white-space: pre-wrap;
    max-width: 520px;
  }
  .pub-footer {
    text-align: center;
    padding: 1.2rem;
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded-light);
    background: var(--panel-bg);
  }
  /* --- Index page --- */
  .pub-index-hero {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 40vh;
    background: var(--panel-bg);
    color: var(--parchment);
    padding: 3rem 1.5rem;
    text-align: center;
  }
  .pub-index-hero h1 {
    font-size: 2.4rem;
    font-weight: 300;
    letter-spacing: 0.04em;
    margin-bottom: 0.5rem;
  }
  .pub-index-hero p {
    font-size: 0.75rem;
    letter-spacing: 0.15em;
    color: var(--faded);
  }
  .pub-toc {
    max-width: 680px;
    margin: 0 auto;
    padding: 2rem 1.5rem 3rem;
  }
  .pub-toc h2 {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 1rem;
  }
  .pub-toc-list {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .pub-toc-list li {
    border-bottom: 1px solid var(--rule-light);
  }
  .pub-toc-list li a {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.6rem 0.4rem;
    text-decoration: none;
    color: var(--ink);
    font-size: 0.88rem;
    transition: background 0.15s ease;
  }
  .pub-toc-list li a:hover {
    background: var(--glow);
  }
  .pub-toc-list .toc-page-id {
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.04em;
  }
  .pub-toc-list .toc-preview {
    flex: 1;
    margin-left: 1.5rem;
    color: var(--faded);
    font-size: 0.76rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  @media (max-width: 860px) {
    .pub-spread {
      grid-template-columns: 1fr;
    }
    .pub-image-panel {
      min-height: 50vh;
      padding: 1.5rem;
    }
  }
"""


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _page_filename(page_id: str) -> str:
    """Derive a stable HTML filename from a page_id."""
    return f"{page_id}.html"


def _nav_bar(
    title: str,
    prev_page_id: str | None,
    next_page_id: str | None,
) -> str:
    """Render the sticky top navigation bar."""
    left = (
        f'<a href="{_page_filename(prev_page_id)}">&larr; {escape(prev_page_id)}</a>'
        if prev_page_id
        else "<span></span>"
    )
    right = (
        f'<a href="{_page_filename(next_page_id)}">{escape(next_page_id)} &rarr;</a>'
        if next_page_id
        else "<span></span>"
    )
    center = f'<a href="index.html">{escape(title)}</a>'
    return f'<nav class="pub-nav">{left}{center}{right}</nav>'


def _render_folio_page(
    rec: dict,
    *,
    title: str,
    image_rel: str | None,
    prev_page_id: str | None,
    next_page_id: str | None,
) -> str:
    """Build the full HTML for a single folio page."""
    page_id = rec["page_id"]
    text = rec.get("text", "")
    translation = rec.get("translation")

    nav = _nav_bar(title, prev_page_id, next_page_id)

    # Image panel
    if image_rel:
        image_panel = (
            f'<div class="pub-image-panel">'
            f'  <img src="{escape(image_rel)}" alt="Folio {escape(page_id)}">'
            f'</div>'
        )
        spread_class = "pub-spread"
    else:
        image_panel = ""
        spread_class = "pub-spread no-image"

    # Text panel
    text_parts: list[str] = []
    text_parts.append(f'<div class="pub-folio-label">Folio {escape(page_id)}</div>')

    text_parts.append('<h2>Transcription</h2>')
    text_parts.append(f'<div class="pub-text">{escape(text)}</div>')

    if translation:
        text_parts.append('<h2>Translation</h2>')
        text_parts.append(
            f'<div class="pub-translation">{escape(translation)}</div>'
        )

    text_panel = f'<div class="pub-text-panel">{"".join(text_parts)}</div>'

    # Metadata footer
    meta_parts: list[str] = []
    if rec.get("model"):
        meta_parts.append(rec["model"])
    if rec.get("source"):
        meta_parts.append(rec["source"])
    footer_text = " &middot; ".join(escape(m) for m in meta_parts) if meta_parts else "Palimpsest"
    footer = f'<footer class="pub-footer">{footer_text}</footer>'

    body = f"{nav}\n<div class=\"{spread_class}\">\n{image_panel}\n{text_panel}\n</div>\n{footer}"

    return html_shell(
        title=f"{page_id} — {title}",
        body=body,
        extra_css=_publisher_css(),
    )


def _render_index_page(
    records: list[dict],
    *,
    title: str,
) -> str:
    """Build the index / table-of-contents page."""
    hero = (
        '<div class="pub-index-hero">'
        f'  <h1>{escape(title)}</h1>'
        f'  <p>{len(records)} folios</p>'
        '</div>'
    )

    items: list[str] = []
    for rec in records:
        page_id = rec["page_id"]
        preview = rec.get("text", "")[:80].replace("\n", " ").strip()
        items.append(
            f'<li>'
            f'<a href="{_page_filename(page_id)}">'
            f'<span class="toc-page-id">{escape(page_id)}</span>'
            f'<span class="toc-preview">{escape(preview)}</span>'
            f'</a></li>'
        )

    toc = (
        '<div class="pub-toc">'
        '<h2>Contents</h2>'
        f'<ul class="pub-toc-list">{"".join(items)}</ul>'
        '</div>'
    )

    footer = '<footer class="pub-footer">Generated by Palimpsest</footer>'

    body = f"{hero}\n{toc}\n{footer}"

    return html_shell(
        title=title,
        body=body,
        extra_css=_publisher_css(),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_book_site(
    input_path: Path,
    *,
    out_dir: Path,
    title: str | None = None,
    image_dir: Path | None = None,
) -> Path:
    """Generate a static HTML book site from a JSONL transcription file.

    Parameters
    ----------
    input_path:
        Path to ``transcriptions.jsonl`` or ``enriched.jsonl``.
    out_dir:
        Directory where the HTML site will be written.
    title:
        Human-readable book title.  Falls back to the ``book_title``
        field from the first record, or the input filename.
    image_dir:
        Optional directory containing page images (``<page_id>.jpg``).
        When provided, images are copied into ``out_dir/images/`` and
        each folio page displays the corresponding image.

    Returns
    -------
    Path
        The path to the generated ``index.html``.
    """
    input_path = Path(input_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    records = _load_records(input_path)
    if not records:
        raise ValueError(f"No records found in {input_path}")

    # Resolve title
    if title is None:
        title = records[0].get("book_title") or input_path.stem
    if not title:
        title = input_path.stem

    # Copy images if available
    image_map: dict[str, str] = {}
    if image_dir is not None:
        image_dir = Path(image_dir).resolve()
        image_map = _copy_images(records, image_dir, out_dir)

    # Generate per-folio pages
    for idx, rec in enumerate(records):
        prev_id = records[idx - 1]["page_id"] if idx > 0 else None
        next_id = records[idx + 1]["page_id"] if idx < len(records) - 1 else None
        page_id = rec["page_id"]

        html = _render_folio_page(
            rec,
            title=title,
            image_rel=image_map.get(page_id),
            prev_page_id=prev_id,
            next_page_id=next_id,
        )
        (out_dir / _page_filename(page_id)).write_text(html, encoding="utf-8")

    # Generate index
    index_html = _render_index_page(records, title=title)
    index_path = out_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    print(f"Published {len(records)} folios to {out_dir}")
    return index_path
