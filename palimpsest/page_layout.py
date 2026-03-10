from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from palimpsest.config import DEFAULT_MODEL_VISION, DEFAULT_MODEL_READING
from palimpsest.models import LayoutProbe, PageAssembly, PageAssemblyUnit, RegionOrientation


DEFAULT_LAYOUT_PROMPT_NAME = "page_layout_probe"
DEFAULT_REGION_PROMPT_NAME = "page_region_orientation"
DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS = 32768


@dataclass
class PageLayoutProbeArtifact:
    image_path: Path
    output_dir: Path
    prompt_path: Path
    layout_json_path: Path
    overlay_path: Path
    crops_dir: Path
    orientations_path: Path
    meta_path: Path
    model: str
    orientation_model: str
    finish_reason: str | None


@dataclass
class RegionReadsArtifact:
    probe_dir: Path
    reads_path: Path
    meta_path: Path
    model: str


@dataclass
class PageAssemblyArtifact:
    probe_dir: Path
    assembly_json_path: Path
    assembly_md_path: Path
    meta_path: Path


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_output_dir(image_path: Path) -> Path:
    if image_path.parent.name in {"images", "images_cleaned"}:
        return image_path.parent.parent / "experiments" / f"{image_path.stem}_layout_probe"
    return image_path.parent / f"{image_path.stem}_layout_probe"


def _resolve_doc_id(image_path: Path) -> str:
    image_path = image_path.resolve()
    if image_path.parent.name in {"images", "images_cleaned"}:
        return image_path.parent.parent.name
    return image_path.parent.name


def _resolve_prompt_text(prompt_file: Path | None, prompt_name: str) -> tuple[str, Path]:
    if prompt_file is None:
        prompt_path = (Path(__file__).resolve().parents[1] / "palimpsest" / "prompts" / f"{prompt_name}.txt").resolve()
        return prompt_path.read_text(encoding="utf-8"), prompt_path
    prompt_path = prompt_file.resolve()
    return prompt_path.read_text(encoding="utf-8"), prompt_path


def _response_text(response) -> tuple[str, str | None]:
    candidates = getattr(response, "candidates", None) or []
    finish_reason = None
    text_parts: list[str] = []
    for index, candidate in enumerate(candidates):
        if index == 0:
            raw_reason = getattr(candidate, "finish_reason", None)
            finish_reason = str(raw_reason) if raw_reason is not None else None
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            value = getattr(part, "text", None)
            if isinstance(value, str) and value:
                text_parts.append(value)
    text = "\n".join(text_parts).strip()
    if not text:
        raise ValueError("Model returned no text")
    return text, finish_reason


def _coerce_json_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _image_page_unit(image_path: Path) -> str:
    with Image.open(image_path) as image:
        width, height = image.size
    return "spread" if width > (height * 1.1) else "page"


def _bbox_px(width: int, height: int, bbox_norm: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x, y, w, h = bbox_norm
    left = max(0, min(width, round(x * width)))
    top = max(0, min(height, round(y * height)))
    right = max(left + 1, min(width, round((x + w) * width)))
    bottom = max(top + 1, min(height, round((y + h) * height)))
    return left, top, right, bottom


def _draw_overlay(image_path: Path, layout: LayoutProbe, out_path: Path) -> None:
    color_map = {
        "main_text": "#ff4d4f",
        "header": "#faad14",
        "marginalia": "#52c41a",
        "page_number": "#13c2c2",
        "footer": "#722ed1",
        "stamp": "#eb2f96",
        "gutter": "#8c8c8c",
        "damage": "#fa8c16",
        "other": "#1677ff",
    }

    with Image.open(image_path).convert("RGB") as image:
        width, height = image.size
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        if layout.writing_area_bbox_norm:
            left, top, right, bottom = _bbox_px(width, height, layout.writing_area_bbox_norm)
            draw.rectangle((left, top, right, bottom), outline="#00bcd4", width=4)

        for region in layout.regions:
            left, top, right, bottom = _bbox_px(width, height, region.bbox_norm)
            color = color_map.get(region.role, color_map["other"])
            draw.rectangle((left, top, right, bottom), outline=color, width=4)
            priority = f" [{region.reconstruction_priority}]" if region.reconstruction_priority else ""
            label = f"{region.region_id} {region.label}{priority}"
            text_box = draw.textbbox((left, top), label, font=font)
            draw.rectangle(text_box, fill=(255, 255, 255))
            draw.text((left, top), label, fill=color, font=font)

        image.save(out_path, format="PNG")


def _save_crops(image_path: Path, layout: LayoutProbe, crops_dir: Path) -> list[dict]:
    crops_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    with Image.open(image_path).convert("RGB") as image:
        width, height = image.size
        for region in layout.regions:
            left, top, right, bottom = _bbox_px(width, height, region.bbox_norm)
            crop_path = crops_dir / f"{region.region_id}.jpg"
            image.crop((left, top, right, bottom)).save(crop_path, format="JPEG", quality=95)
            saved.append({
                "region_id": region.region_id,
                "label": region.label,
                "role": region.role,
                "page_side": region.page_side,
                "column_index": region.column_index,
                "reconstruction_priority": region.reconstruction_priority,
                "ignore_for_reconstruction": region.ignore_for_reconstruction,
                "bbox_px": [left, top, right, bottom],
                "crop_path": str(crop_path),
            })
    return saved


def _run_region_orientation(
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
) -> RegionOrientation:
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
            types.Part.from_bytes(data=crop_path.read_bytes(), mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
        ),
    )
    text, _ = _response_text(response)
    payload = json.loads(_coerce_json_text(text))
    payload.update({
        "page_id": page_id,
        "region_id": region_id,
        "label": label,
        "role": role,
    })
    return RegionOrientation.model_validate(payload)


