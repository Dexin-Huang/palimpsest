from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Iterable

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.edition_fonts import resolve_edition_font_policy
from palimpsest.models.packet import PacketContinuity, PacketFileRef, PagePacket, PacketWorkflow
from palimpsest.page_prepare import PreparedPageArtifact, prepare_image
from palimpsest.packet_templates import packet_markdown_template


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _resolve_doc_id(image_path: Path) -> str:
    image_path = image_path.resolve()
    if image_path.parent.name in {"images", "images_cleaned"}:
        return image_path.parent.parent.name
    return image_path.parent.name


def _default_output_dir(image_path: Path) -> Path:
    if image_path.parent.name in {"images", "images_cleaned"}:
        return image_path.parent.parent / "experiments" / f"{image_path.stem}_packet"
    return image_path.parent / f"{image_path.stem}_packet"


def _tex_relpath(base_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path.resolve(), start=base_dir.resolve())).as_posix()


def _latex_template(page_id: str, *, source_image_path: Path, packet_dir: Path) -> str:
    font_lines = resolve_edition_font_policy().latex_lines()
    image_relpath = _tex_relpath(packet_dir, source_image_path)
    return "\n".join(
        [
            r"\documentclass[11pt]{article}",
            r"\usepackage[paperwidth=14in,paperheight=8.5in,margin=0.55in]{geometry}",
            r"\usepackage{paracol}",
            r"\usepackage{graphicx}",
            r"\usepackage{xcolor}",
            r"\usepackage{fontspec}",
            r"\usepackage{xeCJK}",
            r"\usepackage{ragged2e}",
            r"\usepackage{setspace}",
            *font_lines,
            r"\definecolor{PaperTone}{HTML}{F7F1E6}",
            r"\definecolor{InkTone}{HTML}{1E1917}",
            r"\definecolor{AccentTone}{HTML}{8A4B2A}",
            r"\definecolor{PanelTone}{HTML}{181614}",
            r"\definecolor{RuleTone}{HTML}{D8C7B0}",
            r"\pagecolor{PaperTone}",
            r"\color{InkTone}",
            r"\pagestyle{empty}",
            r"\setlength{\parindent}{0pt}",
            r"\setlength{\parskip}{0.45em}",
            r"\setlength{\columnsep}{1.3em}",
            r"\columnratio{0.58,0.42}",
            r"\setstretch{1.03}",
            r"\newcommand{\PacketSection}[1]{\vspace{0.45em}{\large\bfseries\color{AccentTone}#1}\par{\color{RuleTone}\rule{\linewidth}{0.7pt}}\par\vspace{0.45em}\color{InkTone}}",
            r"\begin{document}",
            r"\noindent",
            r"\colorbox{PanelTone}{%",
            r"\parbox{\dimexpr\linewidth-2\fboxsep\relax}{%",
            r"\color{white}\vspace{0.45em}%",
            rf"{{\Large\bfseries {page_id}}}\hfill{{\small\scshape Palimpsest Edition}}%",
            r"\vspace{0.35em}}%",
            r"}",
            r"\vspace{0.7em}",
            r"\begin{paracol}{2}",
            r"\switchcolumn[0]*",
            r"\PacketSection{Source Image}",
            r"\begin{center}",
            r"\setlength{\fboxsep}{10pt}",
            r"\colorbox{PanelTone}{%",
            r"\parbox[c][0.84\textheight][c]{0.94\linewidth}{%",
            r"\centering",
            rf"\includegraphics[width=0.92\linewidth,height=0.76\textheight,keepaspectratio]{{{image_relpath}}}",
            r"\par\vspace{0.75em}{\color{white}\footnotesize\scshape Source witness image}}%",
            r"}",
            r"\end{center}",
            r"\footnotesize\color{AccentTone}Shown verbatim for side-by-side reading against the witness and interpretation.",
            r"\switchcolumn",
            r"\RaggedRight",
            r"\small",
            r"\PacketSection{Witness}",
            r"% BEGIN WITNESS CONTENT",
            r"% Fill from witness.md. Keep diplomatic witness on this side.",
            r"% END WITNESS CONTENT",
            r"\PacketSection{Direct Translation}",
            r"% BEGIN TRANSLATION CONTENT",
            r"% Keep this page close to the witness. Translation only.",
            r"% END TRANSLATION CONTENT",
            r"\end{paracol}",
            r"\newpage",
            r"\noindent",
            r"\colorbox{PanelTone}{%",
            r"\parbox{\dimexpr\linewidth-2\fboxsep\relax}{%",
            r"\color{white}\vspace{0.35em}%",
            rf"{{\Large\bfseries {page_id}}}\hfill{{\small\scshape Commentary And Apparatus}}%",
            r"\vspace{0.25em}}%",
            r"}",
            r"\vspace{0.7em}",
            r"\RaggedRight",
            r"\PacketSection{Interpretation}",
            r"% BEGIN COMMENTARY CONTENT",
            r"% Move broader interpretation, codicology, and description here.",
            r"% END COMMENTARY CONTENT",
            r"\PacketSection{Notes}",
            r"% BEGIN NOTES CONTENT",
            r"% Optional notes synced from notes.md or scholar edits.",
            r"% END NOTES CONTENT",
            r"\PacketSection{Names And Terms}",
            r"% BEGIN TERMS CONTENT",
            r"% Optional named entities and glosses.",
            r"% END TERMS CONTENT",
            r"\PacketSection{Open Questions}",
            r"% BEGIN QUESTIONS CONTENT",
            r"% Optional unresolved readings and follow-up checks.",
            r"% END QUESTIONS CONTENT",
            r"\end{document}",
            "",
        ]
    )


