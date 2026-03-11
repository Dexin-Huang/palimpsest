from __future__ import annotations

import re
from pathlib import Path

from palimpsest.models import (
    ColumnWitness,
    FolioRenderImageRegion,
    InterpretationBlock,
    InterpretationContent,
    MarginaliaEntry,
    NoteBlock,
    PageAssembly,
    PagePacket,
    QuestionEntry,
    SentencePair,
    TermEntry,
    WitnessContent,
)
from palimpsest.web import MarkdownDocument, MarkdownSection


_CHINESE_SENTENCE_RE = re.compile(r"[。！？]")
_HEADER_RE = re.compile(r"\*\*Header\*\*:\s*(.+)")
_MARGINALIA_LABEL_RE = re.compile(r"\*\*Marginalia\*\*\s*\(([^,)]+),\s*([^)]+)\)\s*:")
_TERM_RE = re.compile(r"\*\*(.+?)\*\*\s*(?:\(([^)]+)\))?\s*[:\-—]\s*(.+)")
_COLUMN_TITLE_RE = re.compile(r"^(.+?):\s*(.+?)(?:\s*\((.+)\))?\s*$")


def split_chinese_sentences(text: str) -> list[str]:
    joined = re.sub(r"\s+", "", text)
    sentences: list[str] = []
    last = 0
    for match in _CHINESE_SENTENCE_RE.finditer(joined):
        end = match.end()
        sentence = joined[last:end].strip()
        if sentence:
            sentences.append(sentence)
        last = end
    remainder = joined[last:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def extract_witness_column(section_body: str) -> tuple[str, str | None, list[str]]:
    header = ""
    main_lines: list[str] = []
    in_code_block = False
    marginalia_text: str | None = None
    marginalia_lines: list[str] = []
    in_marginalia = False

    for line in section_body.splitlines():
        stripped = line.strip()

        header_match = _HEADER_RE.match(stripped)
        if header_match:
            header = header_match.group(1).strip()
            continue

        if stripped.startswith("**Page Number**"):
            continue

        if _MARGINALIA_LABEL_RE.match(stripped):
            in_marginalia = True
            continue

        if stripped == "```":
            if in_marginalia and not in_code_block:
                in_code_block = True
                continue
            if in_code_block:
                in_code_block = False
                in_marginalia = False
                marginalia_text = "\n".join(marginalia_lines)
                continue

        if in_code_block:
            marginalia_lines.append(line)
            continue

        if stripped.startswith("**Main Text**") or stripped == "---":
            continue

        if stripped and not in_marginalia:
            main_lines.append(stripped)

    raw_text = "\n".join(main_lines)
    source_units = split_chinese_sentences(raw_text)
    if len(source_units) <= 1 and len(main_lines) > 1:
        source_units = [line for line in main_lines if line.strip()]
    return header, marginalia_text, source_units


def extract_translation_paragraphs(section_body: str) -> list[str]:
    paragraphs: list[str] = []
    in_code_block = False
    skip_until_rule = False
    current: list[str] = []

    for line in section_body.splitlines():
        stripped = line.strip()

        if stripped.startswith("**[Page number"):
            continue
        if stripped.startswith("**Latin Marginalia**") or stripped.startswith("**Note**:"):
            skip_until_rule = True
            continue
        if stripped == "```":
            in_code_block = not in_code_block
            if skip_until_rule:
                continue
        if in_code_block:
            continue
        if stripped == "---":
            skip_until_rule = False
            continue
        if skip_until_rule or stripped.startswith("**Main Text**"):
            continue

        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(stripped)

    if current:
        paragraphs.append(" ".join(current))

    return [p for p in paragraphs if p.strip()]


def pair_sentences(source_sentences: list[str], translation_paragraphs: list[str]) -> list[SentencePair]:
    pairs: list[SentencePair] = []
    n_source = len(source_sentences)
    n_trans = len(translation_paragraphs)

    if n_source == 0 and n_trans == 0:
        return pairs
    if n_source == n_trans:
        return [SentencePair(source=src, translation=trans) for src, trans in zip(source_sentences, translation_paragraphs)]
    if n_source > n_trans and n_trans > 0:
        per = n_source / n_trans
        for i, trans in enumerate(translation_paragraphs):
            start = int(i * per)
            end = int((i + 1) * per) if i < n_trans - 1 else n_source
            pairs.append(SentencePair(source="".join(source_sentences[start:end]), translation=trans))
        return pairs
    if n_trans > n_source and n_source > 0:
        per = n_trans / n_source
        for i, src in enumerate(source_sentences):
            start = int(i * per)
            end = int((i + 1) * per) if i < n_source - 1 else n_trans
            pairs.append(SentencePair(source=src, translation=" ".join(translation_paragraphs[start:end])))
        return pairs
    for src, trans in zip(source_sentences, translation_paragraphs):
        pairs.append(SentencePair(source=src, translation=trans))
    for src in source_sentences[n_trans:]:
        pairs.append(SentencePair(source=src, translation=""))
    for trans in translation_paragraphs[n_source:]:
        pairs.append(SentencePair(source="", translation=trans))
    return pairs


def match_column_sections(
    witness_doc: MarkdownDocument,
    translation_doc: MarkdownDocument,
) -> list[tuple[MarkdownSection | None, MarkdownSection | None]]:
    witness_columns = [s for s in witness_doc.sections if s.title.lower() != "layout notes"]
    translation_columns = [
        s for s in translation_doc.sections
        if s.title.lower() not in {"translation notes", "interpretive restraint"}
    ]

    pairs: list[tuple[MarkdownSection | None, MarkdownSection | None]] = []
    used_trans: set[int] = set()

    for ws in witness_columns:
        ws_key = ws.title.lower().strip()
        matched = False
        for i, ts in enumerate(translation_columns):
            if i in used_trans:
                continue
            ts_key = ts.title.lower().split(":")[0].strip()
            if ws_key == ts_key:
                pairs.append((ws, ts))
                used_trans.add(i)
                matched = True
                break
        if not matched:
            pairs.append((ws, None))

    for i, ts in enumerate(translation_columns):
        if i not in used_trans:
            pairs.append((None, ts))

    return pairs


def parse_column_header_en(translation_title: str) -> str:
    def _clean(candidate: str) -> str:
        value = candidate.strip()
        if re.fullmatch(r"\[[^\]]+\]", value):
            return ""
        return value

    match = _COLUMN_TITLE_RE.match(translation_title)
    if match and match.group(3):
        return _clean(match.group(3))
    if match and match.group(2):
        return _clean(match.group(2))
    return _clean(translation_title)


def build_witness_content(witness_doc: MarkdownDocument, translation_doc: MarkdownDocument) -> WitnessContent:
    columns: list[ColumnWitness] = []
    marginalia: list[MarginaliaEntry] = []

    for ws, ts in match_column_sections(witness_doc, translation_doc):
        if ws is None:
            continue
        header_zh, marg_text, source_sentences = extract_witness_column(ws.body)
        header_en = parse_column_header_en(ts.title) if ts else ""
        trans_paragraphs = extract_translation_paragraphs(ts.body) if ts else []
        pairs = pair_sentences(source_sentences, trans_paragraphs)
        columns.append(ColumnWitness(header_zh=header_zh, header_en=header_en, pairs=pairs))

        if marg_text:
            marg_match = _MARGINALIA_LABEL_RE.search(ws.body)
            script = marg_match.group(1).strip() if marg_match else "unknown"
            position = marg_match.group(2).strip() if marg_match else "unknown"
            note = None
            if ts:
                note_match = re.search(r"\*\*Note\*\*:\s*(.+)", ts.body)
                if note_match:
                    note = note_match.group(1).strip()
            marginalia.append(MarginaliaEntry(script=script, position=position, text=marg_text, note=note))

    return WitnessContent(columns=columns, marginalia=marginalia)


def load_page_assembly(packet: PagePacket) -> PageAssembly | None:
    candidate_paths: list[Path] = []
    assembly_ref = packet.files.get("page_assembly")
    if assembly_ref is not None and getattr(assembly_ref, "path", None):
        candidate_paths.append(Path(assembly_ref.path))
    layout_ref = packet.files.get("layout_probe")
    if layout_ref is not None and getattr(layout_ref, "path", None):
        candidate_paths.append(Path(layout_ref.path).parent / "page_assembly.json")
    if packet.prepared_image_path:
        candidate_paths.append(Path(packet.prepared_image_path).resolve().parent.parent / "layout_probe" / "page_assembly.json")
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidate_paths:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)
    assembly_path = next((path for path in unique_paths if path.exists()), None)
    if assembly_path is None:
        return None
    try:
        return PageAssembly.model_validate_json(assembly_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_witness_content_from_assembly(assembly: PageAssembly) -> WitnessContent:
    def _pairs_from_blocks(unit) -> list[SentencePair]:
        source_lines = [line.strip() for line in (unit.source_block or "").splitlines() if line.strip()]
        if not source_lines:
            return []
        return [
            SentencePair(
                source=source_line,
                translation="",
                unit_id=unit.unit_id,
                region_id=unit.region_id,
                bbox_norm=unit.bbox_norm,
            )
            for source_line in source_lines
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
        pairs = _pairs_from_blocks(unit)
        key = (unit.page_side, unit.column_index)

        if unit.role == "header":
            header_lines = [pair.source.strip() for pair in pairs if pair.source.strip()]
            header_text = "\n".join(header_lines) if header_lines else (unit.source_block or unit.label)
            header_translation_lines = [pair.translation.strip() for pair in pairs if pair.translation.strip()]
            header_map[key] = (header_text, "\n".join(header_translation_lines))
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
