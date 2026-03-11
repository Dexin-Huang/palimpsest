from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.models import PageHandoff, PagePacket, WindowSynthesis
from palimpsest.reconstruct.reading import _resolve_prompt_text, _response_text
from palimpsest.packets.scholar import repair_packet_json


DEFAULT_HANDOFF_PROMPT_NAME = "page_handoff_focused"
DEFAULT_WINDOW_PROMPT_NAME = "window_synthesis_focused"
DEFAULT_CONTINUITY_MAX_OUTPUT_TOKENS = 16384


@dataclass
class PageHandoffArtifact:
    packet_path: Path
    json_path: Path
    markdown_path: Path
    meta_path: Path
    prompt_path: Path
    model: str


@dataclass
class WindowSynthesisArtifact:
    packet_paths: list[Path]
    json_path: Path
    markdown_path: Path
    meta_path: Path
    prompt_path: Path
    model: str


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _packet_file_text(packet: PagePacket, key: str) -> str:
    ref = packet.files.get(key)
    if ref is None:
        return ""
    path = Path(ref.path)
    if not path.exists():
        return ""
    return _read_text(path)


def _render_handoff_markdown(handoff: PageHandoff) -> str:
    lines = [
        f"# Page Handoff: {handoff.page_id}",
        "",
        f"- `doc_id`: {handoff.doc_id}",
        f"- `page_id`: {handoff.page_id}",
        f"- `next_page_id`: {handoff.next_page_id or ''}",
        f"- `continues_text`: {handoff.continues_text if handoff.continues_text is not None else 'unknown'}",
        "",
        "## Summary",
        "",
    ]
    for item in handoff.summary:
        lines.append(f"- {item}")

    lines.extend(["", "## Active Entities", ""])
    for item in handoff.active_entities:
        status = f" ({item.status})" if item.status else ""
        note = f": {item.note}" if item.note else ""
        lines.append(f"- `{item.key}` {item.label}{status}{note}")

    lines.extend(["", "## Active Terms", ""])
    for item in handoff.active_terms:
        status = f" ({item.status})" if item.status else ""
        note = f": {item.note}" if item.note else ""
        lines.append(f"- `{item.key}` {item.label}{status}{note}")

    lines.extend(["", "## Verify Next", ""])
    for item in handoff.verify_next:
        lines.append(f"- {item}")

    lines.extend(["", "## Local Questions", ""])
    for item in handoff.local_questions:
        lines.append(f"- {item}")

    lines.extend(["", "## Proposed Links", ""])
    for link in handoff.proposed_links:
        note = f": {link.note}" if link.note else ""
        lines.append(f"- `{link.link_type}` -> `{link.target_page_id}` ({link.status}){note}")

    lines.append("")
    return "\n".join(lines)


def _render_window_markdown(window: WindowSynthesis) -> str:
    lines = [
        f"# Window Synthesis: {window.center_page_id}",
        "",
        f"- `doc_id`: {window.doc_id}",
        f"- `page_ids`: {', '.join(window.page_ids)}",
        f"- `center_page_id`: {window.center_page_id}",
        "",
        "## Summary",
        "",
    ]
    for item in window.summary:
        lines.append(f"- {item}")

    lines.extend(["", "## Contiguous Threads", ""])
    for item in window.contiguous_threads:
        lines.append(f"- {item}")

    lines.extend(["", "## Stable Entities", ""])
    for item in window.stable_entities:
        status = f" ({item.status})" if item.status else ""
        note = f": {item.note}" if item.note else ""
        lines.append(f"- `{item.key}` {item.label}{status}{note}")

    lines.extend(["", "## Stable Terms", ""])
    for item in window.stable_terms:
        status = f" ({item.status})" if item.status else ""
        note = f": {item.note}" if item.note else ""
        lines.append(f"- `{item.key}` {item.label}{status}{note}")

    lines.extend(["", "## Revise Or Confirm", ""])
    for item in window.revise_or_confirm:
        lines.append(f"- {item}")

    lines.extend(["", "## Open Loops", ""])
    for item in window.open_loops:
        lines.append(f"- {item}")

    lines.append("")
    return "\n".join(lines)


def _default_handoff_dir(packet_path: Path) -> Path:
    return packet_path.parent


def _default_window_dir(packet_paths: list[Path]) -> Path:
    first = packet_paths[0].resolve()
    page_ids = []
    for packet_path in packet_paths:
        packet = PagePacket.model_validate(json.loads(packet_path.read_text(encoding="utf-8")))
        page_ids.append(packet.page_id)
    suffix = "_".join(page_ids) + "_window"
    return first.parent.parent / suffix


def _bundle_handoff_inputs(packet: PagePacket, packet_path: Path, previous_handoff_path: Path | None, next_page_id: str | None) -> str:
    parts = [
        "## Packet Metadata",
        json.dumps(
            {
                "doc_id": packet.doc_id,
                "page_id": packet.page_id,
                "page_unit": packet.page_unit,
                "next_page_id": next_page_id,
            },
            indent=2,
            ensure_ascii=False,
        ),
        "",
        "## Notes",
        _packet_file_text(packet, "notes"),
        "",
        "## Translation",
        _packet_file_text(packet, "translation"),
        "",
        "## Interpretation",
        _packet_file_text(packet, "interpretation"),
        "",
        "## Terms",
        _packet_file_text(packet, "terms"),
        "",
        "## Questions",
        _packet_file_text(packet, "questions"),
    ]

    if previous_handoff_path is not None and previous_handoff_path.exists():
        parts.extend(
            [
                "",
                "## Previous Handoff",
                _read_text(previous_handoff_path),
            ]
        )

    return "\n".join(parts).strip()


