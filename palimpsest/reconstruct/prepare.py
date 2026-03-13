from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from palimpsest.contracts import prepare_meta_path, prepare_output_dir, prepared_image_path
from PIL import Image


DEFAULT_FOOTER_TRIM_RATIO = 0.12
DEFAULT_INK_THRESHOLD = 220
DEFAULT_PADDING_PX = 12


@dataclass
class PreparedPageArtifact:
    source_image_path: Path
    prepared_image_path: Path
    meta_path: Path
    bbox_px: tuple[int, int, int, int]
    footer_trim_ratio: float
    ink_threshold: int
    padding_px: int


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_output_dir(image_path: Path) -> Path:
    return prepare_output_dir(image_path)


def detect_content_bbox(
    image_path: Path,
    *,
    footer_trim_ratio: float = DEFAULT_FOOTER_TRIM_RATIO,
    ink_threshold: int = DEFAULT_INK_THRESHOLD,
    padding_px: int = DEFAULT_PADDING_PX,
) -> tuple[int, int, int, int]:
    image_path = image_path.resolve()
    with Image.open(image_path) as image:
        gray = image.convert("L")
        width, height = gray.size
        cutoff = max(1, min(height, int(round(height * (1.0 - footer_trim_ratio)))))
        pixels = gray.load()

        min_x = width
        min_y = cutoff
        max_x = -1
        max_y = -1

        for y in range(cutoff):
            for x in range(width):
                if pixels[x, y] < ink_threshold:
                    if x < min_x:
                        min_x = x
                    if y < min_y:
                        min_y = y
                    if x > max_x:
                        max_x = x
                    if y > max_y:
                        max_y = y

        if max_x < min_x or max_y < min_y:
            return (0, 0, width, cutoff)

        x0 = max(0, min_x - padding_px)
        y0 = max(0, min_y - padding_px)
        x1 = min(width, max_x + 1 + padding_px)
        y1 = min(cutoff, max_y + 1 + padding_px)
        return (x0, y0, x1, y1)


def prepare_image(
    image_path: Path,
    *,
    out_dir: Path | None = None,
    footer_trim_ratio: float = DEFAULT_FOOTER_TRIM_RATIO,
    ink_threshold: int = DEFAULT_INK_THRESHOLD,
    padding_px: int = DEFAULT_PADDING_PX,
) -> PreparedPageArtifact:
    image_path = image_path.resolve()
    target_dir = (out_dir.resolve() if out_dir else _default_output_dir(image_path).resolve())
    target_dir.mkdir(parents=True, exist_ok=True)

    bbox_px = detect_content_bbox(
        image_path,
        footer_trim_ratio=footer_trim_ratio,
        ink_threshold=ink_threshold,
        padding_px=padding_px,
    )

    resolved_prepared_image_path = prepared_image_path(target_dir, image_path)
    with Image.open(image_path) as image:
        image.crop(bbox_px).save(resolved_prepared_image_path, format="JPEG", quality=95)

    meta_path = prepare_meta_path(target_dir)
    meta = {
        "generated_at": _utc_now(),
        "source_image_path": str(image_path),
        "prepared_image_path": str(resolved_prepared_image_path),
        "bbox_px": list(bbox_px),
        "footer_trim_ratio": footer_trim_ratio,
        "ink_threshold": ink_threshold,
        "padding_px": padding_px,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return PreparedPageArtifact(
        source_image_path=image_path,
        prepared_image_path=resolved_prepared_image_path,
        meta_path=meta_path,
        bbox_px=bbox_px,
        footer_trim_ratio=footer_trim_ratio,
        ink_threshold=ink_threshold,
        padding_px=padding_px,
    )
