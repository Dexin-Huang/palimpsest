from __future__ import annotations

from pathlib import Path

from palimpsest.model_io import resolve_prompt_text, response_text
from palimpsest.reconstruct import assembly as assembly_mod
from palimpsest.reconstruct import common as common_mod
from palimpsest.reconstruct import pipeline as pipeline_mod
from palimpsest.reconstruct import region_reads as region_reads_mod


DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS = common_mod.DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS


def utc_now() -> str:
    return common_mod._utc_now()


def coerce_json_text(text: str) -> str:
    return common_mod._coerce_json_text(text)


def load_layout_probe(probe_dir: Path):
    return assembly_mod._load_layout_probe(probe_dir)


def load_section_resolution(probe_dir: Path):
    return assembly_mod._load_section_resolution(probe_dir)


def load_box_cleanup(probe_dir: Path):
    return assembly_mod._load_box_cleanup(probe_dir)


def load_page_validation(probe_dir: Path):
    return assembly_mod._load_page_validation(probe_dir)


def load_region_reads(probe_dir: Path):
    return region_reads_mod._load_region_reads(probe_dir)


def region_crop_path(probe_dir: Path, region_id: str):
    return region_reads_mod._region_crop_path(probe_dir, region_id)


def bbox_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    return pipeline_mod._bbox_overlap_ratio(a, b)


def line_similarity(a: str, b: str) -> float:
    return pipeline_mod._line_similarity(a, b)


def draw_pair_overlay(
    image_path: Path,
    *,
    bbox_a: tuple[float, float, float, float],
    bbox_b: tuple[float, float, float, float],
    label_a: str,
    label_b: str,
    out_path: Path,
) -> None:
    pipeline_mod._draw_pair_overlay(
        image_path,
        bbox_a=bbox_a,
        bbox_b=bbox_b,
        label_a=label_a,
        label_b=label_b,
        out_path=out_path,
    )


__all__ = [
    "DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS",
    "bbox_overlap_ratio",
    "coerce_json_text",
    "draw_pair_overlay",
    "line_similarity",
    "load_box_cleanup",
    "load_layout_probe",
    "load_page_validation",
    "load_region_reads",
    "load_section_resolution",
    "region_crop_path",
    "resolve_prompt_text",
    "response_text",
    "utc_now",
]
