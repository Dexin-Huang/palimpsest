from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import json
from itertools import combinations
from pathlib import Path
import re

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from palimpsest.config import DEFAULT_MODEL_VISION, DEFAULT_MODEL_READING, DEFAULT_MODEL_TRIAGE
from palimpsest.models import (
    BoxCleanupDecision,
    LayoutProbe,
    PageBoxCleanup,
    PageAssembly,
    PageAssemblyUnit,
    PageValidation,
    PageValidationIssue,
    PageSectionResolution,
    RegionOrientation,
    RegionReadPair,
    SectionResolutionAssignment,
    OverlapResolution,
    OverlapResolutionDecision,
)


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


@dataclass
class OverlapResolutionArtifact:
    probe_dir: Path
    resolution_json_path: Path
    meta_path: Path
    model: str


@dataclass
class SectionResolutionArtifact:
    probe_dir: Path
    resolution_json_path: Path
    meta_path: Path
    model: str


@dataclass
class BoxCleanupArtifact:
    probe_dir: Path
    cleanup_json_path: Path
    meta_path: Path
    model: str


@dataclass
class PageValidationArtifact:
    probe_dir: Path
    validation_json_path: Path
    validation_md_path: Path
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


def _context_excerpt(lines: list[str], index: int, *, radius: int = 2) -> str:
    if not lines:
        return ""
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(line for line in lines[start:end] if line.strip())


def _role_ownership_rank(role: str) -> int:
    ranks = {
        "main_text": 0,
        "marginalia": 1,
        "header": 2,
        "page_number": 3,
    }
    return ranks.get(role, 9)


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


def _load_overlap_resolution(probe_dir: Path) -> OverlapResolution | None:
    path = probe_dir / "overlap_resolution.json"
    if not path.exists():
        return None
    try:
        return OverlapResolution.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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


def _overlap_candidate_payload(layout: LayoutProbe, reads: dict[str, RegionOrientation]) -> list[dict]:
    candidate_payload: list[dict] = []
    for region_a, region_b in combinations(layout.regions, 2):
        if region_a.ignore_for_reconstruction or region_b.ignore_for_reconstruction:
            continue
        if region_a.reconstruction_priority == "ignore" or region_b.reconstruction_priority == "ignore":
            continue
        if region_a.page_side and region_b.page_side and region_a.page_side != region_b.page_side:
            continue
        overlap_ratio = _bbox_overlap_ratio(region_a.bbox_norm, region_b.bbox_norm)
        if overlap_ratio < 0.08:
            continue
        read_a = reads.get(region_a.region_id)
        read_b = reads.get(region_b.region_id)
        if not read_a or not read_b:
            continue
        lines_a = read_a.diplomatic_lines or [pair.source for pair in read_a.pairs if pair.source]
        lines_b = read_b.diplomatic_lines or [pair.source for pair in read_b.pairs if pair.source]
        for idx_a, pair_a in enumerate(read_a.pairs):
            if not pair_a.source.strip():
                continue
            for idx_b, pair_b in enumerate(read_b.pairs):
                if not pair_b.source.strip():
                    continue
                similarity = _line_similarity(pair_a.source, pair_b.source)
                if similarity < 0.52:
                    continue
                candidate_payload.append(
                    {
                        "candidate_id": f"{region_a.region_id}:{idx_a}-{region_b.region_id}:{idx_b}",
                        "region_a": region_a.region_id,
                        "region_b": region_b.region_id,
                        "pair_index_a": idx_a,
                        "pair_index_b": idx_b,
                        "label_a": region_a.label,
                        "label_b": region_b.label,
                        "role_a": region_a.role,
                        "role_b": region_b.role,
                        "reading_order_a": region_a.reading_order,
                        "reading_order_b": region_b.reading_order,
                        "page_side_a": region_a.page_side,
                        "page_side_b": region_b.page_side,
                        "column_index_a": region_a.column_index,
                        "column_index_b": region_b.column_index,
                        "bbox_a": list(region_a.bbox_norm),
                        "bbox_b": list(region_b.bbox_norm),
                        "overlap_ratio": round(overlap_ratio, 4),
                        "similarity": round(similarity, 4),
                        "source_a": pair_a.source,
                        "source_b": pair_b.source,
                        "context_a": _context_excerpt(lines_a, idx_a),
                        "context_b": _context_excerpt(lines_b, idx_b),
                        "region_source_block_a": read_a.source_block or "\n".join(lines_a),
                        "region_source_block_b": read_b.source_block or "\n".join(lines_b),
                        "translation_a": pair_a.translation,
                        "translation_b": pair_b.translation,
                    }
                )
    # Keep best candidate per exact region pair + line pair by similarity.
    deduped: dict[str, dict] = {}
    for item in candidate_payload:
        key = item["candidate_id"]
        current = deduped.get(key)
        if current is None or item["similarity"] > current["similarity"]:
            deduped[key] = item
    return list(deduped.values())


