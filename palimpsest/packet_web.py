from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import shutil

from palimpsest.models import (
    ColumnWitness,
    FolioRender,
    FolioRenderCover,
    FolioRenderImagePanel,
    FolioRenderImageRegion,
    FolioRenderNavigation,
    FolioRenderSection,
    FolioRenderSpread,
    FolioRenderTextPanel,
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
from palimpsest.packet_scholar import repair_packet_json
from palimpsest.web import (
    FolioTemplateSection,
    MarkdownDocument,
    MarkdownSection,
    MarkdownSectionGroup,
    group_document_sections as web_group_document_sections,
    groups_to_template_sections as web_groups_to_template_sections,
    html_shell as web_html_shell,
    parse_markdown_document as web_parse_markdown_document,
    render_markdown_body as web_render_markdown_body,
    render_template_sections as web_render_template_sections,
    site_css as web_site_css,
)


@dataclass
class RenderedPacketHtmlArtifact:
    packet_path: Path
    html_path: Path
    folio_render_path: Path
    meta_path: Path


@dataclass
class RenderedPacketSiteArtifact:
    doc_id: str
    index_path: Path
    contents_path: Path
    ending_path: Path
    folio_paths: list[Path]
    meta_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relpath(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), start=from_dir.resolve())).as_posix()


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "section"


def _page_sort_key(page_id: str) -> tuple[int, int, int, str]:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        side = 0 if match.group(2).lower() == "r" else 1
        return (0, int(match.group(1)), side, page_id)
    match = re.match(r"^page_(\d+)$", page_id, re.IGNORECASE)
    if match:
        return (1, int(match.group(1)), 0, page_id)
    return (2, 0, 0, page_id)


def _display_page_id(page_id: str) -> str:
    match = re.match(r"^f(\d+)([rv])$", page_id, re.IGNORECASE)
    if match:
        return f"Folio {int(match.group(1))}{match.group(2).lower()}"
    return page_id.replace("_", " ")


def _read_text(path: str | Path | None) -> str:
    if not path:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8")


# ═══════════════════════════════════════
# Structured content parsers
# ═══════════════════════════════════════

_CHINESE_SENTENCE_RE = re.compile(r"[。！？]")
_HEADER_RE = re.compile(r"\*\*Header\*\*:\s*(.+)")
_MARGINALIA_LABEL_RE = re.compile(
    r"\*\*Marginalia\*\*\s*\(([^,)]+),\s*([^)]+)\)\s*:"
)
_TERM_RE = re.compile(
    r"\*\*(.+?)\*\*\s*(?:\(([^)]+)\))?\s*[:\-—]\s*(.+)"
)
_COLUMN_TITLE_RE = re.compile(
    r"^(.+?):\s*(.+?)(?:\s*\((.+)\))?\s*$"
)


def _split_chinese_sentences(text: str) -> list[str]:
    """Split continuous Chinese text into sentences by terminal punctuation."""
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


def _extract_witness_column(section_body: str) -> tuple[str, str | None, list[str]]:
    """Extract header, marginalia text, and main text lines from a witness column."""
    header = ""
    main_lines: list[str] = []
    in_code_block = False
    marginalia_text: str | None = None
    marginalia_lines: list[str] = []
    in_marginalia = False
    past_main_text_label = False

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
            elif in_code_block:
                in_code_block = False
                in_marginalia = False
                marginalia_text = "\n".join(marginalia_lines)
                continue

        if in_code_block:
            marginalia_lines.append(line)
            continue

        if stripped.startswith("**Main Text**"):
            past_main_text_label = True
            continue

        if stripped == "---":
            continue

        if stripped and not in_marginalia:
            main_lines.append(stripped)

    raw_text = "\n".join(main_lines)
    source_units = _split_chinese_sentences(raw_text)
    if len(source_units) <= 1 and len(main_lines) > 1:
        source_units = [line for line in main_lines if line.strip()]
    return header, marginalia_text, source_units


def _extract_translation_paragraphs(section_body: str) -> list[str]:
    """Extract clean translation paragraphs from a translation column section."""
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
        if skip_until_rule:
            continue
        if stripped.startswith("**Main Text**"):
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


def _pair_sentences(
    source_sentences: list[str],
    translation_paragraphs: list[str],
) -> list[SentencePair]:
    """Pair Chinese sentences with translation paragraphs positionally."""
    pairs: list[SentencePair] = []
    n_source = len(source_sentences)
    n_trans = len(translation_paragraphs)

    if n_source == 0 and n_trans == 0:
        return pairs

    # If counts match, pair 1:1
    if n_source == n_trans:
        for src, trans in zip(source_sentences, translation_paragraphs):
            pairs.append(SentencePair(source=src, translation=trans))
        return pairs

    # If more source than translation, group sources per translation
    if n_source > n_trans and n_trans > 0:
        per = n_source / n_trans
        for i, trans in enumerate(translation_paragraphs):
            start = int(i * per)
            end = int((i + 1) * per) if i < n_trans - 1 else n_source
            combined_source = "".join(source_sentences[start:end])
            pairs.append(SentencePair(source=combined_source, translation=trans))
        return pairs

    # If more translation than source, group translations per source
    if n_trans > n_source and n_source > 0:
        per = n_trans / n_source
        for i, src in enumerate(source_sentences):
            start = int(i * per)
            end = int((i + 1) * per) if i < n_source - 1 else n_trans
            combined_trans = " ".join(translation_paragraphs[start:end])
            pairs.append(SentencePair(source=src, translation=combined_trans))
        return pairs

    # Fallback: just zip what we have
    for src, trans in zip(source_sentences, translation_paragraphs):
        pairs.append(SentencePair(source=src, translation=trans))

    # Append remainders
    for src in source_sentences[n_trans:]:
        pairs.append(SentencePair(source=src, translation=""))
    for trans in translation_paragraphs[n_source:]:
        pairs.append(SentencePair(source="", translation=trans))

    return pairs


def _match_column_sections(
    witness_doc: MarkdownDocument,
    translation_doc: MarkdownDocument,
) -> list[tuple[MarkdownSection | None, MarkdownSection | None]]:
    """Match witness and translation sections by column label."""
    witness_columns = [s for s in witness_doc.sections if s.title.lower() != "layout notes"]
    translation_columns = [
        s for s in translation_doc.sections
        if s.title.lower() not in {"translation notes", "interpretive restraint"}
    ]

    # Try matching by normalized column name
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


def _parse_column_header_en(translation_title: str) -> str:
    """Extract English header from translation section title like 'Left Column: 天賦恒性 (Heaven Endows Constant Nature)'."""
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


