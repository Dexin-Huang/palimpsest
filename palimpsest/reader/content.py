from __future__ import annotations

import re
from pathlib import Path

from palimpsest.models.folio_render import (
    ColumnWitness,
    FolioRenderImageRegion,
    InterpretationBlock,
    InterpretationContent,
    MarginaliaEntry,
    NoteBlock,
    QuestionEntry,
    SentencePair,
    TermEntry,
    WitnessContent,
)
from palimpsest.models.layout_probe import PageAssembly
from palimpsest.models.packet import PagePacket
from palimpsest.web.markup import MarkdownDocument, MarkdownSection


_TERM_RE = re.compile(r"\*\*(.+?)\*\*\s*(?:\(([^)]+)\))?\s*[:\-â€”]\s*(.+)")


def load_page_assembly(packet: PagePacket) -> PageAssembly:
    assembly_ref = packet.files.get("page_assembly")
    if assembly_ref is None or not getattr(assembly_ref, "path", None):
        raise FileNotFoundError(f"Packet {packet.page_id} is missing page_assembly metadata")
    assembly_path = Path(assembly_ref.path).resolve()
    if not assembly_path.exists():
        raise FileNotFoundError(f"Packet {packet.page_id} is missing page_assembly.json at {assembly_path}")
    return PageAssembly.model_validate_json(assembly_path.read_text(encoding="utf-8"))


def build_witness_content_from_assembly(assembly: PageAssembly) -> WitnessContent:
    def _pairs_from_unit(unit) -> list[SentencePair]:
        lines = [line.strip() for line in (unit.source_block or "").splitlines() if line.strip()]
        return [
            SentencePair(
                source=line,
                translation="",
                unit_id=unit.unit_id,
                region_id=unit.region_id,
                bbox_norm=unit.bbox_norm,
            )
            for line in lines
        ]

    ordered_units = sorted(
        assembly.units,
        key=lambda unit: (
            9999 if unit.reading_order is None else unit.reading_order,
            unit.page_side or "",
            9999 if unit.column_index is None else unit.column_index,
            unit.unit_id,
        ),
    )

    header_map: dict[tuple[str | None, int | None], tuple[str, str]] = {}
    marginalia: list[MarginaliaEntry] = []
    columns: list[ColumnWitness] = []

    for unit in ordered_units:
        key = (unit.page_side, unit.column_index)
        pairs = _pairs_from_unit(unit)

        if unit.role == "header":
            header_lines = [pair.source.strip() for pair in pairs if pair.source.strip()]
            header_text = "\n".join(header_lines) if header_lines else (unit.source_block or unit.label)
            header_map[key] = (header_text, "")
            continue

        if unit.role == "page_number":
            continue

        if unit.role == "marginalia":
            marginalia_text = unit.source_block or "\n".join(pair.source for pair in pairs if pair.source.strip())
            if marginalia_text.strip():
                marginalia.append(
                    MarginaliaEntry(
                        script="unknown",
                        position=f"{unit.page_side or 'page'} margin",
                        text=marginalia_text,
                        note=unit.label,
                    )
                )
            continue

        header_zh, header_en = header_map.get(key, ("", ""))
        if not header_zh:
            header_zh = unit.label

        columns.append(
            ColumnWitness(
                header_zh=header_zh,
                header_en=header_en,
                unit_id=unit.unit_id,
                region_id=unit.region_id,
                role=unit.role,
                bbox_norm=unit.bbox_norm,
                page_side=unit.page_side,
                pairs=pairs or [
                    SentencePair(
                        source=line,
                        translation="",
                        unit_id=unit.unit_id,
                        region_id=unit.region_id,
                        bbox_norm=unit.bbox_norm,
                    )
                    for line in unit.diplomatic_lines
                    if line.strip()
                ],
            )
        )

    return WitnessContent(columns=columns, marginalia=marginalia)


def build_image_regions_from_assembly(assembly: PageAssembly) -> list[FolioRenderImageRegion]:
    return [
        FolioRenderImageRegion(
            region_id=unit.region_id,
            unit_id=unit.unit_id,
            label=unit.label,
            role=unit.role,
            bbox_norm=unit.bbox_norm,
            page_side=unit.page_side,
        )
        for unit in assembly.units
    ]


def parse_terms(terms_doc: MarkdownDocument) -> list[TermEntry]:
    entries: list[TermEntry] = []
    category_map = {
        "divine names": "divine_name",
        "historical figures": "historical_figure",
        "classical texts": "classical_text",
        "people and beings": "being",
        "works and texts": "text",
        "places and institutions": "place_or_institution",
        "technical terms": "technical_term",
        "technical terms (neo-confucian)": "technical_term",
        "political terms": "political_term",
    }
    for section in terms_doc.sections:
        category = category_map.get(section.title.lower(), section.title.lower().replace(" ", "_"))
        for line in section.body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") and not stripped.startswith("* "):
                continue
            item = re.sub(r"^[-*]\s+", "", stripped)
            match = _TERM_RE.match(item)
            if not match:
                continue
            zh = match.group(1).strip()
            paren = match.group(2)
            gloss = match.group(3).strip().rstrip(".")
            romanization = None
            if paren:
                parts = [p.strip() for p in paren.split(",")]
                rom_parts = [p for p in parts if not re.match(r"^(line|lines|throughout)\b", p, re.IGNORECASE)]
                if rom_parts:
                    romanization = rom_parts[0]
            entries.append(TermEntry(zh=zh, romanization=romanization, gloss=gloss, category=category))
    return entries


def parse_questions(questions_doc: MarkdownDocument) -> list[QuestionEntry]:
    entries: list[QuestionEntry] = []
    for section in questions_doc.sections:
        category = section.title.lower().replace(" ", "_")
        for line in section.body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") and not stripped.startswith("* "):
                continue
            text = re.sub(r"^[-*]\s+", "", stripped)
            if text:
                entries.append(QuestionEntry(text=text, category=category))
    return entries


def parse_interpretations(interpretation_doc: MarkdownDocument) -> list[InterpretationBlock]:
    blocks: list[InterpretationBlock] = []
    for section in interpretation_doc.sections:
        paragraphs = [
            cleaned
            for line_group in section.body.split("\n\n")
            if (cleaned := line_group.strip()) and not cleaned.startswith("###")
        ]
        if paragraphs:
            blocks.append(InterpretationBlock(title=section.title, paragraphs=paragraphs))
    return blocks


def parse_notes(notes_doc: MarkdownDocument) -> list[NoteBlock]:
    blocks: list[NoteBlock] = []
    for section in notes_doc.sections:
        items: list[str] = []
        for line in section.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                items.append(re.sub(r"^[-*]\s+", "", stripped))
            elif stripped and not stripped.startswith("#"):
                items.append(stripped)
        if items:
            blocks.append(NoteBlock(title=section.title, items=items))
    return blocks


def build_interpretation_content(
    interpretation_doc: MarkdownDocument,
    notes_doc: MarkdownDocument,
    terms_doc: MarkdownDocument,
    questions_doc: MarkdownDocument,
) -> InterpretationContent:
    return InterpretationContent(
        interpretations=parse_interpretations(interpretation_doc),
        terms=parse_terms(terms_doc),
        questions=parse_questions(questions_doc),
        notes=parse_notes(notes_doc),
    )


def pick_witness_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        if section.title.lower() == "layout notes":
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus


def pick_translation_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        lowered = section.title.lower()
        if lowered in {"translation notes", "interpretive restraint"}:
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus
