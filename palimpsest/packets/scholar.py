from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from palimpsest.agent_sdk import AgentRunResult, run_agent_prompt
from palimpsest.packets.templates import packet_format_contract_block, packet_heading_contract_block
from palimpsest.models import ALLOWED_PACKET_STATUSES, PacketFileRef, PagePacket


TASK_CHOICES = [
    "advance",
    "fill_witness",
    "annotate",
    "translate",
    "interpret",
]
PACKET_NEXT_ACTIONS = (
    "fill_witness",
    "fill_notes",
    "draft_translation",
    "draft_interpretation",
    "review_terms",
    "review_questions",
    "prepare_section_synthesis",
    "complete",
)
_STATUS_ALIASES = {
    "filled": "draft",
    "done": "complete",
    "completed": "complete",
    "in_progress": "started",
    "in-progress": "started",
    "review": "reviewed",
    "final": "complete",
}
_NEXT_ACTION_ALIASES = {
    "annotate": "fill_notes",
    "translate": "draft_translation",
    "interpret": "draft_interpretation",
    "notes": "fill_notes",
    "translation": "draft_translation",
    "interpretation": "draft_interpretation",
    "edition": "prepare_section_synthesis",
    "synthesize": "prepare_section_synthesis",
    "done": "complete",
}
@dataclass
class PacketScholarInputs:
    packet_path: Path
    workspace: Path
    packet: PagePacket
    witness_source: Path | None
    context_sources: list[Path]