def _build_witness_content(
    witness_doc: MarkdownDocument,
    translation_doc: MarkdownDocument,
) -> WitnessContent:
    """Build structured witness content from witness and translation markdown."""
    columns: list[ColumnWitness] = []
    marginalia: list[MarginaliaEntry] = []

    matched = _match_column_sections(witness_doc, translation_doc)

    for ws, ts in matched:
        if ws is None:
            continue

        header_zh, marg_text, source_sentences = _extract_witness_column(ws.body)
        header_en = _parse_column_header_en(ts.title) if ts else ""

        if ts:
            trans_paragraphs = _extract_translation_paragraphs(ts.body)
        else:
            trans_paragraphs = []

        pairs = _pair_sentences(source_sentences, trans_paragraphs)
        columns.append(ColumnWitness(
            header_zh=header_zh,
            header_en=header_en,
            pairs=pairs,
        ))

        if marg_text:
            marg_match = _MARGINALIA_LABEL_RE.search(ws.body)
            script = marg_match.group(1).strip() if marg_match else "unknown"
            position = marg_match.group(2).strip() if marg_match else "unknown"

            # Try to find note from translation
            note = None
            if ts:
                note_match = re.search(r"\*\*Note\*\*:\s*(.+)", ts.body)
                if note_match:
                    note = note_match.group(1).strip()

            marginalia.append(MarginaliaEntry(
                script=script,
                position=position,
                text=marg_text,
                note=note,
            ))

    return WitnessContent(columns=columns, marginalia=marginalia)


def _load_page_assembly(packet: PagePacket) -> PageAssembly | None:
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


def _build_witness_content_from_assembly(assembly: PageAssembly) -> WitnessContent:
    def _pairs_from_blocks(unit: PageAssemblyUnit) -> list[SentencePair]:
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
            header_translation = "\n".join(header_translation_lines)
            header_map[key] = (header_text, header_translation)
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
                pairs=pairs
                or [
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


def _build_image_regions_from_assembly(assembly: PageAssembly) -> list[FolioRenderImageRegion]:
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


def _parse_terms(terms_doc: MarkdownDocument) -> list[TermEntry]:
    """Parse terms.md into structured term entries."""
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
            if match:
                zh = match.group(1).strip()
                paren = match.group(2)
                gloss = match.group(3).strip().rstrip(".")

                romanization = None
                if paren:
                    # Extract just the romanization, stripping line refs
                    parts = [p.strip() for p in paren.split(",")]
                    rom_parts = [p for p in parts if not re.match(r"^(line|lines|throughout)\b", p, re.IGNORECASE)]
                    if rom_parts:
                        romanization = rom_parts[0]

                entries.append(TermEntry(
                    zh=zh,
                    romanization=romanization,
                    gloss=gloss,
                    category=category,
                ))

    return entries


def _parse_questions(questions_doc: MarkdownDocument) -> list[QuestionEntry]:
    """Parse questions.md into structured question entries."""
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


def _parse_interpretations(interpretation_doc: MarkdownDocument) -> list[InterpretationBlock]:
    """Parse interpretation.md into structured blocks."""
    blocks: list[InterpretationBlock] = []
    for section in interpretation_doc.sections:
        paragraphs: list[str] = []
        for line_group in section.body.split("\n\n"):
            cleaned = line_group.strip()
            if cleaned and not cleaned.startswith("###"):
                paragraphs.append(cleaned)
        if paragraphs:
            blocks.append(InterpretationBlock(title=section.title, paragraphs=paragraphs))
    return blocks


def _parse_notes(notes_doc: MarkdownDocument) -> list[NoteBlock]:
    """Parse notes.md into structured note blocks."""
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


def _build_interpretation_content(
    interpretation_doc: MarkdownDocument,
    notes_doc: MarkdownDocument,
    terms_doc: MarkdownDocument,
    questions_doc: MarkdownDocument,
) -> InterpretationContent:
    """Build structured interpretation content from markdown documents."""
    return InterpretationContent(
        interpretations=_parse_interpretations(interpretation_doc),
        terms=_parse_terms(terms_doc),
        questions=_parse_questions(questions_doc),
        notes=_parse_notes(notes_doc),
    )


# ═══════════════════════════════════════
# Structured HTML renderers
# ═══════════════════════════════════════

def _render_lacuna(text: str) -> str:
    """Replace [////] markers with lacuna spans."""
    return re.sub(
        r"\[/{2,}\]",
        '<span class="lacuna">&thinsp;[////]&thinsp;</span>',
        escape(text),
    )


def _render_translation_inline(text: str) -> str:
    """Render translation text with inline markdown to HTML."""
    html = escape(text)
    # Bold
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    # Italic
    html = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", html)
    # [text unclear] and similar uncertain markers
    html = re.sub(
        r"\[([^\]]*(?:unclear|uncertain|continues)[^\]]*)\]",
        r'<span class="uncertain">[\1]</span>',
        html,
        flags=re.IGNORECASE,
    )
    # Em-dash
    html = html.replace("—", "&mdash;")
    return html


def _render_structured_witness_face(folio: FolioRender) -> str:
    """Render the witness/translation face from structured content."""
    wc = folio.spread.content.witness_content
    if not wc:
        return ""

    parts: list[str] = []
    parts.append('<div class="face face-witness">')
    parts.append('  <div class="page-text-inner">')
    parts.append('    <div class="page-header">')
    parts.append(f'      <div class="page-header-label">{escape(folio.spread.content.header_label)}</div>')
    parts.append(f'      <div class="page-header-title">{escape(folio.spread.content.header_title)}</div>')
    parts.append('    </div>')

    for col in wc.columns:
        region_attrs = ""
        if col.region_id:
            region_attrs += f' data-region-id="{escape(col.region_id)}"'
        if col.unit_id:
            region_attrs += f' data-unit-id="{escape(col.unit_id)}"'
        parts.append(f'    <section class="witness-unit"{region_attrs}>')
        parts.append('    <div class="column-header">')
        parts.append(f'      <div class="column-header-chinese">{escape(col.header_zh)}</div>')
        if col.header_en:
            parts.append(f'      <div class="column-header-english">{escape(col.header_en)}</div>')
        parts.append('      <div class="column-rule"></div>')
        parts.append('    </div>')

        for pair in col.pairs:
            source_html = _render_lacuna(pair.source) if pair.source else ""
            trans_html = _render_translation_inline(pair.translation) if pair.translation else ""
            pair_region_attrs = region_attrs
            if pair.region_id:
                pair_region_attrs = f' data-region-id="{escape(pair.region_id)}"'
                if pair.unit_id:
                    pair_region_attrs += f' data-unit-id="{escape(pair.unit_id)}"'
            parts.append(f'    <div class="pair"{pair_region_attrs}>')
            if source_html:
                parts.append(f'      <div class="pair-source">{source_html}</div>')
            if trans_html:
                parts.append(f'      <div class="pair-translation">{trans_html}</div>')
            parts.append('    </div>')
        parts.append('    </section>')

    if wc.marginalia:
        for marg in wc.marginalia:
            parts.append('    <div class="marginalia-section">')
            label = f"{escape(marg.script)} Marginalia &middot; {escape(marg.position)}"
            parts.append(f'      <div class="marginalia-label">{label}</div>')
            parts.append(f'      <div class="marginalia-text">{escape(marg.text)}</div>')
            if marg.note:
                parts.append(f'      <div class="marginalia-note">{escape(marg.note)}</div>')
            parts.append('    </div>')

    parts.append('  </div>')
    parts.append('</div>')

    return "\n".join(parts)


def _render_structured_interpretation_face(folio: FolioRender) -> str:
    """Render the interpretation/apparatus face from structured content."""
    ic = folio.spread.interpretation.interpretation_content
    if not ic:
        return ""

    parts: list[str] = []
    parts.append('<div class="face face-interp">')
    parts.append('  <div class="page-text-inner">')
    parts.append('    <div class="page-header page-header-dark">')
    parts.append(f'      <div class="page-header-label">{escape(folio.spread.interpretation.header_label)}</div>')
    parts.append(f'      <div class="page-header-title">{escape(folio.spread.interpretation.header_title)}</div>')
    parts.append('    </div>')

    # Interpretation blocks
    for block in ic.interpretations:
        parts.append(f'    <div class="interpretation-label">{escape(block.title)}</div>')
        for para in block.paragraphs:
            parts.append(f'    <p class="interpretation-text">{_render_translation_inline(para)}</p>')

    # Terms grid
    if ic.terms:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Key Terms</div>')
        parts.append('      <div class="term-grid">')
        for term in ic.terms:
            rom = f" <em>{escape(term.romanization)}</em>" if term.romanization else ""
            parts.append(
                f'        <div class="term"><span class="term-zh">{escape(term.zh)}</span>{rom} &mdash; {escape(term.gloss)}</div>'
            )
        parts.append('      </div>')
        parts.append('    </div>')

    # Notes
    if ic.notes:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Notes</div>')
        for block in ic.notes:
            parts.append(f'      <div style="margin-bottom: 0.8rem;">')
            parts.append(f'        <div style="font-size: 0.6rem; letter-spacing: 0.15em; text-transform: uppercase; color: var(--faded); margin-bottom: 0.3rem;">{escape(block.title)}</div>')
            for item in block.items:
                parts.append(f'        <p style="font-size: 0.68rem; line-height: 1.7; color: var(--faded-light); margin-bottom: 0.35rem;">{_render_translation_inline(item)}</p>')
            parts.append('      </div>')
        parts.append('    </div>')

    # Questions
    if ic.questions:
        parts.append('    <div class="terms-divider">')
        parts.append('      <div class="terms-label">Open Questions</div>')
        for q in ic.questions:
            parts.append(f'      <p style="font-size: 0.68rem; line-height: 1.7; color: var(--faded-light); margin-bottom: 0.5rem;">{_render_translation_inline(q.text)}</p>')
        parts.append('    </div>')

    parts.append('    <div class="colophon"><div class="colophon-text">Palimpsest Edition</div></div>')
    parts.append('  </div>')
    parts.append('</div>')

    return "\n".join(parts)


def _build_folio_render(
    *,
    packet: PagePacket,
    book_title: str,
    image_href: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
    content_sections: list[FolioTemplateSection],
    interpretation_sections: list[FolioTemplateSection],
    witness_content: WitnessContent | None = None,
    interpretation_content: InterpretationContent | None = None,
    image_regions: list[FolioRenderImageRegion] | None = None,
) -> FolioRender:
    display_page = _display_page_id(packet.page_id)
    content_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in content_sections
    ]
    interpretation_render_sections = [
        FolioRenderSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
        for section in interpretation_sections
    ]

    return FolioRender(
        created_at=_utc_now(),
        doc_id=packet.doc_id,
        page_id=packet.page_id,
        page_label=display_page,
        book_title=book_title,
        page_unit=packet.page_unit,
        source_image_path=packet.source_image_path,
        cover=FolioRenderCover(
            label="Palimpsest Edition",
            title=display_page,
            subtitle=book_title,
            nav_hint="Press arrow keys or click to open the folio",
        ),
        spread=FolioRenderSpread(
            image=FolioRenderImagePanel(
                folio_label=display_page,
                source_label=book_title,
                image_path=image_href,
                caption="Source witness / raw folio image",
                regions=image_regions or [],
            ),
            content=FolioRenderTextPanel(
                header_label="Witness & Translation",
                header_title=display_page,
                sections=content_render_sections,
                witness_content=witness_content,
            ),
            interpretation=FolioRenderTextPanel(
                header_label="Interpretation & Apparatus",
                header_title=display_page,
                sections=interpretation_render_sections,
                interpretation_content=interpretation_content,
            ),
        ),
        navigation=FolioRenderNavigation(
            home_href=home_href,
            prev_href=prev_href,
            next_href=next_href,
        ),
    )


