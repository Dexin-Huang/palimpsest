from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

from palimpsest.reconstruct.probe_layout import _bbox_px

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
