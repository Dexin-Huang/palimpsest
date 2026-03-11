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
from palimpsest.packets.scholar import repair_packet_json
from palimpsest.web import (
    FolioTemplateSection,
    MarkdownDocument,
    MarkdownSection,
    MarkdownSectionGroup,
    build_folio_render as web_build_folio_render,
    display_page_id as web_display_page_id,
    group_document_sections as web_group_document_sections,
    groups_to_template_sections as web_groups_to_template_sections,
    html_shell as web_html_shell,
    parse_markdown_document as web_parse_markdown_document,
    page_sort_key as web_page_sort_key,
    render_content_piece as web_render_content_piece,
    render_cover_piece as web_render_cover_piece,
    render_interpretation_piece as web_render_interpretation_piece,
    render_spread_piece as web_render_spread_piece,
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
    return web_page_sort_key(page_id)


def _display_page_id(page_id: str) -> str:
    return web_display_page_id(page_id)


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

    folio = web_build_folio_render(
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
        page_label=display_page,
        created_at=_utc_now(),
    )
    cover_piece = web_render_cover_piece(folio)
    content_piece = web_render_content_piece(folio)
    interpretation_piece = web_render_interpretation_piece(folio)
    spread_piece = web_render_spread_piece(folio, content_piece=content_piece, interpretation_piece=interpretation_piece)
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

    html_path = target_dir / "index.html"
    meta_path = target_dir / "render_meta.json"
    folio_render_path = target_dir / "render.json"
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
        prev_href = f"../{packets[index - 1].page_id}/index.html" if index > 0 else "../../contents.html"
        next_href = f"../{packets[index + 1].page_id}/index.html" if index < len(packets) - 1 else "../../ending.html"
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
                "href": f"folios/{packet.page_id}/index.html",
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