def _render_cover_piece(folio: FolioRender) -> str:
    return "\n".join(
        [
            f'<div class="cover-label">{escape(folio.cover.label)}</div>',
            f'<h1 class="cover-title">{escape(folio.cover.title)}</h1>',
            f'<div class="cover-subtitle">{escape(folio.cover.subtitle)}</div>',
            '<div class="cover-rule"></div>',
            f'<div class="cover-nav-hint"><span class="arrow">&rarr;</span> {escape(folio.cover.nav_hint or "")}</div>',
        ]
    )


def _render_content_piece(folio: FolioRender) -> str:
    if folio.spread.content.witness_content:
        return _render_structured_witness_face(folio)

    sections_html = web_render_template_sections(
        [
            FolioTemplateSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
            for section in folio.spread.content.sections
        ],
        article_class="content-block",
        title_class="content-block-title",
    )
    return "\n".join(
        [
            '<div class="face face-witness">',
            '  <div class="page-text-inner">',
            '    <div class="page-header">',
            f'      <div class="page-header-label">{escape(folio.spread.content.header_label)}</div>',
            f'      <div class="page-header-title">{escape(folio.spread.content.header_title)}</div>',
            '    </div>',
            sections_html,
            '  </div>',
            '</div>',
        ]
    )


def _render_interpretation_piece(
    folio: FolioRender,
) -> str:
    if folio.spread.interpretation.interpretation_content:
        return _render_structured_interpretation_face(folio)

    sections_html = web_render_template_sections(
        [
            FolioTemplateSection(kind=section.kind, title=section.title, body_html=section.body_html, wide=section.wide)
            for section in folio.spread.interpretation.sections
        ],
        article_class="apparatus-block",
        title_class="apparatus-block-title",
    )
    return "\n".join(
        [
            '<div class="face face-interp">',
            '  <div class="page-text-inner">',
            '    <div class="page-header page-header-dark">',
            f'      <div class="page-header-label">{escape(folio.spread.interpretation.header_label)}</div>',
            f'      <div class="page-header-title">{escape(folio.spread.interpretation.header_title)}</div>',
            '    </div>',
            sections_html,
            '    <div class="colophon"><div class="colophon-text">Palimpsest Edition</div></div>',
            '  </div>',
            '</div>',
        ]
    )


