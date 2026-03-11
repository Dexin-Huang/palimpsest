from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from palimpsest.config import DEFAULT_MODEL_VISION, DEFAULT_MODEL_READING
from palimpsest.reconstruct.artifacts import (
    BlobRefinementArtifact,
    PageAssemblyArtifact,
    PageLayoutProbeArtifact,
    RegionReadsArtifact,
)
from palimpsest.models import (
    LayoutProbe,
    PageAssembly,
    PageAssemblyUnit,
    PageBoxCleanup,
    PageSectionResolution,
    PageValidation,
    RegionOrientation,
)


DEFAULT_LAYOUT_PROMPT_NAME = "page_layout_probe"
DEFAULT_REGION_PROMPT_NAME = "page_region_read"
DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS = 32768


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


def _image_page_unit(image_path: Path) -> str:
    with Image.open(image_path) as image:
        width, height = image.size
    return "spread" if width > (height * 1.1) else "page"


def _clamp_bbox(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    min_x: float = 0.0,
    min_y: float = 0.0,
    max_x: float = 1.0,
    max_y: float = 1.0,
) -> tuple[float, float, float, float]:
    left = max(min_x, x)
    top = max(min_y, y)
    right = min(max_x, x + w)
    bottom = min(max_y, y + h)
    if right <= left:
        right = min(max_x, left + 0.001)
    if bottom <= top:
        bottom = min(max_y, top + 0.001)
    return (
        round(left, 4),
        round(top, 4),
        round(right - left, 4),
        round(bottom - top, 4),
    )


def _expanded_region_bbox(layout: LayoutProbe, region) -> tuple[float, float, float, float]:
    x, y, w, h = region.bbox_norm
    writing_area = layout.writing_area_bbox_norm or (0.0, 0.0, 1.0, 1.0)
    wx, wy, ww, wh = writing_area
    wr = wx + ww
    wb = wy + wh
    mid_x = wx + (ww / 2.0)

    # Do not expand every region by default.
    # Only role-specific rules below should enlarge boxes.
    pad_x = 0.0
    pad_y = 0.0
    extra_top = 0.0
    extra_bottom = 0.0

    if region.role == "main_text":
        pad_x = max(pad_x, max(0.018, w * 0.08))
        pad_y = max(pad_y, max(0.018, h * 0.04))
        extra_top = max(0.01, h * 0.015)
        extra_bottom = max(0.02, h * 0.03)
    elif region.role == "header":
        pad_x = max(pad_x, max(0.02, w * 0.25))
        pad_y = max(pad_y, max(0.012, h * 0.18))
        extra_top = max(0.005, h * 0.06)
        extra_bottom = max(0.012, h * 0.12)
    elif region.role == "marginalia":
        pad_x = max(pad_x, max(0.012, w * 0.08))
        pad_y = max(pad_y, max(0.012, h * 0.08))
        extra_bottom = max(0.01, h * 0.05)
    elif region.role == "page_number":
        pad_x = max(pad_x, max(0.006, w * 0.2))
        pad_y = max(pad_y, max(0.006, h * 0.2))

    left = x - pad_x
    top = y - pad_y - extra_top
    right = x + w + pad_x
    bottom = y + h + pad_y + extra_bottom

    min_x = wx
    max_x = wr
    if layout.page_unit == "spread":
        gutter_slack = 0.025
        if region.page_side == "left":
            min_x = wx
            max_x = min(wr, mid_x + gutter_slack)
        elif region.page_side == "right":
            min_x = max(wx, mid_x - gutter_slack)
            max_x = wr

    return _clamp_bbox(
        left,
        top,
        right - left,
        bottom - top,
        min_x=min_x,
        min_y=wy,
        max_x=max_x,
        max_y=wb,
    )


def _coarsen_layout(layout: LayoutProbe) -> LayoutProbe:
    for region in layout.regions:
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        region.bbox_norm = _expanded_region_bbox(layout, region)
    return layout


def _bbox_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ar = ax + aw
    ab = ay + ah
    br = bx + bw
    bb = by + bh
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ar, br)
    bottom = min(ab, bb)
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    min_area = min(aw * ah, bw * bh)
    if min_area <= 0:
        return 0.0
    return inter / min_area


def _normalize_line_for_compare(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\[[^\]]*\]", "", lowered)
    lowered = re.sub(r"\([^\)]*\)", "", lowered)
    lowered = re.sub(r"[\s\W_]+", "", lowered, flags=re.UNICODE)
    return lowered


