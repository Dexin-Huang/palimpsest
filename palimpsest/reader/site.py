from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import shutil

from palimpsest.packets.scholar import repair_packet_json
from palimpsest.web import html_shell as web_html_shell

from .common import display_page_id, page_sort_key, relpath, utc_now
from .folio import render_packet_folio_html


@dataclass
class RenderedPacketSiteArtifact:
    doc_id: str
    index_path: Path
    contents_path: Path
    ending_path: Path
    folio_paths: list[Path]
    meta_path: Path


def build_packet_book_site(
    packet_paths: list[Path],
    *,
    out_dir: Path,
    title: str | None = None,
) -> RenderedPacketSiteArtifact:
    if not packet_paths:
        raise ValueError("At least one packet path is required")

    resolved_packets = sorted((Path(path).resolve() for path in packet_paths), key=lambda path: page_sort_key(repair_packet_json(path).page_id))
    first_packet = repair_packet_json(resolved_packets[0])
    doc_id = first_packet.doc_id
    book_title = title or doc_id.replace("_", " ")

    out_dir = out_dir.resolve()
    title_path = out_dir / "index.html"
    contents_path = out_dir / "contents.html"
    ending_path = out_dir / "ending.html"
    folio_dir = out_dir / "folios"
    image_dir = out_dir / "assets" / "images"
    folio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    copied_images: dict[str, str] = {}
    folio_paths: list[Path] = []
    page_entries: list[dict[str, str]] = []

    packets = [repair_packet_json(path) for path in resolved_packets]
    for packet in packets:
        source_image = Path(packet.source_image_path).resolve()
        image_target = image_dir / source_image.name
        if not image_target.exists():
            shutil.copy2(source_image, image_target)
        copied_images[packet.page_id] = relpath(folio_dir / packet.page_id, image_target)

    for index, (packet_path, packet) in enumerate(zip(resolved_packets, packets)):
        page_out_dir = folio_dir / packet.page_id
        page_out_dir.mkdir(parents=True, exist_ok=True)
        prev_href = f"../{packets[index - 1].page_id}/index.html" if index > 0 else "../../contents.html"
        next_href = f"../{packets[index + 1].page_id}/index.html" if index < len(packets) - 1 else "../../ending.html"
        artifact = render_packet_folio_html(
            packet_path,
            out_dir=page_out_dir,
            book_title=book_title,
            image_href=copied_images[packet.page_id],
            prev_href=prev_href,
            next_href=next_href,
            home_href="../../contents.html",
            include_cover=False,
        )
        folio_paths.append(artifact.html_path)
        page_entries.append({"page_id": packet.page_id, "href": f"folios/{packet.page_id}/index.html"})

    shell_css = "\n".join(
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
        ]
    )

    title_body = "\n".join(
        [
            '<div class="book">',
            '  <div class="page cover active" data-page="0">',
            '    <div class="cover-label">Palimpsest Codex</div>',
            f'    <h1 class="cover-title">{escape(book_title)}</h1>',
            f'    <div class="cover-subtitle">Assembled set of {len(page_entries)} folios</div>',
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
    title_path.write_text(web_html_shell(title=book_title, body=title_body), encoding="utf-8")

    contents_body = "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner">',
            '    <div class="contents-label">Contents</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{len(page_entries)} folios</div>',
            '    <div class="contents-rule"></div>',
            '    <div class="folio-list">',
            *[
                f'      <div class="folio-item"><a href="{escape(entry["href"])}"><span class="folio-name">{escape(display_page_id(entry["page_id"]))}</span><span class="folio-arrow">&rarr;</span></a></div>'
                for entry in page_entries
            ],
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )
    contents_path.write_text(web_html_shell(title=f"{book_title} - Contents", body=contents_body, extra_css=shell_css), encoding="utf-8")

    ending_body = "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner" style="text-align: center;">',
            '    <div class="contents-label">End of Codex</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{len(page_entries)} folios assembled</div>',
            '    <div class="contents-rule" style="margin-left: auto; margin-right: auto;"></div>',
            f'    <a href="contents.html" style="font-size: 0.7rem; letter-spacing: 0.2em; text-transform: uppercase; color: var(--faded); text-decoration: none; transition: color 0.2s ease;"',
            '       onmouseover="this.style.color=\'var(--parchment)\'" onmouseout="this.style.color=\'var(--faded)\'">',
            '      &larr; Return to contents</a>',
            "  </div>",
            "</div>",
        ]
    )
    ending_path.write_text(web_html_shell(title=f"{book_title} - End", body=ending_body, extra_css=shell_css), encoding="utf-8")

    meta_path = out_dir / "site_meta.json"
    meta = {
        "generated_at": utc_now(),
        "doc_id": doc_id,
        "title": book_title,
        "packet_paths": [str(path) for path in resolved_packets],
        "folio_paths": [str(path) for path in folio_paths],
        "index_path": str(title_path),
        "contents_path": str(contents_path),
        "ending_path": str(ending_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketSiteArtifact(
        doc_id=doc_id,
        index_path=title_path,
        contents_path=contents_path,
        ending_path=ending_path,
        folio_paths=folio_paths,
        meta_path=meta_path,
    )
