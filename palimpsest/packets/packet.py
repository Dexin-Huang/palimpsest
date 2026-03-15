from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Iterable

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.contracts import (
    IMAGE_PARENT_DIRNAMES,
    packet_file_path,
    packet_meta_path,
    packet_output_dir_for_image,
    packet_path as packet_json_path,
)
from palimpsest.models.layout_probe import PageAssembly
from palimpsest.models.packet import PacketContinuity, PacketFileRef, PagePacket, PacketWorkflow
from palimpsest.reconstruct.prepare import PreparedPageArtifact, prepare_image
from palimpsest.packets.templates import packet_markdown_template

from .contract import packet_artifact_contracts
from .state import load_packet_json, write_packet_json


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _resolve_doc_id(image_path: Path) -> str:
    image_path = image_path.resolve()
    if image_path.parent.name in IMAGE_PARENT_DIRNAMES:
        return image_path.parent.parent.name
    return image_path.parent.name


def _default_output_dir(image_path: Path) -> Path:
    return packet_output_dir_for_image(image_path)


def create_page_packet(
    image_path: Path,
    *,
    out_dir: Path | None = None,
    prepare: bool = True,
    previous_packet_path: Path | None = None,
    previous_handoff_path: Path | None = None,
    window_synthesis_path: Path | None = None,
    ) -> tuple[PagePacket, Path]:
    image_path = image_path.resolve()
    target_dir = (out_dir.resolve() if out_dir else _default_output_dir(image_path).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    with Image.open(image_path) as source_image:
        width, height = source_image.size
    page_unit = "spread" if width > (height * 1.1) else "page"

    prepared: PreparedPageArtifact | None = None
    if prepare:
        prepared = prepare_image(image_path, out_dir=target_dir / "prepared")

    witness_path = packet_file_path(target_dir, "witness")
    notes_path = packet_file_path(target_dir, "notes")
    translation_path = packet_file_path(target_dir, "translation")
    interpretation_path = packet_file_path(target_dir, "interpretation")
    terms_path = packet_file_path(target_dir, "terms")
    questions_path = packet_file_path(target_dir, "questions")
    artifact_contracts = packet_artifact_contracts(target_dir)

    if not witness_path.exists():
        witness_path.write_text(
            packet_markdown_template("witness", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    if not notes_path.exists():
        notes_path.write_text(
            packet_markdown_template("notes", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    if not translation_path.exists():
        translation_path.write_text(
            packet_markdown_template("translation", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    if not interpretation_path.exists():
        interpretation_path.write_text(
            packet_markdown_template("interpretation", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    if not terms_path.exists():
        terms_path.write_text(
            packet_markdown_template("terms", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    if not questions_path.exists():
        questions_path.write_text(
            packet_markdown_template("questions", page_id=image_path.stem, page_unit=page_unit),
            encoding="utf-8",
        )
    packet = PagePacket(
        created_at=_utc_now(),
        doc_id=_resolve_doc_id(image_path),
        page_id=image_path.stem,
        page_unit=page_unit,
        source_image_path=str(image_path),
        prepared_image_path=str(prepared.prepared_image_path) if prepared is not None else None,
        files={
            "witness": PacketFileRef(kind="witness", path=str(witness_path), status="empty"),
            "notes": PacketFileRef(kind="notes", path=str(notes_path), status="empty"),
            "translation": PacketFileRef(kind="translation", path=str(translation_path), status="empty"),
            "interpretation": PacketFileRef(kind="interpretation", path=str(interpretation_path), status="empty"),
            "terms": PacketFileRef(kind="terms", path=str(terms_path), status="empty"),
            "questions": PacketFileRef(kind="questions", path=str(questions_path), status="empty"),
            **{
                key: PacketFileRef(kind=key, path=str(contract.path), status="empty")
                for key, contract in artifact_contracts.items()
            },
        },
        continuity=PacketContinuity(
            previous_packet_path=str(previous_packet_path.resolve()) if previous_packet_path else None,
            previous_handoff_path=str(previous_handoff_path.resolve()) if previous_handoff_path else None,
            window_synthesis_path=str(window_synthesis_path.resolve()) if window_synthesis_path else None,
        ),
        workflow=PacketWorkflow(
            primary_reasoner="claude_agent_sdk",
            witness_model=DEFAULT_MODEL_READING,
            synthesis_model=DEFAULT_MODEL_READING,
            next_action="fill_witness",
        ),
        notes=[
            "This packet is the scholar-facing working bundle for one page unit.",
            "Fill witness first, then notes, then translation and interpretation.",
        ],
    )

    packet_path = packet_json_path(target_dir)
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    meta_path = packet_meta_path(target_dir)
    meta = {
        "generated_at": _utc_now(),
        "source_image_path": str(image_path),
        "packet_path": str(packet_path),
        "prepared_image_path": str(prepared.prepared_image_path) if prepared is not None else None,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return packet, packet_path


def attach_layout_probe(packet_path: Path, probe_dir: Path) -> PagePacket:
    packet_path = packet_path.resolve()
    probe_dir = probe_dir.resolve()
    packet = load_packet_json(packet_path)
    artifact_contracts = packet_artifact_contracts(packet_path.parent, probe_dir=probe_dir)
    for key in ("layout_probe", "layout_overlay", "region_reads", "section_resolution", "box_cleanup", "page_assembly"):
        contract = artifact_contracts[key]
        resolved_path = contract.path
        ref = packet.files.get(key)
        if ref is None:
            ref = PacketFileRef(kind=key, path=str(resolved_path), status="empty", note=contract.note)
            packet.files[key] = ref
        else:
            ref.path = str(resolved_path)
            if not ref.note:
                ref.note = contract.note
        if resolved_path.exists():
            ref.status = "draft"

    notes = list(packet.notes or [])
    note = "Layout probe attached."
    if note not in notes:
        notes.append(note)
    packet.notes = notes

    return write_packet_json(packet_path, packet)


def _parse_markdown_sections(text: str) -> tuple[str | None, dict[str, list[str]]]:
    title: str | None = None
    sections: dict[str, list[str]] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title is not None:
            sections[current_title] = current_lines[:]
        current_title = None
        current_lines = []

    for line in text.splitlines():
        if line.startswith("# ") and title is None:
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            continue
        if current_title is not None:
            current_lines.append(line)
    flush()
    return title, sections


def _extract_unit_label(section_title: str) -> str:
    match = re.match(r"^Reading Unit \d+\s*\((.+)\)$", section_title)
    if match:
        return match.group(1).strip()
    return section_title.strip()


def _iter_bullets(lines: Iterable[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            items.append(re.sub(r"^[-*]\s+", "", stripped))
    return items


def _load_packet(packet_path: Path) -> PagePacket:
    return load_packet_json(packet_path)


def _load_packet_assembly(packet: PagePacket, assembly_path: Path | None = None) -> PageAssembly:
    if assembly_path is None:
        assembly_ref = packet.files.get("page_assembly")
        if assembly_ref is None or not assembly_ref.path:
            raise FileNotFoundError(f"Packet {packet.page_id} is missing page_assembly metadata")
        assembly_path = Path(assembly_ref.path)
    assembly_path = assembly_path.resolve()
    if not assembly_path.exists():
        raise FileNotFoundError(f"Missing page_assembly.json: {assembly_path}")
    return PageAssembly.model_validate_json(assembly_path.read_text(encoding="utf-8"))


def _sorted_assembly_units(assembly: PageAssembly):
    return sorted(
        assembly.units,
        key=lambda unit: (
            9999 if unit.reading_order is None else unit.reading_order,
            unit.page_side or "",
            9999 if unit.column_index is None else unit.column_index,
            unit.unit_id,
        ),
    )


def _unit_key(unit) -> tuple[str | None, int | None]:
    return (unit.page_side, unit.column_index)


def _normalize_block(text: str | None) -> str:
    if not text:
        return ""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _pick_marginalia_for_unit(main_unit, marginalia_by_side):
    candidates = list(marginalia_by_side.get(main_unit.page_side or "", []))
    if not candidates:
        return []
    return candidates


def sync_packet_from_assembly(packet_path: Path, assembly_path: Path | None = None) -> PagePacket:
    packet_path = packet_path.resolve()
    packet = _load_packet(packet_path)
    assembly = _load_packet_assembly(packet, assembly_path=assembly_path)

    ordered_units = _sorted_assembly_units(assembly)
    headers: dict[tuple[str | None, int | None], str] = {}
    page_numbers: dict[tuple[str | None, int | None], str] = {}
    marginalia_by_side: dict[str, list] = {}
    main_units: list = []

    for unit in ordered_units:
        text = _normalize_block(unit.source_block)
        if unit.role == "header":
            headers[_unit_key(unit)] = text
            continue
        if unit.role == "page_number":
            page_numbers[_unit_key(unit)] = text
            continue
        if unit.role == "marginalia":
            marginalia_by_side.setdefault(unit.page_side or "", []).append(unit)
            continue
        if unit.role == "main_text":
            main_units.append(unit)

    witness_parts = [f"# Witness: {packet.page_id}", ""]
    translation_parts = ["# Working Translation", ""]
    notes_parts = [
        "# Notes",
        "",
        "## Layout",
        f"- Page unit: {assembly.page_unit}",
        "- Synced deterministically from page_assembly.json.",
    ]

    if not main_units:
        witness_parts.extend(
            [
                "## Main Witness",
                "**Header**:",
                "**Page Number**:",
                "**Main Text**",
                "",
                "## Layout Notes",
                "- No witness-bearing content units in page_assembly.json.",
                "",
            ]
        )
        notes_parts.extend(
            [
                "- Non-content or empty page assembly.",
                "",
                "## Text Structure",
                "",
                "## Citations And Allusions",
                "",
                "## Marginalia And Non-Main Text",
                "",
                "## Uncertainty Markers",
                "",
            ]
        )
        witness_ref = packet.files["witness"]
        Path(witness_ref.path).write_text("\n".join(witness_parts), encoding="utf-8")
        witness_ref.status = "draft"
        packet.workflow.next_action = "complete"
        packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return packet

    for main_unit in main_units:
        title = main_unit.label
        key = _unit_key(main_unit)
        header_text = headers.get(key, "")
        page_number_text = page_numbers.get(key, "")
        main_text = _normalize_block(main_unit.source_block)
        marginalia_units = _pick_marginalia_for_unit(main_unit, marginalia_by_side)

        witness_parts.append(f"## {title}")
        witness_parts.append(f"**Header**: {header_text}" if header_text else "**Header**:")
        witness_parts.append(f"**Page Number**: {page_number_text}" if page_number_text else "**Page Number**:")
        if marginalia_units:
            position = f"{main_unit.page_side or 'page'} margin"
            marginalia_text = "\n\n".join(
                block for block in (_normalize_block(unit.source_block) for unit in marginalia_units) if block
            )
            witness_parts.extend(
                [
                    f"**Marginalia** (unknown, {position}):",
                    "```",
                    marginalia_text,
                    "```",
                ]
            )
        else:
            witness_parts.append("**Marginalia** (script, position):")
            witness_parts.extend(["```", "```"])
        witness_parts.append("**Main Text**")
        if main_text:
            witness_parts.extend(main_text.splitlines())
        witness_parts.append("")

        translation_parts.extend(
            [
                f"## {title}: [English Header]",
                "**Main Text**",
                "",
            ]
        )

    witness_parts.extend(
        [
            "## Layout Notes",
            f"- Page unit: {assembly.page_unit}",
            f"- Canonical witness units: {', '.join(unit.label for unit in main_units)}",
            "",
        ]
    )
    translation_parts.extend(
        [
            "## Translation Notes",
            "",
            "## Interpretive Restraint",
            "",
        ]
    )
    notes_parts.extend(
        [
            "",
            "## Text Structure",
            *[f"- Main unit: {unit.label}" for unit in main_units],
            "",
            "## Citations And Allusions",
            "",
            "## Marginalia And Non-Main Text",
            *[
                f"- {unit.label} on {unit.page_side or 'page'} side"
                for units in marginalia_by_side.values()
                for unit in units
            ],
            "",
            "## Uncertainty Markers",
            "",
        ]
    )

    witness_path = Path(packet.files["witness"].path)
    witness_path.write_text("\n".join(witness_parts), encoding="utf-8")
    packet.files["witness"].status = "draft"

    translation_ref = packet.files.get("translation")
    if translation_ref is not None and translation_ref.status in {"empty", "started"}:
        Path(translation_ref.path).write_text("\n".join(translation_parts), encoding="utf-8")
        translation_ref.status = "started"

    notes_ref = packet.files.get("notes")
    if notes_ref is not None and notes_ref.status in {"empty", "started"}:
        Path(notes_ref.path).write_text("\n".join(notes_parts), encoding="utf-8")
        notes_ref.status = "started"

    terms_ref = packet.files.get("terms")
    if terms_ref is not None and terms_ref.status in {"empty", "started"}:
        Path(terms_ref.path).write_text(packet_markdown_template("terms", page_id=packet.page_id, page_unit=packet.page_unit), encoding="utf-8")
        terms_ref.status = "started"

    questions_ref = packet.files.get("questions")
    if questions_ref is not None and questions_ref.status in {"empty", "started"}:
        Path(questions_ref.path).write_text(packet_markdown_template("questions", page_id=packet.page_id, page_unit=packet.page_unit), encoding="utf-8")
        questions_ref.status = "started"

    packet.workflow.next_action = "fill_notes"
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def ingest_page_reading(packet_path: Path, reading_path: Path) -> PagePacket:
    packet_path = packet_path.resolve()
    reading_path = reading_path.resolve()

    packet = PagePacket.model_validate_json(packet_path.read_text(encoding="utf-8"))
    reading_text = reading_path.read_text(encoding="utf-8")
    _, sections = _parse_markdown_sections(reading_text)

    witness_units = [title for title in sections if title.startswith("Reading Unit")]
    layout_lines = sections.get("Layout Notes", [])
    terms_lines = sections.get("Visible Names And Terms", [])
    uncertainty_lines = sections.get("Uncertainties", [])

    witness_parts = [f"# Witness: {packet.page_id}", ""]
    for title in witness_units:
        label = _extract_unit_label(title)
        witness_parts.append(f"## {label}")
        for raw_line in sections[title]:
            stripped = raw_line.strip()
            if stripped.startswith("- **Header**:"):
                witness_parts.append(stripped[2:])
            elif stripped.startswith("- **Page Number**:"):
                witness_parts.append(stripped[2:])
            elif stripped.startswith("- **Marginalia**"):
                witness_parts.append(stripped[2:])
            elif stripped.startswith("- **Main Text**"):
                witness_parts.append(stripped[2:])
            else:
                witness_parts.append(raw_line)
        witness_parts.append("")
    witness_parts.append("## Layout Notes")
    witness_parts.extend(layout_lines)
    witness_parts.append("")

    translation_parts = ["# Working Translation", ""]
    for title in witness_units:
        label = _extract_unit_label(title)
        translation_parts.extend(
            [
                f"## {label}: [English Header]",
                "**Main Text**",
                "",
            ]
        )
    translation_parts.extend(
        [
            "## Translation Notes",
            "",
            "## Interpretive Restraint",
            "",
        ]
    )

    notes_parts = [
        "# Notes",
        "",
        "## Layout",
        *layout_lines,
        "",
        "## Text Structure",
        "",
        "## Citations And Allusions",
        "",
        "## Marginalia And Non-Main Text",
        "",
        "## Uncertainty Markers",
        *uncertainty_lines,
        "",
    ]

    terms_parts = [
        "# Names And Terms",
        "",
        "## Technical Terms",
        *terms_lines,
        "",
    ]

    questions_parts = [
        "# Open Questions",
        "",
        "## Witness Uncertainties",
        *uncertainty_lines,
        "",
        "## Cross-Page Checks",
        "",
        "## Research Follow-Ups",
        "",
    ]

    file_paths = {
        "witness": Path(packet.files["witness"].path),
        "translation": Path(packet.files["translation"].path),
        "notes": Path(packet.files["notes"].path),
        "terms": Path(packet.files["terms"].path),
        "questions": Path(packet.files["questions"].path),
    }
    file_paths["witness"].write_text("\n".join(witness_parts), encoding="utf-8")
    file_paths["translation"].write_text("\n".join(translation_parts), encoding="utf-8")
    file_paths["notes"].write_text("\n".join(notes_parts), encoding="utf-8")
    file_paths["terms"].write_text("\n".join(terms_parts), encoding="utf-8")
    file_paths["questions"].write_text("\n".join(questions_parts), encoding="utf-8")

    for key in ("witness", "translation", "notes", "terms", "questions"):
        packet.files[key].status = "draft"
    packet.workflow.next_action = "draft_interpretation"

    meta_path = reading_path.parent / "reading_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            packet.workflow.witness_model = str(meta.get("model") or packet.workflow.witness_model or "")
        except Exception:
            pass

    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet
