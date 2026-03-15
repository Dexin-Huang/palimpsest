from __future__ import annotations

import json
from pathlib import Path

from palimpsest.contracts import (
    box_cleanup_path,
    layout_probe_json_path,
    page_assembly_json_path,
    page_assembly_md_path,
    page_assembly_meta_path,
    page_validation_json_path,
    section_resolution_path,
)
from palimpsest.models.layout_probe import (
    LayoutProbe,
    PageAssembly,
    PageAssemblyUnit,
    PageBoxCleanup,
    PageSectionResolution,
    PageValidation,
)
from palimpsest.reconstruct.artifacts import PageAssemblyArtifact
from palimpsest.reconstruct.common import _utc_now
from palimpsest.reconstruct.region_reads import _load_region_reads


def _load_layout_probe(probe_dir: Path) -> LayoutProbe:
    return LayoutProbe.model_validate_json(layout_probe_json_path(probe_dir).read_text(encoding="utf-8"))


def _load_section_resolution(probe_dir: Path) -> PageSectionResolution | None:
    path = section_resolution_path(probe_dir)
    if not path.exists():
        return None
    try:
        return PageSectionResolution.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_box_cleanup(probe_dir: Path) -> PageBoxCleanup | None:
    path = box_cleanup_path(probe_dir)
    if not path.exists():
        return None
    try:
        return PageBoxCleanup.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_page_validation(probe_dir: Path) -> PageValidation | None:
    path = page_validation_json_path(probe_dir)
    if not path.exists():
        return None
    try:
        return PageValidation.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_page_assembly(probe_dir: Path) -> PageAssemblyArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_reads(probe_dir)}
    section_resolution = _load_section_resolution(probe_dir)
    box_cleanup = _load_box_cleanup(probe_dir)
    units: list[PageAssemblyUnit] = []
    counter = 1
    section_assignments = {
        item.region_id: item
        for item in (section_resolution.assignments if section_resolution is not None else [])
    }
    if box_cleanup is not None:
        for decision in box_cleanup.decisions:
            assignment_a = section_assignments.get(decision.region_a)
            assignment_b = section_assignments.get(decision.region_b)
            if assignment_a is not None:
                assignment_a.source_block = decision.cleaned_source_block_a
            if assignment_b is not None:
                assignment_b.source_block = decision.cleaned_source_block_b
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        assignment = section_assignments.get(region.region_id)
        source_block = assignment.source_block if assignment is not None else (read.source_block if read else None)
        units.append(
            PageAssemblyUnit(
                unit_id=f"u{counter:03d}",
                region_id=region.region_id,
                label=region.label,
                role=region.role,
                bbox_norm=region.bbox_norm,
                page_side=region.page_side,
                column_index=region.column_index,
                reading_order=region.reading_order,
                source_block=source_block,
                notes=(
                    list(assignment.notes)
                    if assignment is not None
                    else (list(read.notes) if read else ([region.notes] if region.notes else []))
                ),
            )
        )
        counter += 1

    assembly = PageAssembly(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        image_path=layout.image_path,
        page_unit=layout.page_unit,
        units=units,
    )

    assembly_json_path = page_assembly_json_path(probe_dir)
    assembly_md_path = page_assembly_md_path(probe_dir)
    assembly_json_path.write_text(assembly.model_dump_json(indent=2), encoding="utf-8")

    markdown_lines = [f"# Page Assembly: {assembly.page_id}", ""]
    for unit in assembly.units:
        markdown_lines.extend(
            [
                f"## {unit.label}",
                f"- Region: `{unit.region_id}`",
                f"- Role: `{unit.role}`",
                f"- BBox: `{list(unit.bbox_norm)}`",
            ]
        )
        markdown_lines.append("")
        if unit.source_block:
            markdown_lines.append("")
            markdown_lines.append("Source Block:")
            markdown_lines.append(unit.source_block)
        if unit.notes:
            markdown_lines.append("")
            markdown_lines.append("Notes:")
            for note in unit.notes:
                markdown_lines.append(f"- {note}")
        markdown_lines.append("")
    assembly_md_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    meta_path = page_assembly_meta_path(probe_dir)
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "assembly_json_path": str(assembly_json_path),
        "assembly_md_path": str(assembly_md_path),
        "section_resolution_path": str(section_resolution_path(probe_dir).resolve()) if section_resolution is not None else None,
        "box_cleanup_path": str(box_cleanup_path(probe_dir).resolve()) if box_cleanup is not None else None,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return PageAssemblyArtifact(
        probe_dir=probe_dir,
        assembly_json_path=assembly_json_path,
        assembly_md_path=assembly_md_path,
        meta_path=meta_path,
    )