_WITNESS_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN WITNESS CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END WITNESS CONTENT)",
    re.DOTALL,
)
_TRANSLATION_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN TRANSLATION CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END TRANSLATION CONTENT)",
    re.DOTALL,
)
_COMMENTARY_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN COMMENTARY CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END COMMENTARY CONTENT)",
    re.DOTALL,
)
_NOTES_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN NOTES CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END NOTES CONTENT)",
    re.DOTALL,
)
_TERMS_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN TERMS CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END TERMS CONTENT)",
    re.DOTALL,
)
_QUESTIONS_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN QUESTIONS CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END QUESTIONS CONTENT)",
    re.DOTALL,
)
_LEGACY_SYNTHESIS_BLOCK_RE = re.compile(
    r"(?P<begin>% BEGIN SYNTHESIS CONTENT\s*)(?P<body>.*?)(?P<end>\s*% END SYNTHESIS CONTENT)",
    re.DOTALL,
)


def _split_legacy_synthesis_body(body: str) -> tuple[str, str]:
    cleaned = body.strip("\n")
    if not cleaned:
        return "", ""
    match = re.search(r"(\\textbf\{Interpretation\}|\\subsection\*\{Interpretation\})", cleaned)
    if match is None:
        return cleaned, ""
    translation = cleaned[: match.start()].strip("\n")
    commentary = cleaned[match.start() :].strip("\n")
    return translation, commentary


def _replace_block(template: str, pattern: re.Pattern[str], body: str) -> str:
    return pattern.sub(
        lambda match: (
            f"{match.group('begin')}\n\n{body}\n\n{match.group('end').lstrip()}"
            if body
            else f"{match.group('begin')}{match.group('end')}"
        ),
        template,
        count=1,
    )


