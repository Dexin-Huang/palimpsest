from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from palimpsest.contracts import layout_probe_dir, packet_path as packet_json_path
from palimpsest.models.packet import PagePacket
from palimpsest.packets.packet import attach_layout_probe, create_page_packet
from palimpsest.packets.state import reconcile_packet_json
from palimpsest.reconstruct import (
    BoxCleanupArtifact,
    PageAssemblyArtifact,
    PageLayoutProbeArtifact,
    PageValidationArtifact,
    RegionReadsArtifact,
    SectionResolutionArtifact,
    run_box_cleanup,
    run_page_assembly,
    run_page_layout_probe,
    run_page_validate,
    run_region_reads,
    run_section_resolution,
)


@dataclass
class LayoutPipelineResult:
    probe_artifact: PageLayoutProbeArtifact
    region_artifact: RegionReadsArtifact | None
    section_artifact: SectionResolutionArtifact | None
    validation_artifact: PageValidationArtifact | None
    box_cleanup_artifact: BoxCleanupArtifact | None
    assembly_artifact: PageAssemblyArtifact | None


@dataclass
class PacketDecodeResult:
    created: bool
    packet_path: Path
    packet: PagePacket
    layout: LayoutPipelineResult


def run_layout_pipeline(
    *,
    image_path: Path,
    probe_dir: Path,
    layout_model: str,
    region_model: str,
    cleanup_model: str,
    run_regions: bool,
    run_section_resolution_stage: bool,
    run_box_cleanup_stage: bool,
    run_assembly: bool,
    retries: int = 2,
) -> LayoutPipelineResult:
    probe_artifact = None
    region_artifact = None
    section_artifact = None
    validation_artifact = None
    box_cleanup_artifact = None
    assembly_artifact = None

    def retry(stage_name: str, fn):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                if attempt == 0:
                    print(f"{stage_name}: running", flush=True)
                return fn()
            except Exception as exc:  # pragma: no cover - defensive runtime retry path
                last_exc = exc
                if attempt >= retries:
                    raise
                print(f"{stage_name}: retry {attempt + 1}/{retries} after {exc.__class__.__name__}", flush=True)
                time.sleep(min(2 + attempt, 5))
        raise last_exc  # pragma: no cover

    probe_artifact = retry(
        "layout_probe",
        lambda: run_page_layout_probe(
            image_path,
            out_dir=probe_dir,
            model=layout_model,
            orient_model=region_model,
            orient_regions=False,
        ),
    )

    if run_regions:
        region_artifact = retry(
            "region_read",
            lambda: run_region_reads(
                probe_artifact.output_dir,
                model=region_model,
            ),
        )

    if run_section_resolution_stage:
        section_artifact = retry(
            "section_resolution",
            lambda: run_section_resolution(
                probe_artifact.output_dir,
                model=cleanup_model,
            ),
        )
        # Validation operates on page assembly units, so build a provisional
        # assembly from the section-resolved boxes before targeted cleanup.
        assembly_artifact = run_page_assembly(probe_artifact.output_dir)
        validation_artifact = run_page_validate(probe_artifact.output_dir)

    if run_box_cleanup_stage:
        box_cleanup_artifact = retry(
            "box_cleanup",
            lambda: run_box_cleanup(
                probe_artifact.output_dir,
                model=cleanup_model,
            ),
        )

    if run_assembly:
        assembly_artifact = run_page_assembly(probe_artifact.output_dir)

    return LayoutPipelineResult(
        probe_artifact=probe_artifact,
        region_artifact=region_artifact,
        section_artifact=section_artifact,
        validation_artifact=validation_artifact,
        box_cleanup_artifact=box_cleanup_artifact,
        assembly_artifact=assembly_artifact,
    )


def run_packet_decode(
    *,
    image_path: Path,
    packet_dir: Path,
    raw: bool,
    layout_model: str,
    region_model: str,
    cleanup_model: str,
    retries: int,
) -> PacketDecodeResult:
    packet_path = packet_json_path(packet_dir)
    created = False
    if packet_path.exists():
        packet = reconcile_packet_json(packet_path)
    else:
        packet, packet_path = create_page_packet(
            image_path,
            out_dir=packet_dir,
            prepare=not raw,
        )
        created = True

    layout = run_layout_pipeline(
        image_path=image_path,
        probe_dir=layout_probe_dir(packet_dir),
        layout_model=layout_model,
        region_model=region_model,
        cleanup_model=cleanup_model,
        run_regions=True,
        run_section_resolution_stage=True,
        run_box_cleanup_stage=True,
        run_assembly=True,
        retries=retries,
    )
    packet = attach_layout_probe(packet_path, layout.probe_artifact.output_dir)

    return PacketDecodeResult(
        created=created,
        packet_path=packet_path,
        packet=packet,
        layout=layout,
    )


__all__ = [
    "LayoutPipelineResult",
    "PacketDecodeResult",
    "run_layout_pipeline",
    "run_packet_decode",
]