def _fallback_overlap_decisions(layout: LayoutProbe, candidates: list[dict]) -> OverlapResolution:
    region_lookup = {region.region_id: region for region in layout.regions}
    decisions: list[OverlapResolutionDecision] = []
    for item in candidates:
        region_a = region_lookup[item["region_a"]]
        region_b = region_lookup[item["region_b"]]
        if _role_ownership_rank(region_a.role) < _role_ownership_rank(region_b.role):
            canonical = region_a.region_id
            relation = "region_a_owns"
        elif _role_ownership_rank(region_b.role) < _role_ownership_rank(region_a.role):
            canonical = region_b.region_id
            relation = "region_b_owns"
        else:
            order_a = region_a.reading_order or 9999
            order_b = region_b.reading_order or 9999
            canonical = region_a.region_id if order_a <= order_b else region_b.region_id
            relation = "shared_duplicate"
        decisions.append(
            OverlapResolutionDecision(
                candidate_id=item["candidate_id"],
                region_a=item["region_a"],
                region_b=item["region_b"],
                pair_index_a=item["pair_index_a"],
                pair_index_b=item["pair_index_b"],
                relation=relation,
                canonical_region_id=canonical,
                canonical_text=item["source_a"] if canonical == item["region_a"] else item["source_b"],
                reason="Deterministic fallback based on role priority and reading order.",
            )
        )
    return OverlapResolution(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        decisions=decisions,
    )


def _section_resolution_payload(layout: LayoutProbe, reads: dict[str, RegionOrientation]) -> list[dict]:
    payload: list[dict] = []
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        if not read or not (read.source_block or read.diplomatic_lines):
            continue
        payload.append(
            {
                "region_id": region.region_id,
                "label": region.label,
                "role": region.role,
                "page_side": region.page_side,
                "column_index": region.column_index,
                "reading_order": region.reading_order,
                "bbox_norm": list(region.bbox_norm),
                "source_block": read.source_block or "\n".join(read.diplomatic_lines),
            }
        )
    return payload


def _fallback_section_resolution(layout: LayoutProbe, reads: dict[str, RegionOrientation]) -> PageSectionResolution:
    assignments: list[SectionResolutionAssignment] = []
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        if not read or not (read.source_block or read.diplomatic_lines):
            continue
        lines = [line for line in (read.diplomatic_lines or []) if str(line).strip()]
        if not lines and read.source_block:
            lines = [line for line in read.source_block.splitlines() if line.strip()]

        if region.role == "header":
            kept: list[str] = []
            for line in lines:
                compact = re.sub(r"\s+", "", line)
                if not compact:
                    continue
                if len(compact) <= 2:
                    kept.append(compact)
                else:
                    break
            source_block = "\n".join(kept)
            render_in_witness = bool(source_block)
        elif region.role == "page_number":
            digits = [re.sub(r"\s+", "", line) for line in lines if re.sub(r"\s+", "", line)]
            source_block = "\n".join(digits[:1])
            render_in_witness = False
        else:
            source_block = read.source_block or "\n".join(lines)
            render_in_witness = True

        assignments.append(
            SectionResolutionAssignment(
                region_id=region.region_id,
                label=region.label,
                role=region.role,
                page_side=region.page_side,
                column_index=region.column_index,
                render_in_witness=render_in_witness,
                source_block=source_block,
                notes=["Deterministic fallback section assignment."],
            )
        )

    return PageSectionResolution(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        assignments=assignments,
    )


