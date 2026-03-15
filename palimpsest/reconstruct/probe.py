from __future__ import annotations

import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING, DEFAULT_MODEL_VISION
from palimpsest.contracts import (
    layout_crops_dir,
    layout_overlay_path,
    layout_probe_json_path,
    layout_probe_meta_path,
    layout_probe_raw_response_path,
    layout_prompt_copy_path,
    region_reads_path,
)
from palimpsest.model_io import resolve_prompt_text as _resolve_prompt_text
from palimpsest.model_io import response_text as _response_text
from palimpsest.models.layout_probe import LayoutProbe, RegionRead
from palimpsest.reconstruct.artifacts import PageLayoutProbeArtifact
from palimpsest.reconstruct.common import DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS, _coerce_json_text, _utc_now
from palimpsest.reconstruct.probe_layout import (
    _coarsen_layout,
    _default_output_dir,
    _draw_overlay,
    _image_page_unit,
    _resolve_doc_id,
    _save_crops,
)
from palimpsest.reconstruct.region_reads import DEFAULT_REGION_PROMPT_NAME, _run_region_read


def run_page_layout_probe(
    image_path: Path,
    *,
    out_dir: Path | None = None,
    prompt_file: Path | None = None,
    model: str = DEFAULT_MODEL_VISION,
    orient_model: str = DEFAULT_MODEL_READING,
    orient_regions: bool = True,
) -> PageLayoutProbeArtifact:
    image_path = image_path.resolve()
    target_dir = (out_dir.resolve() if out_dir else _default_output_dir(image_path).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_layout_probe")
    prompt_text = prompt_text.replace("{PAGE_ID}", image_path.stem)
    prompt_copy_artifact_path = layout_prompt_copy_path(target_dir)
    prompt_copy_artifact_path.write_text(prompt_text, encoding="utf-8")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = _response_text(response)
    payload = json.loads(_coerce_json_text(text))
    payload.update(
        {
            "created_at": _utc_now(),
            "doc_id": _resolve_doc_id(image_path),
            "page_id": image_path.stem,
            "image_path": str(image_path),
            "page_unit": _image_page_unit(image_path),
        }
    )
    layout = _coarsen_layout(LayoutProbe.model_validate(payload))

    layout_json_path = layout_probe_json_path(target_dir)
    raw_response_path = layout_probe_raw_response_path(target_dir)
    layout_json_path.write_text(layout.model_dump_json(indent=2), encoding="utf-8")
    raw_response_path.write_text(_coerce_json_text(text), encoding="utf-8")

    overlay_path = layout_overlay_path(target_dir)
    _draw_overlay(image_path, layout, overlay_path)

    crops_dir = layout_crops_dir(target_dir)
    crop_rows = _save_crops(image_path, layout, crops_dir)

    region_reads: list[RegionRead] = []
    if orient_regions:
        region_prompt_path = (Path(__file__).resolve().parents[1] / "prompts" / f"{DEFAULT_REGION_PROMPT_NAME}.txt").resolve()
        for row, region in zip(crop_rows, layout.regions):
            if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
                continue
            if not region.contains_text and region.role not in {"header", "page_number", "marginalia"}:
                continue
            try:
                region_reads.append(
                    _run_region_read(
                        client,
                        page_id=layout.page_id,
                        region_id=region.region_id,
                        label=region.label,
                        role=region.role,
                        bbox_norm=region.bbox_norm,
                        crop_path=Path(row["crop_path"]),
                        prompt_path=region_prompt_path,
                        model=orient_model,
                    )
                )
            except Exception:
                continue

    region_reads_output_path = region_reads_path(target_dir)
    region_reads_output_path.write_text(
        json.dumps([item.model_dump() for item in region_reads], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    meta_path = layout_probe_meta_path(target_dir)
    meta = {
        "generated_at": _utc_now(),
        "image_path": str(image_path),
        "prompt_path": str(prompt_path),
        "prompt_copy_path": str(prompt_copy_artifact_path),
        "layout_json_path": str(layout_json_path),
        "raw_response_path": str(raw_response_path),
        "overlay_path": str(overlay_path),
        "crops_dir": str(crops_dir),
        "region_reads_path": str(region_reads_output_path),
        "model": model,
        "region_read_model": orient_model,
        "finish_reason": finish_reason,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return PageLayoutProbeArtifact(
        image_path=image_path,
        output_dir=target_dir,
        prompt_path=prompt_path,
        layout_json_path=layout_json_path,
        overlay_path=overlay_path,
        crops_dir=crops_dir,
        region_reads_path=region_reads_output_path,
        meta_path=meta_path,
        model=model,
        region_read_model=orient_model,
        finish_reason=finish_reason,
    )
