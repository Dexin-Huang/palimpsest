from __future__ import annotations

import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING, DEFAULT_MODEL_VISION
from palimpsest.reconstruct.artifacts import PageLayoutProbeArtifact
from palimpsest.models import LayoutProbe, RegionOrientation

from .pipeline import (
    DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
    DEFAULT_REGION_PROMPT_NAME,
    _coarsen_layout,
    _coerce_json_text,
    _default_output_dir,
    _draw_overlay,
    _image_page_unit,
    _resolve_doc_id,
    _resolve_prompt_text,
    _response_text,
    _run_region_orientation,
    _save_crops,
    _utc_now,
)


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
    prompt_copy_path = target_dir / "layout_prompt.txt"
    prompt_copy_path.write_text(prompt_text, encoding="utf-8")

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

    layout_json_path = target_dir / "layout_probe.json"
    raw_response_path = target_dir / "layout_probe_raw.json"
    layout_json_path.write_text(layout.model_dump_json(indent=2), encoding="utf-8")
    raw_response_path.write_text(_coerce_json_text(text), encoding="utf-8")

    overlay_path = target_dir / "layout_overlay.png"
    _draw_overlay(image_path, layout, overlay_path)

    crops_dir = target_dir / "crops"
    crop_rows = _save_crops(image_path, layout, crops_dir)

    orientations: list[RegionOrientation] = []
    if orient_regions:
        region_prompt_path = (Path(__file__).resolve().parents[1] / "prompts" / f"{DEFAULT_REGION_PROMPT_NAME}.txt").resolve()
        for row, region in zip(crop_rows, layout.regions):
            if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
                continue
            if not region.contains_text and region.role not in {"header", "page_number", "marginalia"}:
                continue
            try:
                orientations.append(
                    _run_region_orientation(
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

    orientations_path = target_dir / "region_reads.json"
    orientations_path.write_text(
        json.dumps([item.model_dump() for item in orientations], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    meta_path = target_dir / "layout_probe_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "image_path": str(image_path),
        "prompt_path": str(prompt_path),
        "prompt_copy_path": str(prompt_copy_path),
        "layout_json_path": str(layout_json_path),
        "raw_response_path": str(raw_response_path),
        "overlay_path": str(overlay_path),
        "crops_dir": str(crops_dir),
        "orientations_path": str(orientations_path),
        "model": model,
        "orientation_model": orient_model,
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
        orientations_path=orientations_path,
        meta_path=meta_path,
        model=model,
        orientation_model=orient_model,
        finish_reason=finish_reason,
    )