def run_section_resolution(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
) -> SectionResolutionArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_orientations(probe_dir)}
    sections = _section_resolution_payload(layout, reads)

    resolution_json_path = probe_dir / "section_resolution.json"
    meta_path = probe_dir / "section_resolution_meta.json"

    if not sections:
        resolution = PageSectionResolution(
            created_at=_utc_now(),
            doc_id=layout.doc_id,
            page_id=layout.page_id,
            assignments=[],
        )
        resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "resolution_json_path": str(resolution_json_path),
                    "model": None,
                    "section_count": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SectionResolutionArtifact(
            probe_dir=probe_dir,
            resolution_json_path=resolution_json_path,
            meta_path=meta_path,
            model=model,
        )

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_section_resolution")
    prompt_text = (
        prompt_text
        .replace("{PAGE_ID}", layout.page_id)
        .replace("{SECTIONS_JSON}", json.dumps(sections, indent=2, ensure_ascii=False))
    )

    client = genai.Client()
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt_text],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
            ),
        )
        text, _ = _response_text(response)
        payload = json.loads(_coerce_json_text(text))
        payload.update({
            "created_at": _utc_now(),
            "doc_id": layout.doc_id,
            "page_id": layout.page_id,
        })
        resolution = PageSectionResolution.model_validate(payload)
    except Exception:
        resolution = _fallback_section_resolution(layout, reads)

    resolved_by_region = {item.region_id: item for item in resolution.assignments}
    normalized_assignments: list[SectionResolutionAssignment] = []
    for section in sections:
        candidate = resolved_by_region.get(section["region_id"])
        source_block = candidate.source_block if candidate is not None else section["source_block"]
        render_in_witness = candidate.render_in_witness if candidate is not None else (section["role"] != "page_number")
        notes = list(candidate.notes) if candidate is not None else []

        if section["role"] == "header":
            header_lines = [line for line in str(source_block or "").splitlines() if line.strip()]
            kept_header_lines: list[str] = []
            for line in header_lines:
                compact = re.sub(r"\s+", "", line)
                if not compact:
                    continue
                if len(compact) <= 3:
                    kept_header_lines.append(line)
                else:
                    break
            if kept_header_lines != header_lines:
                notes = notes + ["Header trimmed to compact title lines only."]
            source_block = "\n".join(kept_header_lines)

        if section["role"] == "main_text" and not str(source_block or "").strip():
            source_block = section["source_block"]
            notes = notes + ["Main text restored from source transcription because model returned empty text."]

        normalized_assignments.append(
            SectionResolutionAssignment(
                region_id=section["region_id"],
                label=section["label"],
                role=section["role"],
                page_side=section["page_side"],
                column_index=section["column_index"],
                render_in_witness=render_in_witness,
                source_block=str(source_block or ""),
                notes=notes,
            )
        )

    resolution = PageSectionResolution(
        created_at=resolution.created_at,
        doc_id=resolution.doc_id,
        page_id=resolution.page_id,
        assignments=normalized_assignments,
    )

    resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "resolution_json_path": str(resolution_json_path),
                "model": model,
                "section_count": len(sections),
                "prompt_path": str(prompt_path) if 'prompt_path' in locals() else None,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return SectionResolutionArtifact(
        probe_dir=probe_dir,
        resolution_json_path=resolution_json_path,
        meta_path=meta_path,
        model=model,
    )


def _compact_text_for_match(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in "[](){}<>.,;:!?\"'`-_=+|/\\")


def _shared_substring_size(a: str, b: str) -> int:
    if not a or not b:
        return 0
    return SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b)).size


def _box_cleanup_candidates(layout: LayoutProbe, section_resolution: PageSectionResolution) -> list[dict]:
    region_lookup = {region.region_id: region for region in layout.regions}
    assignments = [
        item for item in section_resolution.assignments
        if item.render_in_witness and item.role != "page_number" and str(item.source_block or "").strip()
    ]
    candidates: list[dict] = []
    for a, b in combinations(assignments, 2):
        region_a = region_lookup.get(a.region_id)
        region_b = region_lookup.get(b.region_id)
        if region_a is None or region_b is None:
            continue
        if region_a.page_side and region_b.page_side and region_a.page_side != region_b.page_side:
            continue

        header_main_neighbor = (
            {a.role, b.role} == {"header", "main_text"}
            and a.page_side == b.page_side
            and a.column_index == b.column_index
        )
        overlap_ratio = _bbox_overlap_ratio(region_a.bbox_norm, region_b.bbox_norm)
        if overlap_ratio < 0.03 and not header_main_neighbor:
            continue

        text_a = str(a.source_block or "").strip()
        text_b = str(b.source_block or "").strip()
        compact_a = _compact_text_for_match(text_a)
        compact_b = _compact_text_for_match(text_b)
        similarity = _line_similarity(compact_a, compact_b) if compact_a and compact_b else 0.0
        shared = _shared_substring_size(compact_a, compact_b)

        if similarity < 0.08 and shared < 2 and not header_main_neighbor:
            continue

        candidates.append(
            {
                "pair_id": f"{a.region_id}:{b.region_id}",
                "region_a": a.region_id,
                "region_b": b.region_id,
                "label_a": a.label,
                "label_b": b.label,
                "role_a": a.role,
                "role_b": b.role,
                "page_side_a": a.page_side,
                "page_side_b": b.page_side,
                "column_index_a": a.column_index,
                "column_index_b": b.column_index,
                "bbox_a": list(region_a.bbox_norm),
                "bbox_b": list(region_b.bbox_norm),
                "overlap_ratio": round(overlap_ratio, 4),
                "text_similarity": round(similarity, 4),
                "shared_substring_size": shared,
                "header_main_neighbor": header_main_neighbor,
                "source_block_a": text_a,
                "source_block_b": text_b,
            }
        )
    return candidates