def _load_layout_probe(probe_dir: Path) -> LayoutProbe:
    return LayoutProbe.model_validate_json((probe_dir / "layout_probe.json").read_text(encoding="utf-8"))


def _load_region_orientations(probe_dir: Path) -> list[RegionOrientation]:
    path = probe_dir / "region_orientations.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RegionOrientation.model_validate(item) for item in payload]


def _write_region_orientations(probe_dir: Path, orientations: list[RegionOrientation]) -> Path:
    reads_path = probe_dir / "region_orientations.json"
    reads_path.write_text(
        json.dumps([item.model_dump() for item in orientations], indent=2, ensure_ascii=False),
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
    crops_dir = probe_dir / "crops"
    prompt_path = (Path(__file__).resolve().parents[1] / "palimpsest" / "prompts" / f"{DEFAULT_REGION_PROMPT_NAME}.txt").resolve()
    client = genai.Client()

    selected = {item for item in (region_ids or [])}
    existing = {item.region_id: item for item in _load_region_orientations(probe_dir)}
    reads: list[RegionOrientation] = []
    for region in layout.regions:
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        if selected and region.region_id not in selected:
            if region.region_id in existing:
                reads.append(existing[region.region_id])
            continue
        if not region.contains_text and region.role not in {"header", "page_number", "marginalia"}:
            continue
        crop_path = crops_dir / f"{region.region_id}.jpg"
        if not crop_path.exists():
            continue
        read = _run_region_orientation(
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

    ordered_region_ids = [region.region_id for region in layout.regions if region.region_id in existing]
    for region_id in ordered_region_ids:
        reads.append(existing[region_id])

    reads_path = _write_region_orientations(probe_dir, reads)
    meta_path = probe_dir / "region_reads_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "reads_path": str(reads_path),
        "model": model,
        "region_ids": region_ids or [],
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return RegionReadsArtifact(probe_dir=probe_dir, reads_path=reads_path, meta_path=meta_path, model=model)


def run_page_assembly(probe_dir: Path) -> PageAssemblyArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_orientations(probe_dir)}

    units: list[PageAssemblyUnit] = []
    counter = 1
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        units.append(
            PageAssemblyUnit(
                unit_id=f"u{counter:03d}",
                region_id=region.region_id,
                label=region.label,
                role=region.role,
                bbox_norm=region.bbox_norm,
                page_side=region.page_side,
                column_index=region.column_index,
                reading_order=region.reading_order,
                reading_direction=read.reading_direction if read else region.orientation_hint,
                line_flow=read.line_flow if read else None,
                start_edge=read.start_edge if read else None,
                summary=read.summary if read else region.notes,
                diplomatic_lines=list(read.diplomatic_lines) if read else [],
                notes=list(read.notes) if read else ([region.notes] if region.notes else []),
            )
        )
        counter += 1

    assembly = PageAssembly(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        image_path=layout.image_path,
        page_unit=layout.page_unit,
        units=units,
    )

    assembly_json_path = probe_dir / "page_assembly.json"
    assembly_md_path = probe_dir / "page_assembly.md"
    assembly_json_path.write_text(assembly.model_dump_json(indent=2), encoding="utf-8")

    markdown_lines = [f"# Page Assembly: {assembly.page_id}", ""]
    for unit in assembly.units:
        markdown_lines.extend(
            [
                f"## {unit.label}",
                f"- Region: `{unit.region_id}`",
                f"- Role: `{unit.role}`",
                f"- BBox: `{list(unit.bbox_norm)}`",
            ]
        )
        if unit.reading_direction:
            markdown_lines.append(f"- Reading direction: `{unit.reading_direction}`")
        if unit.line_flow:
            markdown_lines.append(f"- Line flow: `{unit.line_flow}`")
        markdown_lines.append("")
        for line in unit.diplomatic_lines:
            markdown_lines.append(line)
        if unit.notes:
            markdown_lines.append("")
            markdown_lines.append("Notes:")
            for note in unit.notes:
                markdown_lines.append(f"- {note}")
        markdown_lines.append("")
    assembly_md_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    meta_path = probe_dir / "page_assembly_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "assembly_json_path": str(assembly_json_path),
        "assembly_md_path": str(assembly_md_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return PageAssemblyArtifact(
        probe_dir=probe_dir,
        assembly_json_path=assembly_json_path,
        assembly_md_path=assembly_md_path,
        meta_path=meta_path,
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

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, DEFAULT_LAYOUT_PROMPT_NAME)
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
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        ),
    )
    text, finish_reason = _response_text(response)
    payload = json.loads(_coerce_json_text(text))
    payload.update({
        "created_at": _utc_now(),
        "doc_id": _resolve_doc_id(image_path),
        "page_id": image_path.stem,
        "image_path": str(image_path),
        "page_unit": _image_page_unit(image_path),
    })
    layout = LayoutProbe.model_validate(payload)

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
        region_prompt_path = (Path(__file__).resolve().parents[1] / "palimpsest" / "prompts" / f"{DEFAULT_REGION_PROMPT_NAME}.txt").resolve()
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

    orientations_path = target_dir / "region_orientations.json"
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
