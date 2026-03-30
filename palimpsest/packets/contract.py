from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from palimpsest.contracts import (
    folio_render_path,
    render_html_path,
)


@dataclass(frozen=True)
class PacketArtifactContract:
    path: Path
    note: str


def packet_artifact_contracts(
    packet_dir: Path,
    *,
    probe_dir: Path | None = None,
) -> dict[str, PacketArtifactContract]:
    packet_dir = packet_dir.resolve()
    return {
        "edition_html": PacketArtifactContract(
            path=render_html_path(packet_dir).resolve(),
            note="Rendered HTML folio edition",
        ),
        "folio_render": PacketArtifactContract(
            path=folio_render_path(packet_dir).resolve(),
            note="Structured folio.render JSON artifact",
        ),
    }
