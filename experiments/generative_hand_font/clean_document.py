"""Render source-preserving P.3477 readability-cleanup prototypes.

The archival input is never modified. Three deterministic derivatives expose the
trade-off between faint-stroke retention and a crisp, Kai-like black-on-white
rendering; none is documentary evidence or a replacement for the source image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out" / "document_cleanup"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flatten_luminance(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Divide away low-frequency paper illumination without touching geometry."""

    sigma = max(gray.shape) / 60
    field = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma).astype(np.float32)
    flat = gray.astype(np.float32) / np.maximum(field, 1.0) * 248.0
    return np.clip(flat, 0, 255).astype(np.uint8), field


def sharpen(gray: np.ndarray, *, amount: float) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=0.75)
    return cv2.addWeighted(gray, 1.0 + amount, blurred, -amount, 0)


def soft_readability(flat: np.ndarray) -> np.ndarray:
    """Whiten paper and deepen ink continuously; retain faint marks."""

    denoised = cv2.bilateralFilter(flat, 5, 16, 16)
    darkness = 255.0 - denoised.astype(np.float32)
    mapped = np.clip((darkness - 4.0) * 1.7, 0, 255)
    output = np.uint8(255.0 - mapped)
    output[output >= 249] = 255
    return sharpen(output, amount=0.55)