def _fallback_box_cleanup(section_resolution: PageSectionResolution) -> PageBoxCleanup:
    return PageBoxCleanup(
        created_at=_utc_now(),
        doc_id=section_resolution.doc_id,
        page_id=section_resolution.page_id,
        decisions=[],
    )


def run_box_cleanup(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
) -> BoxCleanupArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    section_resolution = _load_section_resolution(probe_dir)
    if section_resolution is None:
        raise FileNotFoundError(f"No section_resolution.json found in {probe_dir}")

    candidates = _box_cleanup_candidates(layout, section_resolution)
    cleanup_json_path = probe_dir / "box_cleanup.json"
    meta_path = probe_dir / "box_cleanup_meta.json"

    if not candidates:
        cleanup = _fallback_box_cleanup(section_resolution)
        cleanup_json_path.write_text(cleanup.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "cleanup_json_path": str(cleanup_json_path),
                    "model": None,
                    "candidate_count": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return BoxCleanupArtifact(
            probe_dir=probe_dir,
            cleanup_json_path=cleanup_json_path,
            meta_path=meta_path,
            model=model,
        )

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_box_cleanup")
    client = genai.Client()
    decisions: list[BoxCleanupDecision] = []
    for candidate in candidates:
        final_prompt = (
            prompt_text
            .replace("{PAGE_ID}", layout.page_id)
            .replace("{PAIR_JSON}", json.dumps(candidate, indent=2, ensure_ascii=False))
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=[final_prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
                ),
            )
            text, _ = _response_text(response)
            payload = json.loads(_coerce_json_text(text))
            payload.setdefault("pair_id", candidate["pair_id"])
            payload.setdefault("region_a", candidate["region_a"])
            payload.setdefault("region_b", candidate["region_b"])
            decisions.append(BoxCleanupDecision.model_validate(payload))
        except Exception:
            decisions.append(
                BoxCleanupDecision(
                    pair_id=candidate["pair_id"],
                    region_a=candidate["region_a"],
                    region_b=candidate["region_b"],
                    relation="uncertain",
                    cleaned_source_block_a=candidate["source_block_a"],
                    cleaned_source_block_b=candidate["source_block_b"],
                    reason="Fallback preserved original blocks.",
                )
            )

    cleanup = PageBoxCleanup(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        decisions=decisions,
    )
    cleanup_json_path.write_text(cleanup.model_dump_json(indent=2), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "cleanup_json_path": str(cleanup_json_path),
                "model": model,
                "candidate_count": len(candidates),
                "prompt_path": str(prompt_path) if 'prompt_path' in locals() else None,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return BoxCleanupArtifact(
        probe_dir=probe_dir,
        cleanup_json_path=cleanup_json_path,
        meta_path=meta_path,
        model=model,
    )


def run_overlap_resolution(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
) -> OverlapResolutionArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_orientations(probe_dir)}
    candidates = _overlap_candidate_payload(layout, reads)

    resolution_json_path = probe_dir / "overlap_resolution.json"
    meta_path = probe_dir / "overlap_resolution_meta.json"

    if not candidates:
        resolution = OverlapResolution(
            created_at=_utc_now(),
            doc_id=layout.doc_id,
            page_id=layout.page_id,
            decisions=[],
        )
        resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "resolution_json_path": str(resolution_json_path),
                    "model": None,
                    "candidate_count": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return OverlapResolutionArtifact(
            probe_dir=probe_dir,
            resolution_json_path=resolution_json_path,
            meta_path=meta_path,
            model=model,
        )

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_overlap_resolution")
    prompt_text = (
        prompt_text
        .replace("{PAGE_ID}", layout.page_id)
        .replace("{CANDIDATES_JSON}", json.dumps(candidates, indent=2, ensure_ascii=False))
    )

    client = genai.Client()
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt_text],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
            ),
        )
        text, _ = _response_text(response)
        payload = json.loads(_coerce_json_text(text))
        payload.update({
            "created_at": _utc_now(),
            "doc_id": layout.doc_id,
            "page_id": layout.page_id,
        })
        resolution = OverlapResolution.model_validate(payload)
    except Exception:
        resolution = _fallback_overlap_decisions(layout, candidates)

    resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "resolution_json_path": str(resolution_json_path),
                "model": model,
                "candidate_count": len(candidates),
                "prompt_path": str(prompt_path) if 'prompt_path' in locals() else None,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return OverlapResolutionArtifact(
        probe_dir=probe_dir,
        resolution_json_path=resolution_json_path,
        meta_path=meta_path,
        model=model,
    )


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
    reads_path = probe_dir / "region_orientations.json"
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
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return RegionReadsArtifact(probe_dir=probe_dir, reads_path=reads_path, meta_path=meta_path, model=model)


