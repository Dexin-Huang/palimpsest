from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.edition_fonts import resolve_edition_font_policy
from palimpsest.models.packet import PacketContinuity, PacketFileRef, PagePacket, PacketWorkflow
from palimpsest.page_prepare import PreparedPageArtifact, prepare_image


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


def _stub_text(title: str, bullets: list[str]) -> str:
    lines = [f"# {title}", ""]
    for bullet in bullets:
        lines.append(f"- {bullet}")
    lines.append("")
    return "\n".join(lines)


def _latex_template(page_id: str) -> str:
    font_lines = resolve_edition_font_policy().latex_lines()
    return "\n".join(
        [
            r"\documentclass[11pt]{article}",
            r"\usepackage[a4paper,margin=1in]{geometry}",
            r"\usepackage{paracol}",
            r"\usepackage{fontspec}",
            r"\usepackage{xeCJK}",
            *font_lines,
            r"\setlength{\parindent}{0pt}",
            r"\setlength{\parskip}{0.5em}",
            r"\begin{document}",
            f"\\section*{{{page_id}}}",
            r"\begin{paracol}{2}",
            r"\switchcolumn[0]*",
            r"\subsection*{Witness}",
            r"% BEGIN WITNESS CONTENT",
            r"% Fill from witness.md. Keep diplomatic witness on this side.",
            r"% END WITNESS CONTENT",
            r"\switchcolumn",
            r"\subsection*{Translation And Interpretation}",
            r"% BEGIN SYNTHESIS CONTENT",
            r"% Fill from translation.md and interpretation.md.",
            r"% END SYNTHESIS CONTENT",
            r"\end{paracol}",
            r"\end{document}",
            "",
        ]
    )


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

    if not witness_path.exists():
        witness_path.write_text(
            _stub_text(
                "Witness",
                [
                    "Raw diplomatic witness goes here.",
                    "Preserve line breaks and uncertainty.",
                    "Do not add broad interpretation here.",
                ],
            ),
            encoding="utf-8",
        )
    if not notes_path.exists():
        notes_path.write_text(
            _stub_text(
                "Notes",
                [
                    "Local observations about this page.",
                    "Visible parallels, repeated terms, layout signals.",
                    "Questions that arise while reading.",
                ],
            ),
            encoding="utf-8",
        )
    if not translation_path.exists():
        translation_path.write_text(
            _stub_text(
                "Working Translation",
                [
                    "Keep this provisional.",
                    "Mark uncertain renderings explicitly.",
                ],
            ),
            encoding="utf-8",
        )
    if not interpretation_path.exists():
        interpretation_path.write_text(
            _stub_text(
                "Interpretation",
                [
                    "What is this page doing?",
                    "What is direct evidence vs probable inference?",
                    "How does it connect to adjacent pages?",
                ],
            ),
            encoding="utf-8",
        )
    if not terms_path.exists():
        terms_path.write_text(
            _stub_text(
                "Names And Terms",
                [
                    "List visible names, works, places, and technical terms.",
                    "Keep glosses short and evidence-bound.",
                ],
            ),
            encoding="utf-8",
        )
    if not questions_path.exists():
        questions_path.write_text(
            _stub_text(
                "Open Questions",
                [
                    "What remains unresolved on this page?",
                    "What should be checked on later folios?",
                ],
            ),
            encoding="utf-8",
        )
    if not edition_tex_path.exists():
        edition_tex_path.write_text(_latex_template(image_path.stem), encoding="utf-8")

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