def _materialize_input_copy(src: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return target


def _continuity_source_paths(packet: PagePacket) -> list[Path]:
    continuity = packet.continuity
    candidates = [
        continuity.previous_handoff_path,
        continuity.window_synthesis_path,
        continuity.previous_packet_path,
    ]
    result: list[Path] = []
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.exists():
            result.append(path.resolve())
    return result


def _normalize_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_PACKET_STATUSES:
        return raw
    if raw in _STATUS_ALIASES:
        return _STATUS_ALIASES[raw]
    return "draft" if raw else "empty"


def _infer_next_action(payload: dict) -> str:
    files = payload.get("files") or {}
    if not isinstance(files, dict):
        return "fill_witness"

    order = [
        ("witness", "fill_witness"),
        ("notes", "fill_notes"),
        ("translation", "draft_translation"),
        ("interpretation", "draft_interpretation"),
        ("terms", "review_terms"),
        ("questions", "review_questions"),
    ]
    for key, action in order:
        ref = files.get(key) or {}
        if not isinstance(ref, dict):
            return action
        if _normalize_status(ref.get("status")) in {"empty", "started"}:
            return action
    return "prepare_section_synthesis"


def repair_packet_json(packet_path: Path) -> PagePacket:
    packet_path = packet_path.resolve()
    payload: dict[str, Any] = json.loads(packet_path.read_text(encoding="utf-8"))

    files = payload.get("files") or {}
    if isinstance(files, dict):
        for ref in files.values():
            if isinstance(ref, dict):
                ref["status"] = _normalize_status(ref.get("status"))
    else:
        files = {}
        payload["files"] = files

    packet_dir = packet_path.parent
    edition_html_path = str((packet_dir / "index.html").resolve())
    folio_render_path = str((packet_dir / "render.json").resolve())
    layout_probe_path = str((packet_dir / "layout_probe" / "layout_probe.json").resolve())
    layout_overlay_path = str((packet_dir / "layout_probe" / "layout_overlay.png").resolve())
    region_reads_path = str((packet_dir / "layout_probe" / "region_reads.json").resolve())
    section_resolution_path = str((packet_dir / "layout_probe" / "section_resolution.json").resolve())
    box_cleanup_path = str((packet_dir / "layout_probe" / "box_cleanup.json").resolve())
    page_assembly_path = str((packet_dir / "layout_probe" / "page_assembly.json").resolve())
    if "edition_html" not in files:
        files["edition_html"] = PacketFileRef(
            kind="edition_html",
            path=edition_html_path,
            status="draft" if Path(edition_html_path).exists() else "empty",
            note="Rendered HTML folio edition" if Path(edition_html_path).exists() else None,
        ).model_dump()
    elif isinstance(files["edition_html"], dict):
        files["edition_html"]["kind"] = "edition_html"
        files["edition_html"]["path"] = edition_html_path
        if Path(files["edition_html"]["path"]).exists() and _normalize_status(files["edition_html"].get("status")) == "empty":
            files["edition_html"]["status"] = "draft"
            files["edition_html"]["note"] = files["edition_html"].get("note") or "Rendered HTML folio edition"
    if "folio_render" not in files:
        files["folio_render"] = PacketFileRef(
            kind="folio_render",
            path=folio_render_path,
            status="draft" if Path(folio_render_path).exists() else "empty",
            note="Structured folio.render JSON artifact" if Path(folio_render_path).exists() else None,
        ).model_dump()
    elif isinstance(files["folio_render"], dict):
        files["folio_render"].setdefault("kind", "folio_render")
        files["folio_render"]["path"] = folio_render_path
        if Path(files["folio_render"]["path"]).exists() and _normalize_status(files["folio_render"].get("status")) == "empty":
            files["folio_render"]["status"] = "draft"
            files["folio_render"]["note"] = files["folio_render"].get("note") or "Structured folio.render JSON artifact"
    layout_defaults = {
        "layout_probe": (layout_probe_path, "Coarse layout probe for region-first reconstruction"),
        "layout_overlay": (layout_overlay_path, "Overlay preview of coarse layout regions"),
        "region_reads": (region_reads_path, "Full transcription reads for each coarse region"),
        "section_resolution": (section_resolution_path, "Canonical text ownership per coarse region"),
        "box_cleanup": (box_cleanup_path, "Targeted cleanup for overlapping region pairs"),
        "page_assembly": (page_assembly_path, "Deterministic assembly from region reads"),
    }
    for key, (default_path, default_note) in layout_defaults.items():
        if key not in files:
            files[key] = PacketFileRef(
                kind=key,
                path=default_path,
                status="draft" if Path(default_path).exists() else "empty",
                note=default_note if Path(default_path).exists() else None,
            ).model_dump()
            continue
        if not isinstance(files[key], dict):
            continue
        files[key].setdefault("kind", key)
        current_path = str(files[key].get("path") or "").strip()
        if not current_path:
            files[key]["path"] = default_path
        if Path(files[key]["path"]).exists() and _normalize_status(files[key].get("status")) == "empty":
            files[key]["status"] = "draft"
            files[key]["note"] = files[key].get("note") or default_note

    workflow = payload.get("workflow") or {}
    if not isinstance(workflow, dict):
        workflow = {}
        payload["workflow"] = workflow
    next_action = str(workflow.get("next_action") or "").strip()
    next_action = _NEXT_ACTION_ALIASES.get(next_action, next_action)
    if next_action not in PACKET_NEXT_ACTIONS:
        next_action = _infer_next_action(payload)
    workflow["next_action"] = next_action

    packet = PagePacket.model_validate(payload)
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def prepare_packet_workspace(
    packet_path: Path,
    *,
    witness_path: Path | None = None,
    context_paths: list[Path] | None = None,
) -> PacketScholarInputs:
    packet_path = packet_path.resolve()
    if not packet_path.exists():
        raise FileNotFoundError(f"Packet not found: {packet_path}")
    packet = repair_packet_json(packet_path)
    workspace = packet_path.parent.resolve()

    inputs_dir = workspace / "inputs"
    copied_witness: Path | None = None
    copied_contexts: list[Path] = []
    seen_contexts: set[Path] = set()

    if witness_path is not None:
        witness_path = witness_path.resolve()
        copied_witness = _materialize_input_copy(witness_path, inputs_dir / "witness_source.md")

    all_context_paths = [*_continuity_source_paths(packet), *((context_paths or []))]
    for index, context in enumerate(all_context_paths, start=1):
        context = context.resolve()
        if context in seen_contexts:
            continue
        seen_contexts.add(context)
        safe_name = f"context_{index:02d}_{context.stem}{context.suffix or '.md'}"
        copied_contexts.append(_materialize_input_copy(context, inputs_dir / safe_name))

    return PacketScholarInputs(
        packet_path=packet_path,
        workspace=workspace,
        packet=packet,
        witness_source=copied_witness,
        context_sources=copied_contexts,
    )


def build_packet_scholar_prompt(
    packet_inputs: PacketScholarInputs,
    *,
    task: str,
) -> str:
    if task not in TASK_CHOICES:
        valid = ", ".join(TASK_CHOICES)
        raise ValueError(f"Unsupported packet scholar task {task!r}. Expected one of: {valid}")

    lines = [
        "You are advancing one Palimpsest page.packet.",
        "",
        f"Packet file: {packet_inputs.packet_path.name}",
        f"Task: {task}",
        "",
        "Start by reading these files in this order:",
        "1. packet.json",
        "2. witness.md",
        "3. notes.md",
        "4. translation.md",
        "5. interpretation.md",
        "6. terms.md",
        "7. questions.md",
    ]

    if packet_inputs.witness_source is not None:
        lines.extend(
            [
                "",
                "New witness input is available:",
                f"- inputs/{packet_inputs.witness_source.name}",
            ]
        )

    if packet_inputs.context_sources:
        lines.append("")
        lines.append("Local context inputs are available:")
        for path in packet_inputs.context_sources:
            lines.append(f"- inputs/{path.name}")

    lines.extend(
        [
            "",
            "Task rules:",
            "- Work only inside this packet workspace.",
            "- Do not inspect the wider repository.",
            "- Treat witness text as evidence. Never invent witness lines.",
            "- Keep direct evidence separate from probable inference.",
            "- Update packet.json statuses and workflow.next_action before finishing.",
            f"- Allowed packet statuses: {', '.join(ALLOWED_PACKET_STATUSES)}.",
            "- Never invent status labels such as filled, done, or in_progress.",
            f"- Prefer workflow.next_action values from: {', '.join(PACKET_NEXT_ACTIONS)}.",
            "- Keep each file focused on its layer.",
            "",
            "Layer rules:",
            "- witness.md: diplomatic witness only.",
            "- notes.md: page-local observations and cross-page pointers.",
            "- translation.md: provisional translation, explicit about uncertainty.",
            "- interpretation.md: short evidence-bound interpretation.",
            "- terms.md: visible names, works, places, and technical terms.",
            "- questions.md: unresolved items or checks for later pages.",
            "",
            "Packet file heading contract:",
            "- Keep these heading shapes stable so folio.render.json can be assembled deterministically.",
            "- You may improve the wording inside sections, but do not collapse files into freeform essays.",
            "- In witness.md and translation.md, rename Reading Unit headings to visible labels when obvious.",
            "```md",
            packet_heading_contract_block(page_unit=packet_inputs.packet.page_unit),
            "```",
            "",
            "Packet file internal syntax contract:",
            "- The folio compiler expects these local markers and matching unit labels.",
            "```md",
            packet_format_contract_block(page_unit=packet_inputs.packet.page_unit),
            "```",
        ]
    )

    if task == "fill_witness":
        lines.extend(
            [
                "",
                "Specific objective for fill_witness:",
                "- If witness_source.md exists, use it to populate witness.md faithfully.",
                "- Preserve witness uncertainty markers such as [hill/heil/hail], [dominus?], or (dominus). Never reduce uncertainty to a bare [??].",
                "- Keep witness.md structured with Header / Page Number / Marginalia / Main Text markers.",
                "- Do not put commentary, glosses, or modern explanation inside Main Text.",
                "- Then update packet.json so witness status becomes draft and workflow.next_action becomes fill_notes.",
            ]
        )
    elif task == "annotate":
        lines.extend(
            [
                "",
                "Specific objective for annotate:",
                "- Work from witness.md and any context inputs.",
                "- Improve notes.md, terms.md, and questions.md.",
                "- In terms.md, use compact entries such as - **term** (romanization): gloss.",
                "- Do not broaden into a long essay.",
            ]
        )
    elif task == "translate":
        lines.extend(
            [
                "",
                "Specific objective for translate:",
                "- Draft or improve translation.md conservatively from witness and context.",
                "- Mark uncertainty explicitly.",
                "- Match witness unit labels exactly before the colon, e.g. Left Column: Heaven Endows Constant Nature.",
                "- Keep translation inside **Main Text** blocks only.",
            ]
        )
    elif task == "interpret":
        lines.extend(
            [
                "",
                "Specific objective for interpret:",
                "- Draft or improve interpretation.md.",
                "- Use the phrase 'Probable inference' for non-explicit claims.",
                "- Keep Direct Evidence and Probable Inference as separate top-level sections, not nested essay prose.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Specific objective for advance:",
                "- Choose the next sensible scholarly step based on packet.json and file contents.",
                "- Prefer fill_witness first, then annotate, then translate, then interpret, then prepare_section_synthesis.",
            ]
        )

    lines.extend(
        [
            "",
            "Before you finish:",
            "- Save all touched files.",
            "- Update packet.json statuses.",
            "- Set workflow.next_action to the next unresolved step.",
            "- Reply with a brief summary of what changed.",
        ]
    )

    return "\n".join(lines).strip()


async def run_packet_scholar(
    *,
    packet_path: Path,
    model: str,
    task: str = "advance",
    witness_path: Path | None = None,
    context_paths: list[Path] | None = None,
    max_turns: int = 80,
    max_budget_usd: float | None = None,
    max_thinking_tokens: int | None = None,
    permission_mode: str = "default",
) -> AgentRunResult:
    packet_inputs = prepare_packet_workspace(
        packet_path,
        witness_path=witness_path,
        context_paths=context_paths or [],
    )
    prompt = build_packet_scholar_prompt(packet_inputs, task=task)
    result = await run_agent_prompt(
        prompt=prompt,
        workspace=packet_inputs.workspace,
        model=model,
        profile="packet_scholar",
        with_web_search=False,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        max_thinking_tokens=max_thinking_tokens,
        permission_mode=permission_mode,
    )
    repair_packet_json(packet_inputs.packet_path)
    return result
