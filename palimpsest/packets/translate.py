from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.model_io import resolve_prompt_text, response_text
from palimpsest.models.packet import PagePacket


DEFAULT_PACKET_TRANSLATION_PROMPT_NAME = "packet_translation"
DEFAULT_PACKET_TRANSLATION_MAX_OUTPUT_TOKENS = 16384


@dataclass
class PacketTranslationArtifact:
    packet_path: Path
    witness_path: Path
    output_path: Path
    prompt_path: Path
    meta_path: Path
    model: str
    finish_reason: str | None
    char_count: int


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def run_packet_translation(
    packet_path: Path,
    *,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_READING,
) -> PacketTranslationArtifact:
    packet_path = packet_path.resolve()
    packet = PagePacket.model_validate_json(packet_path.read_text(encoding="utf-8"))

    witness_ref = packet.files.get("witness")
    translation_ref = packet.files.get("translation")
    if witness_ref is None or not witness_ref.path:
        raise FileNotFoundError(f"Packet {packet.page_id} is missing witness.md")
    if translation_ref is None or not translation_ref.path:
        raise FileNotFoundError(f"Packet {packet.page_id} is missing translation.md")

    witness_path = Path(witness_ref.path).resolve()
    output_path = Path(translation_ref.path).resolve()
    if not witness_path.exists():
        raise FileNotFoundError(f"Missing witness.md: {witness_path}")

    prompt_text, prompt_path = resolve_prompt_text(prompt_file, DEFAULT_PACKET_TRANSLATION_PROMPT_NAME)
    prompt_copy_path = output_path.parent / "translation_prompt.txt"
    prompt_copy_path.write_text(prompt_text, encoding="utf-8")

    witness_text = witness_path.read_text(encoding="utf-8").strip()
    bundled_input = "\n\n".join(
        [
            f"Packet page id: {packet.page_id}",
            f"Page unit: {packet.page_unit}",
            "Witness markdown:",
            witness_text,
        ]
    )
    bundled_input_path = output_path.parent / "translation_input.md"
    bundled_input_path.write_text(bundled_input, encoding="utf-8")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            bundled_input,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=DEFAULT_PACKET_TRANSLATION_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = response_text(response, empty_error="Model returned no translation text")
    if len(text) > max(len(witness_text) * 8, 24000):
        raise ValueError("Translation output is disproportionately large relative to witness input")

    output_path.write_text(text, encoding="utf-8")
    translation_ref.status = "draft"
    packet.workflow.next_action = "draft_interpretation"
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    meta_path = output_path.parent / "translation_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "packet_path": str(packet_path),
        "witness_path": str(witness_path),
        "output_path": str(output_path),
        "prompt_path": str(prompt_path),
        "prompt_copy_path": str(prompt_copy_path),
        "input_path": str(bundled_input_path),
        "model": model,
        "finish_reason": finish_reason,
        "char_count": len(text),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return PacketTranslationArtifact(
        packet_path=packet_path,
        witness_path=witness_path,
        output_path=output_path,
        prompt_path=prompt_path,
        meta_path=meta_path,
        model=model,
        finish_reason=finish_reason,
        char_count=len(text),
    )
