from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from palimpsest.contracts import EDITION_HTML_FILENAME
from palimpsest.packets.state import load_packet_json
from palimpsest.web.site_pages import (
    BookSitePageEntry,
    book_site_page_css,
    render_book_contents_page,
    render_book_cover_page,
    render_book_ending_page,
)
from palimpsest.web.theme import html_shell as web_html_shell

from .common import page_sort_key, relpath, utc_now
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

    packet_records = [(Path(path).resolve(), load_packet_json(Path(path).resolve())) for path in packet_paths]
    packet_records.sort(key=lambda item: page_sort_key(item[1].page_id))
    resolved_packets = [path for path, _packet in packet_records]
    packets = [packet for _path, packet in packet_records]
    first_packet = packets[0]
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
    page_entries: list[BookSitePageEntry] = []

    for packet in packets:
        source_image = Path(packet.source_image_path).resolve()
        image_target = image_dir / source_image.name
        if not image_target.exists():
            shutil.copy2(source_image, image_target)
        copied_images[packet.page_id] = relpath(folio_dir / packet.page_id, image_target)

    for index, (packet_path, packet) in enumerate(zip(resolved_packets, packets)):
        page_out_dir = folio_dir / packet.page_id
        page_out_dir.mkdir(parents=True, exist_ok=True)
        prev_href = f"../{packets[index - 1].page_id}/{EDITION_HTML_FILENAME}" if index > 0 else "../../contents.html"
        next_href = f"../{packets[index + 1].page_id}/{EDITION_HTML_FILENAME}" if index < len(packets) - 1 else "../../ending.html"
        artifact = render_packet_folio_html(
            packet_path,
            packet=packet,
            out_dir=page_out_dir,
            book_title=book_title,
            image_href=copied_images[packet.page_id],
            prev_href=prev_href,
            next_href=next_href,
            home_href="../../contents.html",
            include_cover=False,
        )
        folio_paths.append(artifact.html_path)
        page_entries.append(BookSitePageEntry(page_id=packet.page_id, href=f"folios/{packet.page_id}/{EDITION_HTML_FILENAME}"))

    title_body = render_book_cover_page(book_title=book_title, folio_count=len(page_entries))
    title_path.write_text(web_html_shell(title=book_title, body=title_body), encoding="utf-8")

    contents_body = render_book_contents_page(book_title=book_title, page_entries=page_entries)
    contents_path.write_text(
        web_html_shell(title=f"{book_title} - Contents", body=contents_body, extra_css=book_site_page_css()),
        encoding="utf-8",
    )

    ending_body = render_book_ending_page(book_title=book_title, folio_count=len(page_entries))
    ending_path.write_text(
        web_html_shell(title=f"{book_title} - End", body=ending_body, extra_css=book_site_page_css()),
        encoding="utf-8",
    )

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