def run_page_assembly(probe_dir: Path) -> PageAssemblyArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_orientations(probe_dir)}
    section_resolution = _load_section_resolution(probe_dir)
    box_cleanup = _load_box_cleanup(probe_dir)
    overlap_resolution = _load_overlap_resolution(probe_dir)
    suppressed_pairs: set[tuple[str, int]] = set()
    if section_resolution is None and overlap_resolution is not None:
        for decision in overlap_resolution.decisions:
            canonical = decision.canonical_region_id
            if canonical == decision.region_a:
                suppressed_pairs.add((decision.region_b, decision.pair_index_b))
            elif canonical == decision.region_b:
                suppressed_pairs.add((decision.region_a, decision.pair_index_a))

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
        kept_pairs = []
        if assignment is not None:
            lines = [line for line in assignment.source_block.splitlines() if line.strip()]
            kept_pairs = [RegionReadPair(source=line, translation="") for line in lines]
        elif read:
            for idx, pair in enumerate(read.pairs):
                if (region.region_id, idx) in suppressed_pairs:
                    continue
                kept_pairs.append(pair)
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
                source_block=assignment.source_block if assignment is not None else (read.source_block if read else None),
                translation_block=read.translation_block if read and assignment is None else None,
                pairs=kept_pairs if (read or assignment is not None) else [],
                diplomatic_lines=[] if (read or assignment is not None) else [],
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
        if unit.reading_direction:
            markdown_lines.append(f"- Reading direction: `{unit.reading_direction}`")
        if unit.line_flow:
            markdown_lines.append(f"- Line flow: `{unit.line_flow}`")
        markdown_lines.append("")
        for pair in unit.pairs:
            markdown_lines.append(pair.source)
            if pair.translation:
                markdown_lines.append(f"=> {pair.translation}")
            if pair.note:
                markdown_lines.append(f"   note: {pair.note}")
        if unit.source_block:
            markdown_lines.append("")
            markdown_lines.append("Source Block:")
            markdown_lines.append(unit.source_block)
        if unit.translation_block:
            markdown_lines.append("")
            markdown_lines.append("Translation Block:")
            markdown_lines.append(unit.translation_block)
        if not unit.pairs:
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
        "section_resolution_path": str((probe_dir / "section_resolution.json").resolve()) if section_resolution is not None else None,
        "box_cleanup_path": str((probe_dir / "box_cleanup.json").resolve()) if box_cleanup is not None else None,
        "overlap_resolution_path": str((probe_dir / "overlap_resolution.json").resolve()) if overlap_resolution is not None else None,
        "suppressed_pair_count": len(suppressed_pairs),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return PageAssemblyArtifact(
        probe_dir=probe_dir,
        assembly_json_path=assembly_json_path,
        assembly_md_path=assembly_md_path,
        meta_path=meta_path,
    )


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff" for ch in text)


