#!/usr/bin/env python3
"""Classical CV probe for manuscript blob and heatmap generation.

This is intentionally simple and deterministic:
- local contrast enhancement
- Sauvola-style thresholding for an ink mask
- local ink-density heatmap
- anisotropic morphology for line-ish and region-ish blobs

Outputs are written into `.tmp/cv_blob_probe/<image_stem>/`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def default_image_path() -> Path | None:
    preferred = PROJECT_ROOT / "library" / "vatican_borg_cin_361" / "images" / "f004r.jpg"
    if preferred.exists():
        return preferred
    for candidate in sorted((PROJECT_ROOT / "library").glob("*/images/*.jpg")):
        return candidate
    return None


def odd(value: int) -> int:
    value = max(3, int(value))
    return value if value % 2 else value + 1


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def normalize_u8(image: np.ndarray) -> np.ndarray:
    return cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def sauvola_ink_mask(gray: np.ndarray, window_size: int, k: float) -> np.ndarray:
    image = gray.astype(np.float64)
    mean = cv2.blur(image, (window_size, window_size))
    mean_sq = cv2.blur(image**2, (window_size, window_size))
    std = np.sqrt(np.maximum(mean_sq - mean**2, 0))
    threshold = mean * (1 + k * (std / 128.0 - 1))
    return (gray < threshold).astype(np.uint8) * 255


def remove_small_components(mask: np.ndarray, min_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= min_area:
            cleaned[labels == label] = 255
    return cleaned


def detect_footer_band_start(
    gray: np.ndarray,
    *,
    pixel_threshold: int = 225,
    coverage_threshold: float = 0.92,
    search_start_ratio: float = 0.75,
    min_run: int = 8,
) -> int | None:
    coverage = (gray < pixel_threshold).mean(axis=1).astype(np.float32)
    smoothed = cv2.blur(coverage.reshape(-1, 1), (1, 9)).ravel()
    start = int(gray.shape[0] * search_start_ratio)
    active = smoothed[start:] >= coverage_threshold

    run_start: int | None = None
    run_length = 0
    for offset, is_active in enumerate(active):
        if is_active:
            if run_start is None:
                run_start = start + offset
                run_length = 1
            else:
                run_length += 1
            if run_length >= min_run:
                return run_start
        else:
            run_start = None
            run_length = 0
    return None


def remove_border_components(mask: np.ndarray, min_area: int, border: int = 5) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    height, width = mask.shape[:2]
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        box_width = int(stats[label, cv2.CC_STAT_WIDTH])
        box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        touches_border = (
            left <= border
            or top <= border
            or left + box_width >= width - border
            or top + box_height >= height - border
        )
        if touches_border and area >= min_area:
            continue
        cleaned[labels == label] = 255
    return cleaned


def merge_mask(
    mask: np.ndarray,
    kernel_width: int,
    kernel_height: int,
    min_area: int,
    *,
    max_area: int | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[np.ndarray, list[dict[str, int]]]:
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (odd(kernel_width), odd(kernel_height))
    )
    merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    merged = cv2.dilate(merged, kernel, iterations=1)
    merged = remove_small_components(merged, min_area=min_area)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    boxes: list[dict[str, int]] = []
    filtered = np.zeros_like(mask)
    for label in range(1, count):
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        if max_area is not None and area > max_area:
            continue
        if max_width is not None and width > max_width:
            continue
        if max_height is not None and height > max_height:
            continue
        filtered[labels == label] = 255
        boxes.append(
            {
                "x": left,
                "y": top,
                "width": width,
                "height": height,
                "area": area,
            }
        )
    boxes.sort(key=lambda box: (box["y"], box["x"]))
    return filtered, boxes


def draw_boxes(base_bgr: np.ndarray, boxes: list[dict[str, int]], color: tuple[int, int, int]) -> np.ndarray:
    overlay = base_bgr.copy()
    for index, box in enumerate(boxes, start=1):
        x0 = box["x"]
        y0 = box["y"]
        x1 = x0 + box["width"]
        y1 = y0 + box["height"]
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2)
        cv2.putText(
            overlay,
            str(index),
            (x0, max(18, y0 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return overlay


def write_preview_grid(output_path: Path, panels: list[tuple[str, np.ndarray]]) -> None:
    target_width = 900
    target_height = 700
    rendered: list[np.ndarray] = []
    for title, image in panels:
        if image.ndim == 2:
            canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            canvas = image.copy()
        scale = min(target_width / canvas.shape[1], target_height / canvas.shape[0])
        resized = cv2.resize(
            canvas,
            (max(1, int(canvas.shape[1] * scale)), max(1, int(canvas.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        tile = np.full((target_height, target_width, 3), 255, dtype=np.uint8)
        y_offset = (target_height - resized.shape[0]) // 2
        x_offset = (target_width - resized.shape[1]) // 2
        tile[y_offset : y_offset + resized.shape[0], x_offset : x_offset + resized.shape[1]] = resized
        cv2.putText(
            tile,
            title,
            (20, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
        rendered.append(tile)

    first_row = cv2.hconcat(rendered[:3])
    second_row = cv2.hconcat(rendered[3:])
    grid = cv2.vconcat([first_row, second_row])
    cv2.imwrite(str(output_path), grid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate classical CV blobs and heatmaps.")
    parser.add_argument("image", nargs="?", help="Input manuscript image")
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: .tmp/cv_blob_probe/<image_stem>)",
    )
    parser.add_argument("--window", type=int, default=41, help="Sauvola window size")
    parser.add_argument("--k", type=float, default=0.22, help="Sauvola k")
    parser.add_argument(
        "--suppress-vatican-print",
        action="store_true",
        help="Suppress the footer strip and faint diagonal watermark",
    )
    parser.add_argument(
        "--dark-ink-threshold",
        type=int,
        default=210,
        help="Raw grayscale threshold used when suppressing faint print",
    )
    args = parser.parse_args()

    image_path = Path(args.image) if args.image else default_image_path()
    if image_path is None:
        raise SystemExit("No manuscript image found under library/*/images")

    if not image_path.is_absolute():
        image_path = (PROJECT_ROOT / image_path).resolve()
    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else PROJECT_ROOT / ".tmp" / "cv_blob_probe" / image_path.stem
    )
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    ensure_dir(output_dir)

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        raise SystemExit(f"Could not load image: {image_path}")

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    median = cv2.medianBlur(clahe, 3)

    base_ink_mask = sauvola_ink_mask(median, window_size=odd(args.window), k=args.k)
    base_ink_mask = remove_small_components(
        base_ink_mask,
        min_area=max(8, (gray.shape[0] * gray.shape[1]) // 60000),
    )
    ink_mask = base_ink_mask.copy()
    dark_ink_gate = None
    footer_mask = np.zeros_like(ink_mask)
    footer_start = None

    if args.suppress_vatican_print:
        dark_ink_gate = (gray < args.dark_ink_threshold).astype(np.uint8) * 255
        ink_mask = cv2.bitwise_and(ink_mask, dark_ink_gate)
        footer_start = detect_footer_band_start(gray)
        if footer_start is not None:
            footer_mask[footer_start:, :] = 255
            ink_mask = cv2.bitwise_and(ink_mask, cv2.bitwise_not(footer_mask))
        ink_mask = remove_small_components(
            ink_mask,
            min_area=max(8, (gray.shape[0] * gray.shape[1]) // 60000),
        )

    density = cv2.GaussianBlur(
        ink_mask.astype(np.float32) / 255.0,
        (odd(gray.shape[1] // 35), odd(gray.shape[0] // 35)),
        0,
    )
    density_u8 = normalize_u8(density)
    density_heatmap = cv2.applyColorMap(density_u8, cv2.COLORMAP_TURBO)
    nonzero_density = density_u8[density_u8 > 0]
    density_threshold = (
        int(np.percentile(nonzero_density, 82)) if nonzero_density.size else 96
    )
    _, density_seed_mask = cv2.threshold(
        density_u8, density_threshold, 255, cv2.THRESH_BINARY
    )

    line_mask, line_boxes = merge_mask(
        ink_mask,
        kernel_width=max(31, gray.shape[1] // 45),
        kernel_height=max(5, gray.shape[0] // 260),
        min_area=max(120, (gray.shape[0] * gray.shape[1]) // 9000),
        max_area=(gray.shape[0] * gray.shape[1]) // 8,
        max_height=gray.shape[0] // 7,
    )
    region_seed_mask = remove_border_components(
        line_mask,
        min_area=max(4000, (gray.shape[0] * gray.shape[1]) // 60),
    )
    region_mask, region_boxes = merge_mask(
        region_seed_mask,
        kernel_width=max(81, gray.shape[1] // 18),
        kernel_height=max(21, gray.shape[0] // 38),
        min_area=max(1200, (gray.shape[0] * gray.shape[1]) // 1000),
        max_area=(gray.shape[0] * gray.shape[1]) // 2,
    )

    line_overlay = draw_boxes(image_bgr, line_boxes, color=(0, 180, 0))
    region_overlay = draw_boxes(image_bgr, region_boxes, color=(0, 0, 220))

    cv2.imwrite(str(output_dir / "gray.png"), gray)
    cv2.imwrite(str(output_dir / "clahe.png"), clahe)
    cv2.imwrite(str(output_dir / "base_ink_mask.png"), base_ink_mask)
    cv2.imwrite(str(output_dir / "ink_mask.png"), ink_mask)
    if dark_ink_gate is not None:
        cv2.imwrite(str(output_dir / "dark_ink_gate.png"), dark_ink_gate)
    if footer_start is not None:
        cv2.imwrite(str(output_dir / "footer_mask.png"), footer_mask)
    cv2.imwrite(str(output_dir / "ink_density.png"), density_u8)
    cv2.imwrite(str(output_dir / "ink_density_heatmap.png"), density_heatmap)
    cv2.imwrite(str(output_dir / "density_seed_mask.png"), density_seed_mask)
    cv2.imwrite(str(output_dir / "line_mask.png"), line_mask)
    cv2.imwrite(str(output_dir / "region_seed_mask.png"), region_seed_mask)
    cv2.imwrite(str(output_dir / "line_blobs_overlay.png"), line_overlay)
    cv2.imwrite(str(output_dir / "region_mask.png"), region_mask)
    cv2.imwrite(str(output_dir / "region_blobs_overlay.png"), region_overlay)
    write_preview_grid(
        output_dir / "preview_grid.png",
        [
            ("gray", gray),
            ("clahe", clahe),
            ("ink mask", ink_mask),
            ("density heatmap", density_heatmap),
            ("line blobs", line_overlay),
            ("region blobs", region_overlay),
        ],
    )

    summary = {
        "image_path": str(image_path),
        "output_dir": str(output_dir),
        "image_size": {"width": int(gray.shape[1]), "height": int(gray.shape[0])},
        "parameters": {
            "sauvola_window": odd(args.window),
            "sauvola_k": args.k,
            "suppress_vatican_print": args.suppress_vatican_print,
            "dark_ink_threshold": args.dark_ink_threshold,
            "line_kernel": {
                "width": max(31, gray.shape[1] // 45),
                "height": max(5, gray.shape[0] // 260),
            },
            "region_kernel": {
                "width": max(81, gray.shape[1] // 18),
                "height": max(21, gray.shape[0] // 38),
            },
            "density_threshold": density_threshold,
        },
        "suppression": {
            "footer_start_row": footer_start,
        },
        "counts": {
            "line_blobs": len(line_boxes),
            "region_blobs": len(region_boxes),
        },
        "line_blobs": line_boxes,
        "region_blobs": region_boxes,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print(f"wrote={output_dir}")
    print(f"line_blobs={len(line_boxes)}")
    print(f"region_blobs={len(region_boxes)}")


if __name__ == "__main__":
    main()