def component_support(
    flat: np.ndarray,
    *,
    support_darkness: float,
    core_darkness: float,
    min_area: int,
) -> np.ndarray:
    """Keep only locally dark components containing an ink-strength core."""

    denoised = cv2.bilateralFilter(flat, 5, 16, 16)
    darkness = 255.0 - denoised.astype(np.float32)
    adaptive = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        10,
    )
    support = ((darkness >= support_darkness) & (adaptive > 0)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(support, connectivity=8)
    keep = np.zeros(count, dtype=np.uint8)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        component = labels == label
        if float(darkness[component].max()) >= core_darkness:
            keep[label] = 1
    return keep[labels].astype(bool)


def contained_ink(flat: np.ndarray) -> np.ndarray:
    """Anti-aliased black-on-white ink constrained to core-bearing components."""

    denoised = cv2.bilateralFilter(flat, 5, 16, 16)
    darkness = 255.0 - denoised.astype(np.float32)
    support = component_support(
        flat, support_darkness=6.0, core_darkness=34.0, min_area=3
    )
    alpha = np.clip((darkness - 6.0) / 42.0, 0, 1)
    alpha = np.power(alpha, 0.72)
    alpha[~support] = 0
    output = np.uint8(np.clip(255.0 * (1.0 - alpha), 0, 255))
    output[output >= 250] = 255
    return sharpen(output, amount=0.35)


def balanced_ink(flat: np.ndarray) -> np.ndarray:
    """Crisp white-paper rendering without filling weak stroke edges black."""

    denoised = cv2.bilateralFilter(flat, 5, 16, 16)
    darkness = 255.0 - denoised.astype(np.float32)
    support = component_support(
        flat, support_darkness=7.0, core_darkness=42.0, min_area=5
    )
    mapped = np.clip((darkness - 7.0) * 2.1, 0, 255)
    mapped[~support] = 0
    output = np.uint8(255.0 - mapped)
    output[output >= 250] = 255
    return sharpen(output, amount=0.3)


def binary_ink(flat: np.ndarray) -> np.ndarray:
    """Destructive CV mask: useful for comparison, never the sole page output."""

    support = component_support(
        flat, support_darkness=8.0, core_darkness=42.0, min_area=4
    )
    return np.where(support, 0, 255).astype(np.uint8)


def quality_metrics(source: np.ndarray, output: np.ndarray) -> dict[str, float]:
    source_flat, _ = flatten_luminance(source)
    source_darkness = 255 - source_flat.astype(np.float32)
    output_darkness = 255 - output.astype(np.float32)
    core = source_darkness >= 50
    background = source_darkness <= 5
    foreground = output_darkness >= 18
    return {
        "background_mean": round(float(output[background].mean()), 4),
        "background_std": round(float(output[background].std()), 4),
        "source_core_retention": round(float(foreground[core].mean()), 6),
        "new_ink_on_source_background": round(float(foreground[background].mean()), 6),
        "foreground_fraction": round(float(foreground.mean()), 6),
    }


def resize_preview(image: Image.Image, width: int) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def render_comparison(
    source: np.ndarray,
    outputs: dict[str, np.ndarray],
    path: Path,
) -> None:
    labels = ["SOURCE", *outputs]
    images = [
        Image.fromarray(source),
        *(Image.fromarray(value) for value in outputs.values()),
    ]
    preview_width = 650
    previews = [resize_preview(image, preview_width) for image in images]
    header = 54
    gap = 12
    width = len(previews) * preview_width + (len(previews) - 1) * gap
    height = header + max(image.height for image in previews)
    sheet = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/arialbd.ttf")
    font = (
        ImageFont.truetype(str(font_path), 21)
        if font_path.exists()
        else ImageFont.load_default()
    )
    for index, (label, image) in enumerate(zip(labels, previews, strict=True)):
        x = index * (preview_width + gap)
        draw.text((x + 12, 16), label, fill=20, font=font)
        sheet.paste(image, (x, header))
    sheet.save(path)


def render_details(
    source: np.ndarray,
    outputs: dict[str, np.ndarray],
    path: Path,
) -> None:
    height, width = source.shape
    regions = (
        (
            "dark ink",
            (
                int(width * 0.62),
                int(height * 0.20),
                int(width * 0.88),
                int(height * 0.52),
            ),
        ),
        (
            "mixed ink",
            (
                int(width * 0.25),
                int(height * 0.31),
                int(width * 0.51),
                int(height * 0.63),
            ),
        ),
        (
            "faint/damaged",
            (
                int(width * 0.46),
                int(height * 0.48),
                int(width * 0.72),
                int(height * 0.80),
            ),
        ),
    )
    labels = ["SOURCE", *outputs]
    columns = len(labels)
    tile_width = 520
    header = 48
    row_label = 42
    gap = 10
    rendered_rows: list[list[Image.Image]] = []
    for _, (x0, y0, x1, y1) in regions:
        arrays = [source, *outputs.values()]
        rendered_rows.append(
            [
                resize_preview(Image.fromarray(array[y0:y1, x0:x1]), tile_width)
                for array in arrays
            ]
        )
    tile_height = max(image.height for row in rendered_rows for image in row)
    sheet = Image.new(
        "L",
        (
            columns * tile_width + (columns - 1) * gap,
            header + len(regions) * (row_label + tile_height + gap),
        ),
        255,
    )
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/arialbd.ttf")
    font = (
        ImageFont.truetype(str(font_path), 19)
        if font_path.exists()
        else ImageFont.load_default()
    )
    for column, label in enumerate(labels):
        draw.text((column * (tile_width + gap) + 10, 14), label, fill=20, font=font)
    y = header
    for (region_label, _), row in zip(regions, rendered_rows, strict=True):
        draw.text((10, y + 9), region_label, fill=55, font=font)
        y += row_label
        for column, image in enumerate(row):
            sheet.paste(image, (column * (tile_width + gap), y))
        y += tile_height + gap
    sheet.save(path)


def process(source_path: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite cleanup prototype: {output_dir}")
    source = cv2.imread(str(source_path), cv2.IMREAD_GRAYSCALE)
    if source is None:
        raise FileNotFoundError(source_path)
    flat, _ = flatten_luminance(source)
    outputs = {
        "SOFT": soft_readability(flat),
        "BALANCED": balanced_ink(flat),
        "CONTAINED": contained_ink(flat),
    }
    output_dir.mkdir(parents=True)
    artifact_paths: dict[str, Path] = {}
    for name, image in outputs.items():
        path = output_dir / f"{name.lower()}.png"
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"Cannot write {path}")
        artifact_paths[name] = path
    comparison_path = output_dir / "comparison-full.png"
    details_path = output_dir / "comparison-details.png"
    render_comparison(source, outputs, comparison_path)
    render_details(source, outputs, details_path)
    record = {
        "schema_version": 1,
        "kind": "document_cleanup_development_prototype",
        "source_path": source_path.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(source_path),
        "source_dimensions": [int(source.shape[1]), int(source.shape[0])],
        "source_was_modified": False,
        "generated_pixels_are_documentary_evidence": False,
        "variants": {
            name.lower(): {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256(path),
                "metrics": quality_metrics(source, outputs[name]),
            }
            for name, path in artifact_paths.items()
        },
        "comparisons": {
            "full": {
                "path": comparison_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(comparison_path),
            },
            "details": {
                "path": details_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(details_path),
            },
        },
    }
    record_path = output_dir / "record.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source_path = args.source.resolve()
    output_dir = (
        args.output.resolve()
        if args.output
        else OUT / f"{source_path.stem}-prototype-v2"
    )
    record = process(source_path, output_dir)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
