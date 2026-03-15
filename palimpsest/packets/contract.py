from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from palimpsest.contracts import (
    box_cleanup_path,
    folio_render_path,
    layout_overlay_path,
    layout_probe_dir,
    layout_probe_json_path,
    page_assembly_json_path,
    region_reads_path,
    render_html_path,
    section_resolution_path,
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
    resolved_probe_dir = layout_probe_dir(probe_dir.resolve()) if probe_dir is not None else layout_probe_dir(packet_dir)
    return {
        "edition_html": PacketArtifactContract(
            path=render_html_path(packet_dir).resolve(),
            note="Rendered HTML folio edition",
        ),
        "folio_render": PacketArtifactContract(
            path=folio_render_path(packet_dir).resolve(),
            note="Structured folio.render JSON artifact",
        ),
        "layout_probe": PacketArtifactContract(
            path=layout_probe_json_path(resolved_probe_dir).resolve(),
            note="Coarse layout probe for region-first reconstruction",
        ),
        "layout_overlay": PacketArtifactContract(
            path=layout_overlay_path(resolved_probe_dir).resolve(),
            note="Overlay preview of coarse layout regions",
        ),
        "region_reads": PacketArtifactContract(
            path=region_reads_path(resolved_probe_dir).resolve(),
            note="Full transcription reads for each coarse region",
        ),
        "section_resolution": PacketArtifactContract(
            path=section_resolution_path(resolved_probe_dir).resolve(),
            note="Canonical text ownership per coarse region",
        ),
        "box_cleanup": PacketArtifactContract(
            path=box_cleanup_path(resolved_probe_dir).resolve(),
            note="Targeted cleanup for overlapping region pairs",
        ),
        "page_assembly": PacketArtifactContract(
            path=page_assembly_json_path(resolved_probe_dir).resolve(),
            note="Deterministic assembly from region reads",
        ),
    }