def _count_cjk_chars(text: str) -> int:
    return sum(1 for ch in text if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff")


def _find_neighbor_header(units: list[PageAssemblyUnit], target: PageAssemblyUnit) -> PageAssemblyUnit | None:
    for unit in units:
        if unit.role != "header":
            continue
        if unit.page_side != target.page_side:
            continue
        if unit.column_index != target.column_index:
            continue
        return unit
    return None


def run_page_validate(probe_dir: Path) -> PageValidationArtifact:
    probe_dir = probe_dir.resolve()
    assembly_path = probe_dir / "page_assembly.json"
    assembly = PageAssembly.model_validate_json(assembly_path.read_text(encoding="utf-8"))

    issues: list[PageValidationIssue] = []

    for unit in assembly.units:
        lines = _nonempty_lines(unit.source_block or "")

        if unit.role == "main_text":
            header = _find_neighbor_header(assembly.units, unit)
            if header is not None and lines:
                short_prefix: list[str] = []
                for line in lines[:3]:
                    compact = re.sub(r"\s+", "", line)
                    if 0 < len(compact) <= 2:
                        short_prefix.append(compact)
                    else:
                        break
                header_text = "".join(_nonempty_lines(header.source_block or ""))
                if short_prefix:
                    evidence = [f"leading_short_lines={short_prefix}", f"header={header_text}"]
                    issues.append(
                        PageValidationIssue(
                            issue_id=f"{unit.region_id}:header_spill",
                            issue_type="header_spill_into_main",
                            severity="high",
                            region_ids=[header.region_id, unit.region_id],
                            message=f"{unit.label} begins with short header-like spill before the body text.",
                            evidence=evidence,
                        )
                    )

        elif unit.role == "marginalia":
            text = unit.source_block or ""
            cjk_count = _count_cjk_chars(text)
            if cjk_count > 0:
                issues.append(
                    PageValidationIssue(
                        issue_id=f"{unit.region_id}:script_contamination",
                        issue_type="marginalia_script_contamination",
                        severity="high",
                        region_ids=[unit.region_id],
                        message=f"{unit.label} contains CJK spill even though it should be marginalia of a different script family.",
                        evidence=[f"cjk_count={cjk_count}", f"preview={''.join(_nonempty_lines(text)[:4])[:120]}"],
                    )
                )

        elif unit.role == "page_number":
            lines = _nonempty_lines(unit.source_block or "")
            compact = "".join(lines)
            normalized = compact.lower()
            if len(lines) > 1 or not re.fullmatch(r"[0-9ivxlcdmfrv.\-]+", normalized):
                issues.append(
                    PageValidationIssue(
                        issue_id=f"{unit.region_id}:page_number_noise",
                        issue_type="page_number_noise",
                        severity="medium",
                        region_ids=[unit.region_id],
                        message=f"{unit.label} contains extra noise beyond a simple folio/page mark.",
                        evidence=[f"lines={lines}", f"compact={compact}"],
                    )
                )

    status = "needs_repair" if issues else "clean"
    score = sum({"low": 1, "medium": 2, "high": 3}[issue.severity] for issue in issues)
    validation = PageValidation(
        created_at=_utc_now(),
        doc_id=assembly.doc_id,
        page_id=assembly.page_id,
        status=status,
        score=score,
        issues=issues,
    )

    validation_json_path = probe_dir / "page_validation.json"
    validation_md_path = probe_dir / "page_validation.md"
    validation_json_path.write_text(validation.model_dump_json(indent=2), encoding="utf-8")

    markdown_lines = [
        f"# Page Validation: {validation.page_id}",
        "",
        f"- Status: `{validation.status}`",
        f"- Score: `{validation.score}`",
        f"- Issue count: `{len(validation.issues)}`",
        "",
    ]
    if not validation.issues:
        markdown_lines.append("No structural issues detected.")
    else:
        for issue in validation.issues:
            markdown_lines.extend(
                [
                    f"## {issue.issue_type}",
                    f"- Severity: `{issue.severity}`",
                    f"- Regions: `{issue.region_ids}`",
                    f"- Message: {issue.message}",
                ]
            )
            if issue.evidence:
                markdown_lines.append("- Evidence:")
                for line in issue.evidence:
                    markdown_lines.append(f"  - {line}")
            markdown_lines.append("")
    validation_md_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    meta_path = probe_dir / "page_validation_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "validation_json_path": str(validation_json_path),
        "validation_md_path": str(validation_md_path),
        "assembly_path": str(assembly_path),
        "issue_count": len(validation.issues),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return PageValidationArtifact(
        probe_dir=probe_dir,
        validation_json_path=validation_json_path,
        validation_md_path=validation_md_path,
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
