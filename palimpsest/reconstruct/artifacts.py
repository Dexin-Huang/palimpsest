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
    orientations_path: Path
    meta_path: Path
    model: str
    orientation_model: str
    finish_reason: str | None


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
class BlobRefinementArtifact:
    probe_dir: Path
    blob_json_path: Path
    blob_overlay_path: Path
    refined_crops_dir: Path
    meta_path: Path


@dataclass
class VisualPairRepairArtifact:
    probe_dir: Path
    decision_json_path: Path
    overlay_path: Path
    meta_path: Path
    model: str