def _render_spread_piece(folio: FolioRender, *, content_piece: str, interpretation_piece: str) -> str:
    region_overlays = "\n".join(
        (
            f'<div class="image-region image-region--{escape(region.role)}" '
            f'data-region-id="{escape(region.region_id)}" '
            f'data-unit-id="{escape(region.unit_id or "")}" '
            f'title="{escape(region.label)}" '
            f'style="left:{region.bbox_norm[0] * 100:.3f}%;top:{region.bbox_norm[1] * 100:.3f}%;'
            f'width:{region.bbox_norm[2] * 100:.3f}%;height:{region.bbox_norm[3] * 100:.3f}%;">'
            f'<span class="image-region-label">{escape(region.label)}</span>'
            f'</div>'
        )
        for region in folio.spread.image.regions
    )
    return "\n".join(
        [
            '<div class="spread">',
            '  <div class="page-image">',
            '    <div class="image-header">',
            f'      <span class="image-header-folio">{escape(folio.spread.image.folio_label)}</span>',
            f'      <span class="image-header-source">{escape(folio.spread.image.source_label)}</span>',
            '    </div>',
            '    <div class="image-frame">',
            f'      <img src="{escape(folio.spread.image.image_path)}" alt="{escape(folio.spread.image.folio_label)} source image">',
            '      <div class="image-overlay">',
            region_overlays,
            '      </div>',
            '    </div>',
            f'    <span class="image-caption">{escape(folio.spread.image.caption)}</span>',
            '  </div>',
            '  <div class="right-panel" id="right-panel">',
            content_piece,
            interpretation_piece,
            '    <button class="flip-symbol" id="flip-symbol" type="button" title="Toggle witness / interpretation" aria-label="Toggle witness / interpretation">',
            '      <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">',
            '        <circle cx="50" cy="50" r="49" fill="none" stroke="var(--ink)" stroke-width="0.5" opacity="0.4"/>',
            '        <circle cx="50" cy="50" r="47" fill="none" stroke="var(--ink)" stroke-width="1.2"/>',
            '        <circle cx="50" cy="50" r="45" fill="var(--parchment)"/>',
            '        <path d="M 50,5 A 45,45 0 0 1 50,95 A 22.5,22.5 0 0 1 50,50 A 22.5,22.5 0 0 0 50,5 Z" fill="var(--ink)"/>',
            '        <circle cx="50" cy="72.5" r="6.5" fill="var(--ink)"/>',
            '        <circle cx="50" cy="27.5" r="6.5" fill="var(--parchment)"/>',
            '      </svg>',
            '    </button>',
            '  </div>',
            '</div>',
        ]
    )


def _pick_witness_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        if section.title.lower() == "layout notes":
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus


def _pick_translation_sections(doc: MarkdownDocument) -> tuple[list[MarkdownSection], list[MarkdownSection]]:
    main: list[MarkdownSection] = []
    apparatus: list[MarkdownSection] = []
    for section in doc.sections:
        lowered = section.title.lower()
        if lowered in {"translation notes", "interpretive restraint"}:
            apparatus.append(section)
        else:
            main.append(section)
    return main, apparatus