def _line_similarity(a: str, b: str) -> float:
    na = _normalize_line_for_compare(a)
    nb = _normalize_line_for_compare(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(a=na, b=nb).ratio()


def _bbox_px(width: int, height: int, bbox_norm: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x, y, w, h = bbox_norm
    left = max(0, min(width, round(x * width)))
    top = max(0, min(height, round(y * height)))
    right = max(left + 1, min(width, round((x + w) * width)))
    bottom = max(top + 1, min(height, round((y + h) * height)))
    return left, top, right, bottom


def _bbox_norm_from_px(width: int, height: int, bbox_px: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    left, top, right, bottom = bbox_px
    return _clamp_bbox(
        left / max(width, 1),
        top / max(height, 1),
        max(1, right - left) / max(width, 1),
        max(1, bottom - top) / max(height, 1),
    )


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


def _draw_pair_overlay(
    image_path: Path,
    *,
    bbox_a: tuple[float, float, float, float],
    bbox_b: tuple[float, float, float, float],
    label_a: str,
    label_b: str,
    out_path: Path,
) -> None:
    with Image.open(image_path).convert("RGB") as image:
        width, height = image.size
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        left_a, top_a, right_a, bottom_a = _bbox_px(width, height, bbox_a)
        left_b, top_b, right_b, bottom_b = _bbox_px(width, height, bbox_b)

        draw.rectangle((left_a, top_a, right_a, bottom_a), outline="#ff4d4f", width=5)
        draw.rectangle((left_b, top_b, right_b, bottom_b), outline="#52c41a", width=5)

        for left, top, label, color in (
            (left_a, top_a, label_a, "#ff4d4f"),
            (left_b, top_b, label_b, "#52c41a"),
        ):
            text_box = draw.textbbox((left, top), label, font=font)
            draw.rectangle(text_box, fill=(255, 255, 255))
            draw.text((left, top), label, fill=color, font=font)

        image.save(out_path, format="PNG")


def _bbox_intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def _expand_bbox_px(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )


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


def _region_crop_path(probe_dir: Path, region_id: str, *, use_blob_refined_crops: bool = False) -> Path | None:
    if use_blob_refined_crops:
        png_path = probe_dir / "blob_refined_crops" / f"{region_id}.png"
        if png_path.exists():
            return png_path
    jpg_path = probe_dir / "crops" / f"{region_id}.jpg"
    if jpg_path.exists():
        return jpg_path
    png_path = probe_dir / "crops" / f"{region_id}.png"
    if png_path.exists():
        return png_path
    return None


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
            types.Part.from_bytes(
                data=crop_path.read_bytes(),
                mime_type="image/png" if crop_path.suffix.lower() == ".png" else "image/jpeg",
            ),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
        ),
    )
    text, _ = _response_text(response)
    payload = _normalize_region_payload(json.loads(_coerce_json_text(text)))
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
    path = probe_dir / "region_reads.json"
    if not path.exists():
        path = probe_dir / "region_orientations.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RegionOrientation.model_validate(item) for item in payload]


def _write_region_orientations(probe_dir: Path, orientations: list[RegionOrientation]) -> Path:
    reads_path = probe_dir / "region_reads.json"
    reads_path.write_text(
        json.dumps([item.model_dump() for item in orientations], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return reads_path


def _load_section_resolution(probe_dir: Path) -> PageSectionResolution | None:
    path = probe_dir / "section_resolution.json"
    if not path.exists():
        return None
    try:
        return PageSectionResolution.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_box_cleanup(probe_dir: Path) -> PageBoxCleanup | None:
    path = probe_dir / "box_cleanup.json"
    if not path.exists():
        return None
    try:
        return PageBoxCleanup.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_page_validation(probe_dir: Path) -> PageValidation | None:
    path = probe_dir / "page_validation.json"
    if not path.exists():
        return None
    try:
        return PageValidation.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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
    reads_path = probe_dir / "region_reads.json"
    for region in layout.regions:
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        if selected and region.region_id not in selected:
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
        partial_order = [item.region_id for item in layout.regions if item.region_id in existing]
        partial_reads = [existing[item_id] for item_id in partial_order]
        _write_region_orientations(probe_dir, partial_reads)

    ordered_region_ids = [region.region_id for region in layout.regions if region.region_id in existing]
    reads = [existing[region_id] for region_id in ordered_region_ids]

    reads_path = _write_region_orientations(probe_dir, reads)
    meta_path = probe_dir / "region_reads_meta.json"
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


def run_page_assembly(probe_dir: Path) -> PageAssemblyArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_orientations(probe_dir)}
    section_resolution = _load_section_resolution(probe_dir)
    box_cleanup = _load_box_cleanup(probe_dir)
    units: list[PageAssemblyUnit] = []
    counter = 1
    section_assignments = {
        item.region_id: item
        for item in (section_resolution.assignments if section_resolution is not None else [])
    }
    if box_cleanup is not None:
        for decision in box_cleanup.decisions:
            assignment_a = section_assignments.get(decision.region_a)
            assignment_b = section_assignments.get(decision.region_b)
            if assignment_a is not None:
                assignment_a.source_block = decision.cleaned_source_block_a
            if assignment_b is not None:
                assignment_b.source_block = decision.cleaned_source_block_b
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        assignment = section_assignments.get(region.region_id)
        source_block = assignment.source_block if assignment is not None else (read.source_block if read else None)
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
                source_block=source_block,
                notes=(
                    list(assignment.notes)
                    if assignment is not None
                    else (list(read.notes) if read else ([region.notes] if region.notes else []))
                ),
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
        markdown_lines.append("")
        if unit.source_block:
            markdown_lines.append("")
            markdown_lines.append("Source Block:")
            markdown_lines.append(unit.source_block)
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
        "section_resolution_path": str((probe_dir / "section_resolution.json").resolve()) if section_resolution is not None else None,
        "box_cleanup_path": str((probe_dir / "box_cleanup.json").resolve()) if box_cleanup is not None else None,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return PageAssemblyArtifact(
        probe_dir=probe_dir,
        assembly_json_path=assembly_json_path,
        assembly_md_path=assembly_md_path,
        meta_path=meta_path,
    )


def run_blob_refinement(
    probe_dir: Path,
    *,
    threshold_block_size: int = 35,
    threshold_c: int = 11,
    min_blob_area: int = 10,
    halo_px: int = 6,
) -> BlobRefinementArtifact:
    import cv2
    import numpy as np

    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    image_path = Path(layout.image_path).resolve()
    image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise FileNotFoundError(f"Could not load image for blob refinement: {image_path}")

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    ink_mask = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        threshold_block_size,
        threshold_c,
    )
    kernel = np.ones((2, 2), dtype=np.uint8)
    ink_mask = cv2.morphologyEx(ink_mask, cv2.MORPH_OPEN, kernel)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(ink_mask, connectivity=8)
    height, width = gray.shape

    active_regions = [
        region
        for region in layout.regions
        if not region.ignore_for_reconstruction and region.reconstruction_priority != "ignore"
    ]
    region_boxes_px = {region.region_id: _bbox_px(width, height, region.bbox_norm) for region in active_regions}
    region_boxes_halo_px = {
        region.region_id: _expand_bbox_px(
            region_boxes_px[region.region_id],
            width=width,
            height=height,
            pad_x=halo_px,
            pad_y=halo_px,
        )
        for region in active_regions
    }
    region_masks: dict[str, np.ndarray] = {
        region.region_id: np.zeros((height, width), dtype=np.uint8) for region in active_regions
    }

    blob_rows: list[dict] = []
    region_blob_counts = {region.region_id: 0 for region in active_regions}

    for label_idx in range(1, num_labels):
        area = int(stats[label_idx, cv2.CC_STAT_AREA])
        if area < min_blob_area:
            continue

        x = int(stats[label_idx, cv2.CC_STAT_LEFT])
        y = int(stats[label_idx, cv2.CC_STAT_TOP])
        w = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        h = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        blob_bbox_px = (x, y, x + w, y + h)
        cx, cy = centroids[label_idx]

        candidate_scores: list[tuple[float, str, dict]] = []
        for region in active_regions:
            coarse_bbox = region_boxes_px[region.region_id]
            halo_bbox = region_boxes_halo_px[region.region_id]
            exact_intersection = _bbox_intersection_area(blob_bbox_px, coarse_bbox)
            halo_intersection = _bbox_intersection_area(blob_bbox_px, halo_bbox)
            centroid_inside = (
                coarse_bbox[0] <= cx <= coarse_bbox[2]
                and coarse_bbox[1] <= cy <= coarse_bbox[3]
            )
            if halo_intersection <= 0 and not centroid_inside:
                continue

            score = (exact_intersection * 10.0) + halo_intersection + (25.0 if centroid_inside else 0.0)
            detail = {
                "region_id": region.region_id,
                "exact_intersection_px": int(exact_intersection),
                "halo_intersection_px": int(halo_intersection),
                "centroid_inside": bool(centroid_inside),
            }
            candidate_scores.append((score, region.region_id, detail))

        if not candidate_scores:
            continue

        candidate_scores.sort(key=lambda item: item[0], reverse=True)
        owner_region_id = candidate_scores[0][1]
        owner_mask = (labels == label_idx)
        region_masks[owner_region_id][owner_mask] = 255
        region_blob_counts[owner_region_id] += 1

        blob_rows.append(
            {
                "blob_id": f"b{label_idx:04d}",
                "bbox_norm": list(_bbox_norm_from_px(width, height, blob_bbox_px)),
                "bbox_px": [x, y, x + w, y + h],
                "area_px": area,
                "centroid_px": [round(float(cx), 2), round(float(cy), 2)],
                "owner_region_id": owner_region_id,
                "candidate_region_ids": [item[1] for item in candidate_scores],
                "candidate_scores": [item[2] for item in candidate_scores[:4]],
            }
        )

    refined_crops_dir = probe_dir / "blob_refined_crops"
    refined_crops_dir.mkdir(parents=True, exist_ok=True)
    region_rows: list[dict] = []

    for region in active_regions:
        coarse_bbox_px = region_boxes_px[region.region_id]
        mask = region_masks[region.region_id]
        ys, xs = np.where(mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            refined_bbox_px = coarse_bbox_px
            refined_mask = np.zeros((coarse_bbox_px[3] - coarse_bbox_px[1], coarse_bbox_px[2] - coarse_bbox_px[0]), dtype=np.uint8)
            crop_rgb = np.full((refined_mask.shape[0], refined_mask.shape[1], 3), 255, dtype=np.uint8)
        else:
            refined_bbox_px = _expand_bbox_px(
                (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                width=width,
                height=height,
                pad_x=2,
                pad_y=2,
            )
            left, top, right, bottom = refined_bbox_px
            refined_mask = mask[top:bottom, left:right]
            crop_rgb = np.full((bottom - top, right - left, 3), 255, dtype=np.uint8)
            source_slice = rgb[top:bottom, left:right]
            keep = refined_mask > 0
            crop_rgb[keep] = source_slice[keep]

        refined_path = refined_crops_dir / f"{region.region_id}.png"
        Image.fromarray(crop_rgb).save(refined_path, format="PNG")
        region_rows.append(
            {
                "region_id": region.region_id,
                "label": region.label,
                "role": region.role,
                "page_side": region.page_side,
                "column_index": region.column_index,
                "coarse_bbox_norm": list(region.bbox_norm),
                "coarse_bbox_px": list(coarse_bbox_px),
                "refined_bbox_norm": list(_bbox_norm_from_px(width, height, refined_bbox_px)),
                "refined_bbox_px": list(refined_bbox_px),
                "assigned_blob_count": region_blob_counts[region.region_id],
                "refined_crop_path": str(refined_path),
            }
        )

    overlay = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    color_map = {
        "main_text": "#ff4d4f",
        "header": "#faad14",
        "marginalia": "#52c41a",
        "page_number": "#13c2c2",
        "other": "#1677ff",
    }

    for region_row in region_rows:
        coarse = tuple(region_row["coarse_bbox_px"])
        refined = tuple(region_row["refined_bbox_px"])
        color = color_map.get(region_row["role"], color_map["other"])
        draw.rectangle(coarse, outline="#bfbfbf", width=2)
        draw.rectangle(refined, outline=color, width=4)
        label = f"{region_row['region_id']} blobs={region_row['assigned_blob_count']}"
        text_box = draw.textbbox((refined[0], refined[1]), label, font=font)
        draw.rectangle(text_box, fill=(255, 255, 255))
        draw.text((refined[0], refined[1]), label, fill=color, font=font)

    blob_overlay_path = probe_dir / "blob_overlay.png"
    overlay.save(blob_overlay_path, format="PNG")

    blob_json_path = probe_dir / "blob_refinement.json"
    payload = {
        "created_at": _utc_now(),
        "doc_id": layout.doc_id,
        "page_id": layout.page_id,
        "image_path": str(image_path),
        "probe_dir": str(probe_dir),
        "threshold_block_size": threshold_block_size,
        "threshold_c": threshold_c,
        "min_blob_area": min_blob_area,
        "halo_px": halo_px,
        "blob_count": len(blob_rows),
        "regions": region_rows,
        "blobs": blob_rows,
    }
    blob_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    meta_path = probe_dir / "blob_refinement_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "blob_json_path": str(blob_json_path),
        "blob_overlay_path": str(blob_overlay_path),
        "refined_crops_dir": str(refined_crops_dir),
        "blob_count": len(blob_rows),
        "region_count": len(region_rows),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return BlobRefinementArtifact(
        probe_dir=probe_dir,
        blob_json_path=blob_json_path,
        blob_overlay_path=blob_overlay_path,
        refined_crops_dir=refined_crops_dir,
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
