from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from palimpsest.models.packet import ALLOWED_PACKET_STATUSES, PacketFileRef, PagePacket

from .contract import packet_artifact_contracts


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


def _normalize_packet_payload(packet_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = payload.get("files") or {}
    if isinstance(files, dict):
        for ref in files.values():
            if isinstance(ref, dict):
                ref["status"] = normalize_packet_status(ref.get("status"))
    else:
        files = {}
        payload["files"] = files

    for key, contract in packet_artifact_contracts(packet_path.parent).items():
        default_path = str(contract.path)
        default_note = contract.note
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

    return payload


def load_packet_json(packet_path: Path) -> PagePacket:
    """Load packet state with normalized defaults but no write-on-read side effects."""
    packet_path = packet_path.resolve()
    payload: dict[str, Any] = json.loads(packet_path.read_text(encoding="utf-8"))
    return PagePacket.model_validate(_normalize_packet_payload(packet_path, payload))


def write_packet_json(packet_path: Path, packet: PagePacket) -> PagePacket:
    """Persist an already-validated packet model."""
    packet_path = packet_path.resolve()
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
    return packet


def reconcile_packet_json(packet_path: Path) -> PagePacket:
    """Persist normalized packet state back to disk when a caller explicitly wants migration/repair."""
    return write_packet_json(packet_path, load_packet_json(packet_path))


def record_packet_render_outputs(
    packet_path: Path,
    *,
    edition_html_path: Path,
    folio_render_path: Path,
) -> PagePacket:
    packet = load_packet_json(packet_path)
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
    return write_packet_json(packet_path, packet)
