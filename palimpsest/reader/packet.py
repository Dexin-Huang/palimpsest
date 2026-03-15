from pathlib import Path

from palimpsest.packets.state import record_packet_render_outputs

from .folio import RenderedPacketHtmlArtifact, render_packet_folio_html
from .site import RenderedPacketSiteArtifact, build_packet_book_site


def render_packet_workspace(
    packet_path: Path,
    *,
    out_dir: Path | None = None,
    book_title: str | None = None,
    image_href: str | None = None,
    prev_href: str | None = None,
    next_href: str | None = None,
    home_href: str | None = None,
    include_cover: bool = True,
) -> RenderedPacketHtmlArtifact:
    packet_path = Path(packet_path).resolve()
    packet_dir = packet_path.parent
    target_dir = out_dir.resolve() if out_dir else packet_dir

    artifact = render_packet_folio_html(
        packet_path,
        out_dir=target_dir,
        book_title=book_title,
        image_href=image_href,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        include_cover=include_cover,
    )

    if target_dir == packet_dir:
        record_packet_render_outputs(
            packet_path,
            edition_html_path=artifact.html_path,
            folio_render_path=artifact.folio_render_path,
        )

    return artifact


__all__ = [
    "RenderedPacketHtmlArtifact",
    "RenderedPacketSiteArtifact",
    "build_packet_book_site",
    "render_packet_folio_html",
    "render_packet_workspace",
]