def _site_css() -> str:
    return """
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --parchment: #F0EBE0;
    --parchment-deep: #E8E1D2;
    --ink: #1A1714;
    --ink-soft: #3D3630;
    --faded: #8A7E6E;
    --faded-light: #B5AA96;
    --accent: #8A4B2A;
    --rule: #D0C4AE;
    --rule-light: #E0D8C8;
    --panel-bg: #141210;
    --panel-fg: #C8BFA8;
    --glow: rgba(138,75,42,0.08);
  }
  html { font-size: 16px; }
  body {
    margin: 0;
    background: var(--panel-bg);
    color: var(--ink);
    font-family: 'Noto Serif', 'Georgia', serif;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    height: 100vh;
    width: 100vw;
  }
  a { color: inherit; }
  .book {
    position: fixed;
    inset: 0;
  }
  .page {
    position: absolute;
    inset: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.5s ease;
  }
  .page.active {
    opacity: 1;
    pointer-events: auto;
  }
  .cover {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: var(--panel-bg);
    overflow: hidden;
  }
  .cover::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 50% 40%, rgba(138,75,42,0.07) 0%, transparent 70%);
    pointer-events: none;
  }
  .cover-label {
    font-size: 0.65rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 1.5rem;
    opacity: 0;
    animation: fadeUp 1.2s 0.3s ease forwards;
  }
  .cover-title {
    font-size: 2.8rem;
    font-weight: 300;
    color: var(--parchment);
    letter-spacing: 0.04em;
    opacity: 0;
    animation: fadeUp 1.2s 0.6s ease forwards;
  }
  .cover-subtitle {
    font-size: 0.85rem;
    color: var(--faded);
    margin-top: 0.75rem;
    letter-spacing: 0.15em;
    opacity: 0;
    animation: fadeUp 1.2s 0.9s ease forwards;
  }
  .cover-rule {
    width: 60px;
    height: 1px;
    background: var(--accent);
    margin-top: 2rem;
    opacity: 0;
    animation: fadeUp 1.2s 1.1s ease forwards;
  }
  .cover-nav-hint {
    position: absolute;
    bottom: 2.5rem;
    font-size: 0.6rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded-light);
    opacity: 0;
    animation: fadeUp 1.2s 1.8s ease forwards, pulse 3s 3s ease-in-out infinite;
    margin-top: 1rem;
    display: flex;
    align-items: center;
    gap: 0.8em;
  }
  .cover-nav-hint .arrow {
    font-size: 0.8rem;
    color: var(--accent);
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }
  .spread-page {
    height: 100%;
  }
  .spread {
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: var(--parchment);
    height: 100%;
    position: relative;
  }
  .spread::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 3px;
    background: linear-gradient(to bottom, transparent 0%, var(--rule) 5%, var(--rule) 95%, transparent 100%);
    transform: translateX(-1.5px);
    z-index: 10;
    box-shadow: -4px 0 12px rgba(0,0,0,0.03), 4px 0 12px rgba(0,0,0,0.03);
  }
  .page-image {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.8rem 2rem;
    background: linear-gradient(160deg, var(--parchment-deep) 0%, var(--parchment) 40%, var(--parchment-deep) 100%);
    position: relative;
    overflow: hidden;
  }
  .page-image::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 55% 45%, var(--glow), transparent 65%);
    pointer-events: none;
  }
  .image-header {
    position: absolute;
    top: 1.2rem;
    left: 1.5rem;
    right: 1.5rem;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .image-header-folio {
    font-size: 0.6rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
  }
  .image-header-source {
    font-size: 0.55rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .image-frame {
    position: relative;
    max-width: 90%;
    max-height: 85vh;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06), 0 12px 32px rgba(0,0,0,0.08), 0 32px 72px rgba(0,0,0,0.05);
    line-height: 0;
    border-radius: 1px;
  }
  .image-frame img {
    display: block;
    width: 100%;
    height: auto;
    max-height: 85vh;
    object-fit: contain;
  }
  .image-frame::after {
    content: '';
    position: absolute;
    inset: 0;
    border: 1px solid rgba(0,0,0,0.05);
    pointer-events: none;
  }
  .image-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .image-region {
    position: absolute;
    border: 2px solid rgba(138,75,42,0.45);
    background: rgba(138,75,42,0.08);
    box-shadow: inset 0 0 0 1px rgba(247,241,230,0.22);
    transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
    pointer-events: auto;
    cursor: pointer;
  }
  .image-region:hover,
  .image-region.is-linked-active {
    border-color: rgba(138,75,42,0.95);
    background: rgba(138,75,42,0.18);
    box-shadow: inset 0 0 0 1px rgba(247,241,230,0.5), 0 0 0 2px rgba(138,75,42,0.18);
  }
  .image-region-label {
    position: absolute;
    top: -1.2rem;
    left: 0;
    background: rgba(20,18,16,0.86);
    color: var(--parchment);
    font-size: 0.46rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    line-height: 1;
    padding: 0.22rem 0.32rem;
    white-space: nowrap;
    opacity: 0;
    transform: translateY(2px);
    transition: opacity 0.18s ease, transform 0.18s ease;
  }
  .image-region:hover .image-region-label,
  .image-region.is-linked-active .image-region-label {
    opacity: 1;
    transform: translateY(0);
  }
  .image-caption {
    position: absolute;
    bottom: 1rem;
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .right-panel {
    position: relative;
    overflow: hidden;
  }
  .face {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 2rem 2.5rem 3rem 2rem;
    transition: opacity 0.4s ease, transform 0.4s ease;
    scrollbar-width: thin;
    scrollbar-color: var(--rule) transparent;
  }
  .face::-webkit-scrollbar { width: 5px; }
  .face::-webkit-scrollbar-track { background: transparent; }
  .face::-webkit-scrollbar-thumb { background: var(--rule); border-radius: 3px; }
  .face-witness {
    background: var(--parchment);
    box-shadow: inset 6px 0 16px -8px rgba(0,0,0,0.04);
  }
  .face-interp {
    background: var(--panel-bg);
    opacity: 0;
    pointer-events: none;
    transform: translateY(8px);
  }
  .right-panel.flipped .face-witness {
    opacity: 0;
    pointer-events: none;
    transform: translateY(-8px);
  }
  .right-panel.flipped .face-interp {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }
  .page-text-inner { max-width: 500px; }
  .page-header {
    margin-bottom: 1.6rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--rule);
  }
  .page-header-dark {
    border-color: rgba(200,191,168,0.15);
  }
  .page-header-label {
    font-size: 0.5rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.2rem;
  }
  .page-header-title {
    font-size: 1rem;
    font-weight: 400;
    color: var(--ink);
    letter-spacing: 0.02em;
  }
  .page-header-dark .page-header-title {
    color: var(--panel-fg);
  }
  .content-block {
    margin-top: 2rem;
  }
  .content-block:first-of-type {
    margin-top: 0;
  }
  .content-block-title,
  .apparatus-block-title {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.9rem;
  }
  .apparatus-block {
    margin-top: 1.8rem;
  }
  .apparatus-block:first-of-type {
    margin-top: 0;
  }
  .apparatus-block-wide {
    margin-top: 2rem;
  }
  .section-card {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px solid var(--rule-light);
  }
  .section-card:first-child {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .section-card-title {
    font-size: 1rem;
    color: var(--ink);
    margin-bottom: 0.5rem;
  }
  .section-card-body p, .apparatus-card-body p {
    margin: 0 0 0.7rem 0;
    line-height: 1.72;
    color: var(--ink-soft);
  }
  .section-card-body ul, .section-card-body ol,
  .apparatus-card-body ul, .apparatus-card-body ol {
    margin: 0 0 0.8rem 1.2rem;
    padding: 0;
    line-height: 1.68;
    color: var(--ink-soft);
  }
  .section-card-body li, .apparatus-card-body li { margin-bottom: 0.35rem; }
  .section-card-body pre, .apparatus-card-body pre {
    margin: 0.8rem 0;
    padding: 0.9rem 1rem;
    background: #efe7d8;
    border-left: 3px solid var(--accent);
    overflow-x: auto;
    font-size: 0.9rem;
    line-height: 1.55;
    color: var(--ink);
    white-space: pre-wrap;
  }
  .section-card-body hr, .apparatus-card-body hr {
    border: 0;
    height: 1px;
    background: var(--rule);
    margin: 1rem 0;
  }
  .subsection-card {
    margin-top: 0.9rem;
    padding: 0.8rem 0 0 0.9rem;
    border-left: 2px solid var(--rule);
  }
  .subsection-card-title {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.45rem;
  }
  .subsection-card-body p,
  .subsection-card-body ul,
  .subsection-card-body ol,
  .subsection-card-body li {
    color: var(--ink-soft);
    line-height: 1.68;
  }
  .content-block--witness .content-block-title {
    color: var(--accent);
  }
  .content-block--translation .content-block-title {
    color: var(--faded);
  }
  .apparatus-block--interpretation .apparatus-block-title,
  .apparatus-block--questions .apparatus-block-title {
    color: var(--parchment);
  }
  .section-card-body code, .apparatus-card-body code {
    background: rgba(20,18,16,0.06);
    padding: 0.08rem 0.28rem;
    border-radius: 2px;
    font-size: 0.95em;
  }
  .face-interp { color: var(--panel-fg); }
  .face-interp p,
  .face-interp ul,
  .face-interp ol,
  .face-interp li {
    color: var(--faded-light);
  }
  .face-interp strong { color: var(--panel-fg); }
  .face-interp em { color: var(--faded-light); }
  .face-interp pre {
    background: rgba(240,235,224,0.06);
    border-left-color: var(--accent);
    color: var(--faded-light);
  }
  .face-interp code {
    background: rgba(240,235,224,0.08);
    color: var(--panel-fg);
  }
  .face-interp hr {
    background: rgba(200,191,168,0.12);
  }
  .face-interp .section-card {
    border-top-color: rgba(200,191,168,0.12);
  }
  .face-interp .section-card-title {
    color: var(--panel-fg);
  }
  .face-interp .subsection-card {
    border-left-color: rgba(200,191,168,0.16);
  }
  .face-interp .subsection-card-title {
    color: var(--parchment);
  }
  /* ─── Structured witness: column headers ─── */
  .column-header { margin-top: 2rem; margin-bottom: 1.2rem; }
  .column-header:first-child { margin-top: 0; }
  .witness-unit {
    position: relative;
    transition: background 0.18s ease, box-shadow 0.18s ease;
    border-radius: 4px;
  }
  .witness-unit.is-linked-active {
    background: rgba(138,75,42,0.08);
    box-shadow: 0 0 0 1px rgba(138,75,42,0.16);
  }
  .column-header-chinese {
    font-size: 1.1rem;
    color: var(--accent);
    letter-spacing: 0.08em;
    margin-bottom: 0.15rem;
  }
  .column-header-english {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
  }
  .column-rule {
    width: 36px;
    height: 2px;
    background: var(--accent);
    margin-top: 0.5rem;
    border-radius: 1px;
  }

  /* ─── Sentence pairs ─── */
  .pair {
    padding: 0.65rem 0;
    border-bottom: 1px solid var(--rule-light);
    transition: background 0.25s ease;
  }
  .pair:hover,
  .pair.is-linked-active {
    background: var(--glow);
    margin-left: -0.6rem; margin-right: -0.6rem;
    padding-left: 0.6rem; padding-right: 0.6rem;
    border-radius: 2px;
  }
  .pair:last-of-type { border-bottom: none; }
  .pair-source {
    font-size: 0.95rem;
    line-height: 1.7;
    color: var(--ink);
    margin-bottom: 0.2rem;
  }
  .pair-translation {
    font-size: 0.76rem;
    line-height: 1.6;
    color: var(--faded);
    font-style: italic;
  }
  .pair-translation em { font-style: normal; color: var(--ink-soft); }
  .pair-translation .uncertain {
    color: var(--accent);
    font-style: normal;
    font-size: 0.68rem;
  }
  .lacuna { color: var(--accent); font-weight: 600; letter-spacing: 0.04em; }

  /* ─── Marginalia ─── */
  .marginalia-section {
    margin-top: 1.8rem;
    padding-top: 1.2rem;
    border-top: 1px solid var(--rule);
  }
  .marginalia-label {
    font-size: 0.5rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.6rem;
  }
  .marginalia-text {
    font-size: 0.72rem;
    line-height: 1.75;
    color: var(--faded);
    font-style: italic;
  }
  .marginalia-note {
    margin-top: 0.4rem;
    font-size: 0.65rem;
    line-height: 1.55;
    color: var(--faded-light);
    font-style: normal;
  }

  /* ─── Interpretation face structured blocks ─── */
  .interpretation-label {
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.8rem;
  }
  .interpretation-text {
    font-size: 0.74rem;
    line-height: 1.75;
    color: var(--panel-fg);
  }
  .interpretation-text + .interpretation-text { margin-top: 0.7rem; }
  .terms-divider {
    margin-top: 1.2rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(200,191,168,0.12);
  }
  .terms-label {
    font-size: 0.45rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.6rem;
  }
  .term-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.3rem 1.5rem;
  }
  .term { font-size: 0.65rem; line-height: 1.45; color: var(--faded-light); }
  .term-zh { color: var(--panel-fg); margin-right: 0.2em; }

  .colophon {
    margin-top: 1.5rem;
    text-align: center;
    padding: 0.8rem 0 0.5rem;
  }
  .colophon-text {
    font-size: 0.45rem;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .flip-symbol {
    position: absolute;
    top: 1.1rem;
    right: 1.2rem;
    z-index: 20;
    width: 18px;
    height: 18px;
    cursor: pointer;
    user-select: none;
    opacity: 0.35;
    transition: opacity 0.3s ease, transform 0.4s ease;
    background: transparent;
    border: none;
    padding: 0;
  }
  .flip-symbol:hover {
    opacity: 0.7;
  }
  .flip-symbol svg {
    width: 100%;
    height: 100%;
    transition: transform 0.4s ease;
  }
  .folio-links {
    position: fixed;
    right: 1.2rem;
    bottom: 1.2rem;
    display: flex;
    gap: 0.7rem;
    z-index: 100;
  }
  .folio-link {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    text-decoration: none;
    padding: 0.65rem 0.8rem;
    border-radius: 999px;
    background: rgba(20,18,16,0.85);
    color: var(--faded-light);
    font-size: 0.62rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }
  .folio-link:hover {
    color: var(--parchment);
  }
  @media (max-width: 1120px) {
    .spread {
      grid-template-columns: 1fr;
    }
    .spread::after {
      display: none;
    }
    .page-image {
      height: 50vh;
      padding: 1.5rem;
    }
  }
  .empty-note {
    margin: 0;
    color: var(--faded-light);
    line-height: 1.7;
  }
"""


