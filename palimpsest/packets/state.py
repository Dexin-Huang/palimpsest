from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from palimpsest.contracts import (
    box_cleanup_path,
    folio_render_path,
    layout_probe_dir,
    layout_probe_json_path,
    layout_overlay_path,
    page_assembly_json_path,
    region_reads_path,
    render_html_path,
    section_resolution_path,
)
from palimpsest.models.packet import ALLOWED_PACKET_STATUSES, PacketFileRef, PagePacket


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


def normalize_packet_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_PACKET_STATUSES:
        return raw
    if raw in _STATUS_ALIASES:
        return _STATUS_ALIASES[raw]
    return "draft" if raw else "empty"


def infer_packet_next_action(payload: dict) -> str:
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
        if normalize_packet_status(ref.get("status")) in {"empty", "started"}:
            return action
    return "prepare_section_synthesis"


def repair_packet_json(packet_path: Path) -> PagePacket:
    packet_path = packet_path.resolve()
    payload: dict[str, Any] = json.loads(packet_path.read_text(encoding="utf-8"))

    files = payload.get("files") or {}
    if isinstance(files, dict):
        for ref in files.values():
            if isinstance(ref, dict):
                ref["status"] = normalize_packet_status(ref.get("status"))
    else:
        files = {}
        payload["files"] = files

    packet_dir = packet_path.parent
    probe_dir = layout_probe_dir(packet_dir)
    edition_html_path = str(render_html_path(packet_dir).resolve())
    resolved_folio_render_path = str(folio_render_path(packet_dir).resolve())
    resolved_layout_probe_path = str(layout_probe_json_path(probe_dir).resolve())
    resolved_layout_overlay_path = str(layout_overlay_path(probe_dir).resolve())
    resolved_region_reads_path = str(region_reads_path(probe_dir).resolve())
    resolved_section_resolution_path = str(section_resolution_path(probe_dir).resolve())
    resolved_box_cleanup_path = str(box_cleanup_path(probe_dir).resolve())
    resolved_page_assembly_path = str(page_assembly_json_path(probe_dir).resolve())

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
        if Path(files["edition_html"]["path"]).exists() and normalize_packet_status(files["edition_html"].get("status")) == "empty":
            files["edition_html"]["status"] = "draft"
            files["edition_html"]["note"] = files["edition_html"].get("note") or "Rendered HTML folio edition"

    if "folio_render" not in files:
        files["folio_render"] = PacketFileRef(
            kind="folio_render",
            path=resolved_folio_render_path,
            status="draft" if Path(resolved_folio_render_path).exists() else "empty",
            note="Structured folio.render JSON artifact" if Path(resolved_folio_render_path).exists() else None,
        ).model_dump()
    elif isinstance(files["folio_render"], dict):
        files["folio_render"].setdefault("kind", "folio_render")
        files["folio_render"]["path"] = resolved_folio_render_path
        if Path(files["folio_render"]["path"]).exists() and normalize_packet_status(files["folio_render"].get("status")) == "empty":
            files["folio_render"]["status"] = "draft"
            files["folio_render"]["note"] = files["folio_render"].get("note") or "Structured folio.render JSON artifact"

    layout_defaults = {
        "layout_probe": (resolved_layout_probe_path, "Coarse layout probe for region-first reconstruction"),
        "layout_overlay": (resolved_layout_overlay_path, "Overlay preview of coarse layout regions"),
        "region_reads": (resolved_region_reads_path, "Full transcription reads for each coarse region"),
        "section_resolution": (resolved_section_resolution_path, "Canonical text ownership per coarse region"),
        "box_cleanup": (resolved_box_cleanup_path, "Targeted cleanup for overlapping region pairs"),
        "page_assembly": (resolved_page_assembly_path, "Deterministic assembly from region reads"),
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
        if Path(files[key]["path"]).exists() and normalize_packet_status(files[key].get("status")) == "empty":
            files[key]["status"] = "draft"
            files[key]["note"] = files[key].get("note") or default_note

    workflow = payload.get("workflow") or {}
    if not isinstance(workflow, dict):
        workflow = {}
        payload["workflow"] = workflow
    next_action = str(workflow.get("next_action") or "").strip()
    next_action = _NEXT_ACTION_ALIASES.get(next_action, next_action)
    if next_action not in PACKET_NEXT_ACTIONS:
        next_action = infer_packet_next_action(payload)
    workflow["next_action"] = next_action

    packet = PagePacket.model_validate(payload)
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def record_packet_render_outputs(
    packet_path: Path,
    *,
    edition_html_path: Path,
    folio_render_path: Path,
) -> PagePacket:
    packet = repair_packet_json(packet_path)
    packet.files["edition_html"] = PacketFileRef(
        kind="edition_html",
        path=str(edition_html_path.resolve()),
        status="draft",
        note="Rendered HTML folio edition",
    )
    packet.files["folio_render"] = PacketFileRef(
        kind="folio_render",
        path=str(folio_render_path.resolve()),
        status="draft",
        note="Structured folio.render JSON artifact",
    )
    packet_path = packet_path.resolve()
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet
