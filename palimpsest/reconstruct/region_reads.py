from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.contracts import (
    layout_crops_dir,
    layout_probe_json_path,
    region_reads_meta_path,
    region_reads_path,
)
from palimpsest.model_io import response_text
from palimpsest.models.layout_probe import LayoutProbe, RegionRead
from palimpsest.reconstruct.artifacts import RegionReadsArtifact


DEFAULT_REGION_PROMPT_NAME = "page_region_read"
DEFAULT_REGION_MAX_OUTPUT_TOKENS = 32768
_TEXT_REGION_ROLES = {"header", "page_number", "marginalia"}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize_region_payload(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        return {"source_block": payload}
    if isinstance(payload, list):
        if len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        lines: list[str] = []
        for item in payload:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    lines.append(text)
                continue
            if isinstance(item, dict):
                block = str(item.get("source_block") or "").strip()
                if block:
                    lines.extend(line for line in block.splitlines() if line.strip())
                    continue
                source = str(item.get("source") or "").strip()
                if source:
                    lines.append(source)
        if lines:
            return {"source_block": "\n".join(lines)}
    raise ValueError(f"Unsupported region payload type: {type(payload).__name__}")


def _load_layout_probe(probe_dir: Path) -> LayoutProbe:
    return LayoutProbe.model_validate_json(layout_probe_json_path(probe_dir).read_text(encoding="utf-8"))


def _region_crop_path(probe_dir: Path, region_id: str) -> Path | None:
    crops_dir = layout_crops_dir(probe_dir)
    jpg_path = crops_dir / f"{region_id}.jpg"
    if jpg_path.exists():
        return jpg_path
    png_path = crops_dir / f"{region_id}.png"
    if png_path.exists():
        return png_path
    return None


def _run_region_read(
    client: genai.Client,
    *,
    page_id: str,
    region_id: str,
    label: str,
    role: str,
    bbox_norm: tuple[float, float, float, float],
    crop_path: Path,
    prompt_path: Path,
    model: str,
) -> RegionRead:
    template = prompt_path.read_text(encoding="utf-8")
    prompt_text = (
        template
        .replace("{PAGE_ID}", page_id)
        .replace("{REGION_ID}", region_id)
        .replace("{LABEL}", label)
        .replace("{ROLE}", role)
        .replace("{BBOX_NORM}", json.dumps(list(bbox_norm)))
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_bytes(
                data=crop_path.read_bytes(),
                mime_type="image/png" if crop_path.suffix.lower() == ".png" else "image/jpeg",
            ),
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=DEFAULT_REGION_MAX_OUTPUT_TOKENS,
        ),
    )
    text, _ = response_text(response)
    payload = _normalize_region_payload(text)
    payload.update(
        {
            "page_id": page_id,
            "region_id": region_id,
            "label": label,
            "role": role,
        }
    )
    return RegionRead.model_validate(payload)


def _load_region_reads(probe_dir: Path) -> list[RegionRead]:
    path = region_reads_path(probe_dir)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RegionRead.model_validate(item) for item in payload]


def _write_region_reads(probe_dir: Path, region_reads: list[RegionRead]) -> Path:
    reads_path = region_reads_path(probe_dir)
    reads_path.write_text(
        json.dumps([item.model_dump() for item in region_reads], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return reads_path


def run_region_reads(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    region_ids: list[str] | None = None,
) -> RegionReadsArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    crops_dir = layout_crops_dir(probe_dir)
    prompt_path = (Path(__file__).resolve().parents[1] / "prompts" / f"{DEFAULT_REGION_PROMPT_NAME}.txt").resolve()
    client = genai.Client()

    selected = {item for item in (region_ids or [])}
    existing = {item.region_id: item for item in _load_region_reads(probe_dir)}
    for region in layout.regions:
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        if selected and region.region_id not in selected:
            continue
        if not region.contains_text and region.role not in _TEXT_REGION_ROLES:
            continue
        crop_path = _region_crop_path(probe_dir, region.region_id)
        if crop_path is None:
            continue
        read = _run_region_read(
            client,
            page_id=layout.page_id,
            region_id=region.region_id,
            label=region.label,
            role=region.role,
            bbox_norm=region.bbox_norm,
            crop_path=crop_path,
            prompt_path=prompt_path,
            model=model,
        )
        existing[region.region_id] = read
        partial_order = [item.region_id for item in layout.regions if item.region_id in existing]
        partial_reads = [existing[item_id] for item_id in partial_order]
        _write_region_reads(probe_dir, partial_reads)

    ordered_region_ids = [region.region_id for region in layout.regions if region.region_id in existing]
    reads = [existing[region_id] for region_id in ordered_region_ids]

    reads_path = _write_region_reads(probe_dir, reads)
    meta_path = region_reads_meta_path(probe_dir)
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "reads_path": str(reads_path),
        "model": model,
        "region_ids": region_ids or [],
        "crops_dir": str(crops_dir),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return RegionReadsArtifact(probe_dir=probe_dir, reads_path=reads_path, meta_path=meta_path, model=model)