def _html_shell(*, title: str, body: str, extra_css: str = "") -> str:
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f"  <title>{escape(title)}</title>",
            "  <style>",
            _site_css(),
            extra_css,
            "  </style>",
            "</head>",
            "<body>",
            body,
            "</body>",
            "</html>",
        ]
    )


def _render_folio_html(
    *,
    packet: PagePacket,
    image_href: str,
    book_title: str,
    prev_href: str | None,
    next_href: str | None,
    home_href: str | None,
    include_cover: bool = True,
) -> tuple[str, FolioRender]:
    witness_doc = web_parse_markdown_document(_read_text(packet.files.get("witness").path if packet.files.get("witness") else None))
    translation_doc = web_parse_markdown_document(_read_text(packet.files.get("translation").path if packet.files.get("translation") else None))
    interpretation_doc = web_parse_markdown_document(_read_text(packet.files.get("interpretation").path if packet.files.get("interpretation") else None))
    notes_doc = web_parse_markdown_document(_read_text(packet.files.get("notes").path if packet.files.get("notes") else None))
    terms_doc = web_parse_markdown_document(_read_text(packet.files.get("terms").path if packet.files.get("terms") else None))
    questions_doc = web_parse_markdown_document(_read_text(packet.files.get("questions").path if packet.files.get("questions") else None))

    witness_main, witness_extra = _pick_witness_sections(witness_doc)
    translation_main, translation_extra = _pick_translation_sections(translation_doc)
    witness_main_doc = MarkdownDocument(title=witness_doc.title, preamble=witness_doc.preamble, sections=witness_main)
    witness_extra_doc = MarkdownDocument(title="Witness Notes", preamble="", sections=witness_extra)
    translation_main_doc = MarkdownDocument(title=translation_doc.title, preamble=translation_doc.preamble, sections=translation_main)
    translation_extra_doc = MarkdownDocument(title="Translation Notes", preamble="", sections=translation_extra)

    display_page = _display_page_id(packet.page_id)
    title = f"{display_page} - {book_title}"

    content_sections = [
        *web_groups_to_template_sections(
            web_group_document_sections(witness_main_doc),
            kind="witness",
            preserve_linebreaks=True,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(translation_main_doc),
            kind="translation",
            preserve_linebreaks=False,
        ),
    ]
    interpretation_sections = [
        *web_groups_to_template_sections(
            web_group_document_sections(interpretation_doc),
            kind="interpretation",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(notes_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(witness_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(translation_extra_doc),
            kind="notes",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(terms_doc),
            kind="terms",
            preserve_linebreaks=False,
        ),
        *web_groups_to_template_sections(
            web_group_document_sections(questions_doc),
            kind="questions",
            preserve_linebreaks=False,
        ),
    ]

    page_assembly = _load_page_assembly(packet)
    if page_assembly is not None:
        witness_content = _build_witness_content_from_assembly(page_assembly)
        image_regions = _build_image_regions_from_assembly(page_assembly)
    else:
        witness_content = _build_witness_content(witness_doc, translation_doc)
        image_regions = []
    interpretation_content = _build_interpretation_content(
        interpretation_doc, notes_doc, terms_doc, questions_doc
    )

    folio = _build_folio_render(
        packet=packet,
        book_title=book_title,
        image_href=image_href,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        content_sections=content_sections,
        interpretation_sections=interpretation_sections,
        witness_content=witness_content,
        interpretation_content=interpretation_content,
        image_regions=image_regions,
    )
    cover_piece = _render_cover_piece(folio)
    content_piece = _render_content_piece(folio)
    interpretation_piece = _render_interpretation_piece(folio)
    spread_piece = _render_spread_piece(folio, content_piece=content_piece, interpretation_piece=interpretation_piece)
    folio_links = "\n".join(
        link
        for link in [
            f'<a class="folio-link" href="{escape(folio.navigation.home_href)}">Contents</a>' if folio.navigation.home_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.prev_href)}">&larr; Previous Folio</a>' if folio.navigation.prev_href else "",
            f'<a class="folio-link" href="{escape(folio.navigation.next_href)}">Next Folio &rarr;</a>' if folio.navigation.next_href else "",
        ]
        if link
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>
{web_site_css()}
</style>
</head>
<body>
<div class="book">
  {f'<div class="page cover active" data-page="0">{cover_piece}</div>' if include_cover else ''}
  <div class="page{' active' if not include_cover else ''}" data-page="1">
    {spread_piece}
  </div>
</div>
<div class="folio-links">
  {folio_links}
</div>

<script>
  (function() {{
    const cover = document.querySelector('.cover');
    const spread = document.querySelector('[data-page="1"]');
    const panel = document.getElementById('right-panel');
    const symbol = document.getElementById('flip-symbol');
    const symbolSvg = symbol ? symbol.querySelector('svg') : null;
    let rotation = 0;

    function openSpread() {{
      if (!cover || !spread) {{
        return;
      }}
      cover.classList.remove('active');
      spread.classList.add('active');
    }}

    if (cover) {{
      cover.addEventListener('click', openSpread);
    }}

    if (symbol && panel) {{
      symbol.addEventListener('click', (event) => {{
        event.stopPropagation();
        panel.classList.toggle('flipped');
        rotation += 180;
        if (symbolSvg) {{
          symbolSvg.style.transform = `rotate(${{rotation}}deg)`;
        }}
        const face = panel.querySelector(panel.classList.contains('flipped') ? '.face-interp' : '.face-witness');
        if (face) {{
          face.scrollTop = 0;
        }}
      }});
    }}

    const linkedNodes = Array.from(document.querySelectorAll('[data-region-id]'));
    function setLinkedActive(regionId, active) {{
      if (!regionId) {{
        return;
      }}
      linkedNodes.forEach((node) => {{
        if (node.dataset.regionId === regionId) {{
          node.classList.toggle('is-linked-active', active);
        }}
      }});
    }}

    linkedNodes.forEach((node) => {{
      node.addEventListener('mouseenter', () => setLinkedActive(node.dataset.regionId, true));
      node.addEventListener('mouseleave', () => setLinkedActive(node.dataset.regionId, false));
      node.addEventListener('focus', () => setLinkedActive(node.dataset.regionId, true));
      node.addEventListener('blur', () => setLinkedActive(node.dataset.regionId, false));
    }});

    window.addEventListener('keydown', (event) => {{
      if (cover && cover.classList.contains('active') && (event.key === 'ArrowRight' || event.key === ' ')) {{
        event.preventDefault();
        openSpread();
        return;
      }}
      if (event.key === 'ArrowLeft' && spread && spread.classList.contains('active') && {str(bool(prev_href)).lower()}) {{
        window.location.href = {json.dumps(prev_href or "")};
      }} else if (event.key === 'ArrowRight' && spread && spread.classList.contains('active') && {str(bool(next_href)).lower()}) {{
        window.location.href = {json.dumps(next_href or "")};
      }}
    }});
  }})();
</script>
</body>
</html>
"""
    return html, folio


def render_packet_folio_html(
    packet_path: Path,
    *,
    out_dir: Path | None = None,
    book_title: str | None = None,
    image_href: str | None = None,
    prev_href: str | None = None,
    next_href: str | None = None,
    home_href: str | None = None,
    include_cover: bool = True,
) -> RenderedPacketHtmlArtifact:
    packet = repair_packet_json(Path(packet_path))
    packet_path = Path(packet_path).resolve()
    packet_dir = packet_path.parent
    target_dir = out_dir.resolve() if out_dir else packet_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    html_path = target_dir / "edition_elegant.html"
    meta_path = target_dir / "edition_elegant_meta.json"
    folio_render_path = target_dir / "folio_render.json"
    image_href_value = image_href or _relpath(target_dir, Path(packet.source_image_path))
    resolved_book_title = book_title or packet.doc_id.replace("_", " ")

    html, folio_render = _render_folio_html(
        packet=packet,
        image_href=image_href_value,
        book_title=resolved_book_title,
        prev_href=prev_href,
        next_href=next_href,
        home_href=home_href,
        include_cover=include_cover,
    )
    html_path.write_text(html, encoding="utf-8")
    folio_render_path.write_text(folio_render.model_dump_json(indent=2), encoding="utf-8")

    packet.files["edition_html"].status = "draft"
    packet.files["edition_html"].note = "Rendered HTML folio edition"
    if "folio_render" in packet.files:
        packet.files["folio_render"].status = "draft"
        packet.files["folio_render"].note = "Structured folio.render JSON artifact"
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    meta = {
        "rendered_at": _utc_now(),
        "packet_path": str(packet_path),
        "html_path": str(html_path),
        "folio_render_path": str(folio_render_path),
        "source_image_path": packet.source_image_path,
        "image_href": image_href_value,
        "book_title": resolved_book_title,
        "previous_href": prev_href,
        "next_href": next_href,
        "home_href": home_href,
        "include_cover": include_cover,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketHtmlArtifact(
        packet_path=packet_path,
        html_path=html_path,
        folio_render_path=folio_render_path,
        meta_path=meta_path,
    )


def build_packet_book_site(
    packet_paths: list[Path],
    *,
    out_dir: Path,
    title: str | None = None,
) -> RenderedPacketSiteArtifact:
    if not packet_paths:
        raise ValueError("At least one packet path is required")

    resolved_packets = sorted((Path(path).resolve() for path in packet_paths), key=lambda path: _page_sort_key(repair_packet_json(path).page_id))
    first_packet = repair_packet_json(resolved_packets[0])
    doc_id = first_packet.doc_id
    book_title = title or doc_id.replace("_", " ")

    out_dir = out_dir.resolve()
    title_path = out_dir / "index.html"
    contents_path = out_dir / "contents.html"
    ending_path = out_dir / "ending.html"
    folio_dir = out_dir / "folios"
    image_dir = out_dir / "assets" / "images"
    folio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    copied_images: dict[str, str] = {}
    folio_paths: list[Path] = []
    page_entries: list[dict[str, str]] = []

    packets = [repair_packet_json(path) for path in resolved_packets]
    for packet in packets:
        source_image = Path(packet.source_image_path).resolve()
        image_target = image_dir / source_image.name
        if not image_target.exists():
            shutil.copy2(source_image, image_target)
        copied_images[packet.page_id] = _relpath(folio_dir / packet.page_id, image_target)

    for index, (packet_path, packet) in enumerate(zip(resolved_packets, packets)):
        page_out_dir = folio_dir / packet.page_id
        page_out_dir.mkdir(parents=True, exist_ok=True)
        prev_href = f"../{packets[index - 1].page_id}/edition_elegant.html" if index > 0 else "../../contents.html"
        next_href = f"../{packets[index + 1].page_id}/edition_elegant.html" if index < len(packets) - 1 else "../../ending.html"
        home_href = "../../contents.html"
        artifact = render_packet_folio_html(
            packet_path,
            out_dir=page_out_dir,
            book_title=book_title,
            image_href=copied_images[packet.page_id],
            prev_href=prev_href,
            next_href=next_href,
            home_href=home_href,
            include_cover=False,
        )
        folio_paths.append(artifact.html_path)
        page_entries.append(
            {
                "page_id": packet.page_id,
                "href": f"folios/{packet.page_id}/edition_elegant.html",
            }
        )

    shell_css = "\n".join(
        [
            "body { overflow: auto; }",
            ".contents-page { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 4rem 2rem; position: relative; }",
            ".contents-page::before { content: ''; position: absolute; inset: 0; background: radial-gradient(ellipse at 50% 30%, rgba(138,75,42,0.05) 0%, transparent 70%); pointer-events: none; }",
            ".contents-inner { width: min(480px, 100%); position: relative; }",
            ".contents-label { font-size: 0.65rem; letter-spacing: 0.35em; text-transform: uppercase; color: var(--faded); margin-bottom: 1.2rem; }",
            ".contents-title { font-size: 1.8rem; font-weight: 300; color: var(--parchment); letter-spacing: 0.04em; margin-bottom: 0.4rem; }",
            ".contents-subtitle { font-size: 0.75rem; color: var(--faded); letter-spacing: 0.12em; margin-bottom: 2.5rem; }",
            ".contents-rule { width: 36px; height: 1px; background: var(--accent); margin-bottom: 2rem; }",
            ".folio-list { display: grid; gap: 0; }",
            ".folio-item { border-top: 1px solid rgba(200,191,168,0.12); }",
            ".folio-item a { display: flex; justify-content: space-between; align-items: center; text-decoration: none; padding: 0.9rem 0; color: var(--panel-fg); transition: color 0.2s ease; }",
            ".folio-item a:hover { color: var(--parchment); }",
            ".folio-item .folio-name { font-size: 0.95rem; letter-spacing: 0.02em; }",
            ".folio-item .folio-arrow { font-size: 0.7rem; color: var(--faded); letter-spacing: 0.18em; text-transform: uppercase; transition: color 0.2s ease; }",
            ".folio-item a:hover .folio-arrow { color: var(--accent); }",
            ".folio-item:last-child { border-bottom: 1px solid rgba(200,191,168,0.12); }",
        ]
    )

    title_body = "\n".join(
        [
            '<div class="book">',
            '  <div class="page cover active" data-page="0">',
            '    <div class="cover-label">Palimpsest Codex</div>',
            f'    <h1 class="cover-title">{escape(book_title)}</h1>',
            f'    <div class="cover-subtitle">Assembled set of {len(page_entries)} folios</div>',
            '    <div class="cover-rule"></div>',
            '    <div class="cover-nav-hint"><span class="arrow">&rarr;</span> Press to open</div>',
            '  </div>',
            '</div>',
            '<script>',
            '  (function() {',
            '    const cover = document.querySelector(".cover");',
            '    function openContents() { window.location.href = "contents.html"; }',
            '    if (cover) { cover.addEventListener("click", openContents); }',
            '    window.addEventListener("keydown", (event) => {',
            '      if (event.key === "ArrowRight" || event.key === " ") {',
            '        event.preventDefault();',
            '        openContents();',
            '      }',
            '    });',
            '  })();',
            '</script>',
        ]
    )
    title_path.write_text(web_html_shell(title=book_title, body=title_body), encoding="utf-8")

    contents_body = "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner">',
            '    <div class="contents-label">Contents</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{len(page_entries)} folios</div>',
            '    <div class="contents-rule"></div>',
            '    <div class="folio-list">',
            *[
                f'      <div class="folio-item"><a href="{escape(entry["href"])}"><span class="folio-name">{escape(_display_page_id(entry["page_id"]))}</span><span class="folio-arrow">&rarr;</span></a></div>'
                for entry in page_entries
            ],
            "    </div>",
            "  </div>",
            "</div>",
        ]
    )
    contents_path.write_text(
        web_html_shell(title=f"{book_title} - Contents", body=contents_body, extra_css=shell_css),
        encoding="utf-8",
    )

    ending_body = "\n".join(
        [
            '<div class="contents-page">',
            '  <div class="contents-inner" style="text-align: center;">',
            '    <div class="contents-label">End of Codex</div>',
            f'    <div class="contents-title">{escape(book_title)}</div>',
            f'    <div class="contents-subtitle">{len(page_entries)} folios assembled</div>',
            '    <div class="contents-rule" style="margin-left: auto; margin-right: auto;"></div>',
            f'    <a href="contents.html" style="font-size: 0.7rem; letter-spacing: 0.2em; text-transform: uppercase; color: var(--faded); text-decoration: none; transition: color 0.2s ease;"',
            '       onmouseover="this.style.color=\'var(--parchment)\'" onmouseout="this.style.color=\'var(--faded)\'">',
            '      &larr; Return to contents</a>',
            "  </div>",
            "</div>",
        ]
    )
    ending_path.write_text(
        web_html_shell(title=f"{book_title} - End", body=ending_body, extra_css=shell_css),
        encoding="utf-8",
    )

    meta_path = out_dir / "site_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "doc_id": doc_id,
        "title": book_title,
        "packet_paths": [str(path) for path in resolved_packets],
        "folio_paths": [str(path) for path in folio_paths],
        "index_path": str(title_path),
        "contents_path": str(contents_path),
        "ending_path": str(ending_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedPacketSiteArtifact(
        doc_id=doc_id,
        index_path=title_path,
        contents_path=contents_path,
        ending_path=ending_path,
        folio_paths=folio_paths,
        meta_path=meta_path,
    )
