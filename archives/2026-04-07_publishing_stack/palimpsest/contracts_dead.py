"""Archived constants and helpers from the original ``palimpsest/contracts.py``.

Retired 2026-04-07 as part of the publishing-stack cleanup. Every name in this
module existed solely to serve the packet/reader/web layer that has been moved
under ``archives/2026-04-07_publishing_stack/``. None of these symbols have a
live caller in the trimmed ``palimpsest/`` tree.

This file is preserved verbatim so the historical packet/render/layout pipeline
can still be reconstructed if needed.
"""

from __future__ import annotations

from pathlib import Path


IMAGES_DIRNAME = "images"
CLEANED_IMAGES_DIRNAME = "images_cleaned"
EXPERIMENTS_DIRNAME = "experiments"

PACKET_WORKSPACE_VERSION = 1
PACKET_FILENAME = "packet.json"
PACKET_META_FILENAME = "packet_meta.json"
WITNESS_FILENAME = "witness.md"
NOTES_FILENAME = "notes.md"
TRANSLATION_FILENAME = "translation.md"
INTERPRETATION_FILENAME = "interpretation.md"
TERMS_FILENAME = "terms.md"
QUESTIONS_FILENAME = "questions.md"
EDITION_HTML_FILENAME = "index.html"
FOLIO_RENDER_FILENAME = "render.json"
RENDER_META_FILENAME = "render_meta.json"

PREPARED_DIR_SUFFIX = "_prepared"
PREPARE_META_FILENAME = "prepare_meta.json"
PREPARED_IMAGE_TEMPLATE = "{stem}_prepared.jpg"
READING_DIR_SUFFIX = "_reading"
READING_OUTPUT_TEMPLATE = "{stem}_reading.md"
READING_META_FILENAME = "reading_meta.json"
PROMPT_COPY_FILENAME = "prompt.txt"
SECTION_SYNTHESIS_DIRNAME = "section_synthesis"
SECTION_SYNTHESIS_FILENAME = "section_synthesis.md"
SECTION_SYNTHESIS_META_FILENAME = "section_synthesis_meta.json"
SECTION_SYNTHESIS_INPUTS_FILENAME = "inputs.md"

LAYOUT_PROBE_DIR_SUFFIX = "_layout_probe"
LAYOUT_PROBE_DIRNAME = "layout_probe"
LAYOUT_PROBE_FILENAME = "layout_probe.json"
LAYOUT_OVERLAY_FILENAME = "layout_overlay.png"
LAYOUT_PROMPT_COPY_FILENAME = "layout_prompt.txt"
LAYOUT_PROBE_RAW_RESPONSE_FILENAME = "layout_probe_raw.json"
LAYOUT_PROBE_META_FILENAME = "layout_probe_meta.json"
LAYOUT_CROPS_DIRNAME = "crops"
REGION_READS_FILENAME = "region_reads.json"
REGION_READS_META_FILENAME = "region_reads_meta.json"
SECTION_RESOLUTION_FILENAME = "section_resolution.json"
SECTION_RESOLUTION_META_FILENAME = "section_resolution_meta.json"
BOX_CLEANUP_FILENAME = "box_cleanup.json"
BOX_CLEANUP_META_FILENAME = "box_cleanup_meta.json"
PAGE_VALIDATION_JSON_FILENAME = "page_validation.json"
PAGE_VALIDATION_MD_FILENAME = "page_validation.md"
PAGE_VALIDATION_META_FILENAME = "page_validation_meta.json"
PAGE_ASSEMBLY_JSON_FILENAME = "page_assembly.json"
PAGE_ASSEMBLY_MD_FILENAME = "page_assembly.md"
PAGE_ASSEMBLY_META_FILENAME = "page_assembly_meta.json"
VISUAL_PAIR_REPAIRS_DIRNAME = "visual_pair_repairs"

IMAGE_PARENT_DIRNAMES = {IMAGES_DIRNAME, CLEANED_IMAGES_DIRNAME}

PACKET_MARKDOWN_FILENAMES = {
    "witness": WITNESS_FILENAME,
    "notes": NOTES_FILENAME,
    "translation": TRANSLATION_FILENAME,
    "interpretation": INTERPRETATION_FILENAME,
    "terms": TERMS_FILENAME,
    "questions": QUESTIONS_FILENAME,
}


def packet_workspace_name(page_id: str, *, version: int = PACKET_WORKSPACE_VERSION) -> str:
    return f"{page_id}_packet_v{version}"


def packet_workspace_dir(doc_dir: Path, page_id: str, *, version: int = PACKET_WORKSPACE_VERSION) -> Path:
    return Path(doc_dir) / EXPERIMENTS_DIRNAME / packet_workspace_name(page_id, version=version)


def packet_output_dir_for_image(image_path: Path, *, version: int = PACKET_WORKSPACE_VERSION) -> Path:
    image_path = Path(image_path).resolve()
    if image_path.parent.name in IMAGE_PARENT_DIRNAMES:
        return packet_workspace_dir(image_path.parent.parent, image_path.stem, version=version)
    return image_path.parent / packet_workspace_name(image_path.stem, version=version)