def sync_packet_edition_template(packet: PagePacket) -> bool:
    edition_ref = packet.files.get("edition_tex")
    if edition_ref is None:
        return False

    edition_path = Path(edition_ref.path).resolve()
    if not edition_path.exists():
        return False

    existing = edition_path.read_text(encoding="utf-8")
    witness_match = _WITNESS_BLOCK_RE.search(existing)
    if witness_match is None:
        return False

    witness_body = witness_match.group("body").strip("\n")

    translation_match = _TRANSLATION_BLOCK_RE.search(existing)
    commentary_match = _COMMENTARY_BLOCK_RE.search(existing)
    notes_match = _NOTES_BLOCK_RE.search(existing)
    terms_match = _TERMS_BLOCK_RE.search(existing)
    questions_match = _QUESTIONS_BLOCK_RE.search(existing)
    legacy_synthesis_match = _LEGACY_SYNTHESIS_BLOCK_RE.search(existing)

    if translation_match is not None:
        translation_body = translation_match.group("body").strip("\n")
    else:
        translation_body = ""

    if commentary_match is not None:
        commentary_body = commentary_match.group("body").strip("\n")
    else:
        commentary_body = ""

    notes_body = notes_match.group("body").strip("\n") if notes_match is not None else ""
    terms_body = terms_match.group("body").strip("\n") if terms_match is not None else ""
    questions_body = questions_match.group("body").strip("\n") if questions_match is not None else ""

    if legacy_synthesis_match is not None and not (translation_body or commentary_body):
        translation_body, commentary_body = _split_legacy_synthesis_body(
            legacy_synthesis_match.group("body")
        )

    template = _latex_template(
        packet.page_id,
        source_image_path=Path(packet.source_image_path),
        packet_dir=edition_path.parent,
    )
    template = _replace_block(template, _WITNESS_BLOCK_RE, witness_body)
    template = _replace_block(template, _TRANSLATION_BLOCK_RE, translation_body)
    template = _replace_block(template, _COMMENTARY_BLOCK_RE, commentary_body)
    template = _replace_block(template, _NOTES_BLOCK_RE, notes_body)
    template = _replace_block(template, _TERMS_BLOCK_RE, terms_body)
    template = _replace_block(template, _QUESTIONS_BLOCK_RE, questions_body)

    if template != existing:
        edition_path.write_text(template, encoding="utf-8")
        return True
    return False


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

    witness_path = target_dir / "witness.md"
    notes_path = target_dir / "notes.md"
    translation_path = target_dir / "translation.md"
    interpretation_path = target_dir / "interpretation.md"
    terms_path = target_dir / "terms.md"
    questions_path = target_dir / "questions.md"
    edition_tex_path = target_dir / "edition_spread.tex"
    edition_pdf_path = target_dir / "edition_spread.pdf"
    edition_html_path = target_dir / "index.html"
    folio_render_path = target_dir / "render.json"
    layout_probe_path = target_dir / "layout_probe" / "layout_probe.json"
    layout_overlay_path = target_dir / "layout_probe" / "layout_overlay.png"
    region_orientations_path = target_dir / "layout_probe" / "region_reads.json"
    section_resolution_path = target_dir / "layout_probe" / "section_resolution.json"
    box_cleanup_path = target_dir / "layout_probe" / "box_cleanup.json"
    page_assembly_path = target_dir / "layout_probe" / "page_assembly.json"

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
    if not edition_tex_path.exists():
        edition_tex_path.write_text(
            _latex_template(image_path.stem, source_image_path=image_path, packet_dir=target_dir),
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
            "edition_tex": PacketFileRef(kind="edition_tex", path=str(edition_tex_path), status="empty"),
            "edition_pdf": PacketFileRef(kind="edition_pdf", path=str(edition_pdf_path), status="empty"),
            "edition_html": PacketFileRef(kind="edition_html", path=str(edition_html_path), status="empty"),
            "folio_render": PacketFileRef(kind="folio_render", path=str(folio_render_path), status="empty"),
            "layout_probe": PacketFileRef(kind="layout_probe", path=str(layout_probe_path), status="empty"),
            "layout_overlay": PacketFileRef(kind="layout_overlay", path=str(layout_overlay_path), status="empty"),
            "region_orientations": PacketFileRef(kind="region_orientations", path=str(region_orientations_path), status="empty"),
            "section_resolution": PacketFileRef(kind="section_resolution", path=str(section_resolution_path), status="empty"),
            "box_cleanup": PacketFileRef(kind="box_cleanup", path=str(box_cleanup_path), status="empty"),
            "page_assembly": PacketFileRef(kind="page_assembly", path=str(page_assembly_path), status="empty"),
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

    packet_path = target_dir / "packet.json"
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    meta_path = target_dir / "packet_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "source_image_path": str(image_path),
        "packet_path": str(packet_path),
        "prepared_image_path": str(prepared.prepared_image_path) if prepared is not None else None,
        "edition_font_policy": resolve_edition_font_policy().as_dict(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return packet, packet_path


def attach_layout_probe(packet_path: Path, probe_dir: Path) -> PagePacket:
    packet_path = packet_path.resolve()
    probe_dir = probe_dir.resolve()
    packet = PagePacket.model_validate_json(packet_path.read_text(encoding="utf-8"))

    mapping = {
        "layout_probe": probe_dir / "layout_probe.json",
        "layout_overlay": probe_dir / "layout_overlay.png",
        "region_orientations": probe_dir / "region_reads.json",
        "section_resolution": probe_dir / "section_resolution.json",
        "box_cleanup": probe_dir / "box_cleanup.json",
        "page_assembly": probe_dir / "page_assembly.json",
    }

    for key, resolved_path in mapping.items():
        ref = packet.files.get(key)
        if ref is None:
            ref = PacketFileRef(kind=key, path=str(resolved_path), status="empty")
            packet.files[key] = ref
        else:
            ref.path = str(resolved_path)
        if resolved_path.exists():
            ref.status = "draft"

    notes = list(packet.notes or [])
    note = "Layout probe attached."
    if note not in notes:
        notes.append(note)
    packet.notes = notes

    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


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
