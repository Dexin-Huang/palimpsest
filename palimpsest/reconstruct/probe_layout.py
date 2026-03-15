from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from palimpsest.contracts import IMAGE_PARENT_DIRNAMES, layout_probe_output_dir
from palimpsest.models.layout_probe import LayoutProbe


def _default_output_dir(image_path: Path) -> Path:
    return layout_probe_output_dir(image_path)


def _resolve_doc_id(image_path: Path) -> str:
    image_path = image_path.resolve()
    if image_path.parent.name in IMAGE_PARENT_DIRNAMES:
        return image_path.parent.parent.name
    return image_path.parent.name


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
            saved.append(
                {
                    "region_id": region.region_id,
                    "label": region.label,
                    "role": region.role,
                    "page_side": region.page_side,
                    "column_index": region.column_index,
                    "reconstruction_priority": region.reconstruction_priority,
                    "ignore_for_reconstruction": region.ignore_for_reconstruction,
                    "bbox_px": [left, top, right, bottom],
                    "crop_path": str(crop_path),
                }
            )
    return saved


__all__ = [
    "_bbox_px",
    "_coarsen_layout",
    "_default_output_dir",
    "_draw_overlay",
    "_image_page_unit",
    "_resolve_doc_id",
    "_save_crops",
]