def packet_path(workspace_dir: Path) -> Path:
    return Path(workspace_dir) / PACKET_FILENAME


def packet_meta_path(workspace_dir: Path) -> Path:
    return Path(workspace_dir) / PACKET_META_FILENAME


def packet_file_path(workspace_dir: Path, kind: str) -> Path:
    if kind not in PACKET_MARKDOWN_FILENAMES:
        raise ValueError(f"Unknown packet file kind: {kind}")
    return Path(workspace_dir) / PACKET_MARKDOWN_FILENAMES[kind]


def render_html_path(target_dir: Path) -> Path:
    return Path(target_dir) / EDITION_HTML_FILENAME


def folio_render_path(target_dir: Path) -> Path:
    return Path(target_dir) / FOLIO_RENDER_FILENAME


def render_meta_path(target_dir: Path) -> Path:
    return Path(target_dir) / RENDER_META_FILENAME


def experiment_output_dir(image_path: Path, *, suffix: str) -> Path:
    image_path = Path(image_path).resolve()
    if image_path.parent.name in IMAGE_PARENT_DIRNAMES:
        return image_path.parent.parent / EXPERIMENTS_DIRNAME / f"{image_path.stem}{suffix}"
    return image_path.parent / f"{image_path.stem}{suffix}"


def prepare_output_dir(image_path: Path) -> Path:
    return experiment_output_dir(image_path, suffix=PREPARED_DIR_SUFFIX)


def prepared_image_path(target_dir: Path, image_path: Path) -> Path:
    return Path(target_dir) / PREPARED_IMAGE_TEMPLATE.format(stem=Path(image_path).stem)


def prepare_meta_path(target_dir: Path) -> Path:
    return Path(target_dir) / PREPARE_META_FILENAME


def reading_output_dir(image_path: Path) -> Path:
    return experiment_output_dir(image_path, suffix=READING_DIR_SUFFIX)


def reading_output_path(target_dir: Path, image_path: Path) -> Path:
    return Path(target_dir) / READING_OUTPUT_TEMPLATE.format(stem=Path(image_path).stem)


def reading_meta_path(target_dir: Path) -> Path:
    return Path(target_dir) / READING_META_FILENAME


def prompt_copy_path(target_dir: Path) -> Path:
    return Path(target_dir) / PROMPT_COPY_FILENAME


def section_synthesis_output_dir(input_paths: list[Path]) -> Path:
    if not input_paths:
        raise ValueError("At least one input path is required")
    first = Path(input_paths[0]).resolve()
    parent_name = first.parent.name
    if parent_name.endswith("_reading") or parent_name.endswith("_witness") or "_reading" in parent_name or "_witness" in parent_name:
        return first.parent.parent / SECTION_SYNTHESIS_DIRNAME
    return first.parent / SECTION_SYNTHESIS_DIRNAME


def section_synthesis_output_path(target_dir: Path) -> Path:
    return Path(target_dir) / SECTION_SYNTHESIS_FILENAME


def section_synthesis_meta_path(target_dir: Path) -> Path:
    return Path(target_dir) / SECTION_SYNTHESIS_META_FILENAME


def section_synthesis_inputs_path(target_dir: Path) -> Path:
    return Path(target_dir) / SECTION_SYNTHESIS_INPUTS_FILENAME


def layout_probe_output_dir(image_path: Path) -> Path:
    return experiment_output_dir(image_path, suffix=LAYOUT_PROBE_DIR_SUFFIX)


def layout_probe_dir(packet_or_probe_dir: Path) -> Path:
    path = Path(packet_or_probe_dir)
    if path.name == LAYOUT_PROBE_DIRNAME:
        return path
    return path / LAYOUT_PROBE_DIRNAME


def layout_probe_json_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_PROBE_FILENAME


def layout_overlay_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_OVERLAY_FILENAME


def layout_prompt_copy_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_PROMPT_COPY_FILENAME


def layout_probe_raw_response_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_PROBE_RAW_RESPONSE_FILENAME


def layout_probe_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_PROBE_META_FILENAME


def layout_crops_dir(probe_dir: Path) -> Path:
    return Path(probe_dir) / LAYOUT_CROPS_DIRNAME


def region_reads_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / REGION_READS_FILENAME


def region_reads_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / REGION_READS_META_FILENAME


def section_resolution_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / SECTION_RESOLUTION_FILENAME


def section_resolution_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / SECTION_RESOLUTION_META_FILENAME


def box_cleanup_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / BOX_CLEANUP_FILENAME


def box_cleanup_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / BOX_CLEANUP_META_FILENAME


def page_validation_json_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_VALIDATION_JSON_FILENAME


def page_validation_md_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_VALIDATION_MD_FILENAME


def page_validation_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_VALIDATION_META_FILENAME


def page_assembly_json_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_ASSEMBLY_JSON_FILENAME


def page_assembly_md_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_ASSEMBLY_MD_FILENAME


def page_assembly_meta_path(probe_dir: Path) -> Path:
    return Path(probe_dir) / PAGE_ASSEMBLY_META_FILENAME


def visual_pair_repairs_dir(probe_dir: Path) -> Path:
    return Path(probe_dir) / VISUAL_PAIR_REPAIRS_DIRNAME
