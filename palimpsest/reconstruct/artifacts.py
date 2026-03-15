from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageLayoutProbeArtifact:
    image_path: Path
    output_dir: Path
    prompt_path: Path
    layout_json_path: Path
    overlay_path: Path
    crops_dir: Path
    region_reads_path: Path
    meta_path: Path
    model: str
    region_read_model: str
    finish_reason: str | None

    @property
    def orientations_path(self) -> Path:
        return self.region_reads_path

    @property
    def orientation_model(self) -> str:
        return self.region_read_model


@dataclass
class RegionReadsArtifact:
    probe_dir: Path
    reads_path: Path
    meta_path: Path
    model: str


@dataclass
class PageAssemblyArtifact:
    probe_dir: Path
    assembly_json_path: Path
    assembly_md_path: Path
    meta_path: Path


@dataclass
class SectionResolutionArtifact:
    probe_dir: Path
    resolution_json_path: Path
    meta_path: Path
    model: str


@dataclass
class BoxCleanupArtifact:
    probe_dir: Path
    cleanup_json_path: Path
    meta_path: Path
    model: str


@dataclass
class PageValidationArtifact:
    probe_dir: Path
    validation_json_path: Path
    validation_md_path: Path
    meta_path: Path


@dataclass
class VisualPairRepairArtifact:
    probe_dir: Path
    decision_json_path: Path
    overlay_path: Path
    meta_path: Path
    model: str
