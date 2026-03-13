from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.contracts import (
    prompt_copy_path,
    reading_meta_path,
    reading_output_dir,
    reading_output_path,
    section_synthesis_inputs_path,
    section_synthesis_meta_path,
    section_synthesis_output_dir,
    section_synthesis_output_path,
)
from palimpsest.model_io import resolve_prompt_text, response_text
from palimpsest.reconstruct.prepare import PreparedPageArtifact, prepare_image


DEFAULT_READING_PROMPT_NAME = "page_witness_focused"
DEFAULT_SYNTHESIS_PROMPT_NAME = "section_synthesis_focused"
DEFAULT_READING_MAX_OUTPUT_TOKENS = 65536


@dataclass
class PageReadingArtifact:
    image_path: Path
    prepared_image_path: Path
    prepare_meta_path: Path | None
    output_path: Path
    prompt_path: Path
    meta_path: Path
    model: str
    finish_reason: str | None
    char_count: int


@dataclass
class SectionSynthesisArtifact:
    input_paths: list[Path]
    output_path: Path
    prompt_path: Path
    meta_path: Path
    model: str
    finish_reason: str | None
    char_count: int


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_output_dir(image_path: Path) -> Path:
    return reading_output_dir(image_path)


def _default_synthesis_output_dir(input_paths: list[Path]) -> Path:
    return section_synthesis_output_dir(input_paths)


def run_page_reading(
    image_path: Path,
    *,
    out_dir: Path | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_READING,
    prepare: bool = True,
) -> PageReadingArtifact:
    image_path = image_path.resolve()
    target_dir = (out_dir.resolve() if out_dir else _default_output_dir(image_path).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    prompt_text, prompt_path = resolve_prompt_text(prompt_file, DEFAULT_READING_PROMPT_NAME)
    copied_prompt_path = prompt_copy_path(target_dir)
    copied_prompt_path.write_text(prompt_text, encoding="utf-8")

    prepared: PreparedPageArtifact | None = None
    model_image_path = image_path
    if prepare:
        prepared = prepare_image(image_path, out_dir=target_dir / "prepared")
        model_image_path = prepared.prepared_image_path

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_bytes(data=model_image_path.read_bytes(), mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=DEFAULT_READING_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = response_text(response)

    resolved_output_path = reading_output_path(target_dir, image_path)
    resolved_output_path.write_text(text, encoding="utf-8")

    meta_path = reading_meta_path(target_dir)
    meta = {
        "generated_at": _utc_now(),
        "image_path": str(image_path),
        "prepared_image_path": str(model_image_path),
        "prepare_meta_path": str(prepared.meta_path) if prepared is not None else None,
        "output_path": str(resolved_output_path),
        "prompt_path": str(prompt_path),
        "model": model,
        "finish_reason": finish_reason,
        "char_count": len(text),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return PageReadingArtifact(
        image_path=image_path,
        prepared_image_path=model_image_path,
        prepare_meta_path=prepared.meta_path if prepared is not None else None,
        output_path=resolved_output_path,
        prompt_path=prompt_path,
        meta_path=meta_path,
        model=model,
        finish_reason=finish_reason,
        char_count=len(text),
    )


def _bundle_section_inputs(input_paths: list[Path]) -> str:
    chunks: list[str] = []
    for index, path in enumerate(input_paths, start=1):
        text = path.read_text(encoding="utf-8").strip()
        chunks.append(f"### Input {index}: {path.stem}\n\n{text}")
    return "\n\n".join(chunks)


def run_section_synthesis(
    input_paths: list[Path],
    *,
    out_dir: Path | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_READING,
) -> SectionSynthesisArtifact:
    resolved_inputs = [path.resolve() for path in input_paths]
    if not resolved_inputs:
        raise ValueError("At least one input path is required")

    target_dir = (out_dir.resolve() if out_dir else _default_synthesis_output_dir(resolved_inputs).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    prompt_text, prompt_path = resolve_prompt_text(prompt_file, DEFAULT_SYNTHESIS_PROMPT_NAME)
    copied_prompt_path = prompt_copy_path(target_dir)
    copied_prompt_path.write_text(prompt_text, encoding="utf-8")

    bundled_inputs = _bundle_section_inputs(resolved_inputs)
    bundled_inputs_path = section_synthesis_inputs_path(target_dir)
    bundled_inputs_path.write_text(bundled_inputs, encoding="utf-8")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            bundled_inputs,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=DEFAULT_READING_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = response_text(response)

    output_path = section_synthesis_output_path(target_dir)
    output_path.write_text(text, encoding="utf-8")

    meta_path = section_synthesis_meta_path(target_dir)
    meta = {
        "generated_at": _utc_now(),
        "input_paths": [str(path) for path in resolved_inputs],
        "inputs_bundle_path": str(bundled_inputs_path),
        "output_path": str(output_path),
        "prompt_path": str(prompt_path),
        "model": model,
        "finish_reason": finish_reason,
        "char_count": len(text),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return SectionSynthesisArtifact(
        input_paths=resolved_inputs,
        output_path=output_path,
        prompt_path=prompt_path,
        meta_path=meta_path,
        model=model,
        finish_reason=finish_reason,
        char_count=len(text),
    )