def _bundle_window_inputs(packet_paths: list[Path]) -> str:
    chunks: list[str] = []
    for index, packet_path in enumerate(packet_paths, start=1):
        packet = repair_packet_json(packet_path)
        handoff_path = packet_path.parent / "page_handoff.md"
        chunks.extend(
            [
                f"## Packet {index}: {packet.page_id}",
                json.dumps(
                    {
                        "doc_id": packet.doc_id,
                        "page_id": packet.page_id,
                        "page_unit": packet.page_unit,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                "",
                "### Translation",
                _packet_file_text(packet, "translation"),
                "",
                "### Interpretation",
                _packet_file_text(packet, "interpretation"),
                "",
                "### Terms",
                _packet_file_text(packet, "terms"),
                "",
                "### Questions",
                _packet_file_text(packet, "questions"),
            ]
        )
        if handoff_path.exists():
            chunks.extend(
                [
                    "",
                    "### Handoff",
                    _read_text(handoff_path),
                ]
            )
        chunks.append("")
    return "\n".join(chunks).strip()


def run_page_handoff(
    packet_path: Path,
    *,
    out_dir: Path | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_READING,
    next_page_id: str | None = None,
    previous_handoff_path: Path | None = None,
) -> PageHandoffArtifact:
    packet_path = packet_path.resolve()
    packet = repair_packet_json(packet_path)
    target_dir = (out_dir.resolve() if out_dir else _default_handoff_dir(packet_path).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, DEFAULT_HANDOFF_PROMPT_NAME)
    bundled_inputs = _bundle_handoff_inputs(packet, packet_path, previous_handoff_path.resolve() if previous_handoff_path else None, next_page_id)
    prompt_copy_path = target_dir / "page_handoff_prompt.txt"
    prompt_copy_path.write_text(prompt_text, encoding="utf-8")
    inputs_path = target_dir / "page_handoff_inputs.md"
    inputs_path.write_text(bundled_inputs, encoding="utf-8")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[prompt_text, bundled_inputs],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_CONTINUITY_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = _response_text(response)
    payload = json.loads(text)
    payload["artifact_type"] = "page.handoff"
    payload["created_at"] = _utc_now()
    payload["doc_id"] = packet.doc_id
    payload["page_id"] = packet.page_id
    payload["next_page_id"] = next_page_id
    payload["source_packet_path"] = str(packet_path)
    handoff = PageHandoff.model_validate(payload)

    json_path = target_dir / "page_handoff.json"
    markdown_path = target_dir / "page_handoff.md"
    meta_path = target_dir / "page_handoff_meta.json"
    json_path.write_text(handoff.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(_render_handoff_markdown(handoff), encoding="utf-8")
    meta = {
        "generated_at": _utc_now(),
        "packet_path": str(packet_path),
        "previous_handoff_path": str(previous_handoff_path.resolve()) if previous_handoff_path else None,
        "prompt_path": str(prompt_path),
        "prompt_copy_path": str(prompt_copy_path),
        "inputs_path": str(inputs_path),
        "model": model,
        "finish_reason": finish_reason,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return PageHandoffArtifact(
        packet_path=packet_path,
        json_path=json_path,
        markdown_path=markdown_path,
        meta_path=meta_path,
        prompt_path=prompt_path,
        model=model,
    )


def run_window_synthesis(
    packet_paths: list[Path],
    *,
    out_dir: Path | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_READING,
    center_page_id: str | None = None,
) -> WindowSynthesisArtifact:
    resolved_packets = [path.resolve() for path in packet_paths]
    if len(resolved_packets) < 2:
        raise ValueError("At least two packet paths are required")

    packets = [repair_packet_json(path) for path in resolved_packets]
    doc_ids = {packet.doc_id for packet in packets}
    if len(doc_ids) != 1:
        raise ValueError("All packet paths must belong to the same document")

    inferred_center = center_page_id or packets[len(packets) // 2].page_id
    target_dir = (out_dir.resolve() if out_dir else _default_window_dir(resolved_packets).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, DEFAULT_WINDOW_PROMPT_NAME)
    bundled_inputs = _bundle_window_inputs(resolved_packets)
    prompt_copy_path = target_dir / "window_synthesis_prompt.txt"
    prompt_copy_path.write_text(prompt_text, encoding="utf-8")
    inputs_path = target_dir / "window_synthesis_inputs.md"
    inputs_path.write_text(bundled_inputs, encoding="utf-8")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[prompt_text, bundled_inputs],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_CONTINUITY_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = _response_text(response)
    payload = json.loads(text)
    payload["artifact_type"] = "window.synthesis"
    payload["created_at"] = _utc_now()
    payload["doc_id"] = packets[0].doc_id
    payload["page_ids"] = [packet.page_id for packet in packets]
    payload["center_page_id"] = inferred_center
    window = WindowSynthesis.model_validate(payload)

    json_path = target_dir / "window_synthesis.json"
    markdown_path = target_dir / "window_synthesis.md"
    meta_path = target_dir / "window_synthesis_meta.json"
    json_path.write_text(window.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(_render_window_markdown(window), encoding="utf-8")
    meta = {
        "generated_at": _utc_now(),
        "packet_paths": [str(path) for path in resolved_packets],
        "prompt_path": str(prompt_path),
        "prompt_copy_path": str(prompt_copy_path),
        "inputs_path": str(inputs_path),
        "model": model,
        "center_page_id": inferred_center,
        "finish_reason": finish_reason,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return WindowSynthesisArtifact(
        packet_paths=resolved_packets,
        json_path=json_path,
        markdown_path=markdown_path,
        meta_path=meta_path,
        prompt_path=prompt_path,
        model=model,
    )
