"""Prototype topology-preserving P.3477 glyph alignment to canonical Kai.

Each human-accepted crop is cleaned, isotropically scaled, and translated so its
ink center of mass matches the same character rendered in canonical Kai. No
rotation, shear, non-uniform scaling, stroke synthesis, or source mutation is
allowed. The result is development input normalization, not documentary pixels.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from palimpsest.image_labeling import resolve_recorded_path, sha256

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out" / "glyph_alignment"
DATASET_PATH = HERE / "out" / "annotation_dataset.json"
ADAPT_PATH = HERE / "adapt.py"
BENCHMARK_PATH = HERE / "benchmark.py"
CANVAS = 128
MARGIN = 8
DEFAULT_LIMIT = 24


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ink_geometry(gray: np.ndarray) -> dict[str, object]:
    darkness = 255.0 - gray.astype(np.float32)
    support = darkness >= 18.0
    ys, xs = np.nonzero(support)
    if not len(xs):
        raise ValueError("Glyph has no ink support")
    weights = darkness[support]
    return {
        "bbox": (
            int(xs.min()),
            int(ys.min()),
            int(xs.max()) + 1,
            int(ys.max()) + 1,
        ),
        "centroid": (
            float(np.average(xs, weights=weights)),
            float(np.average(ys, weights=weights)),
        ),
        "ink_mass": float(weights.sum()),
    }


def safe_isotropic_scale(
    source_geometry: dict[str, object],
    target_geometry: dict[str, object],
    *,
    canvas: int,
    margin: int,
) -> float:
    sx0, sy0, sx1, sy1 = source_geometry["bbox"]
    tx0, ty0, tx1, ty1 = target_geometry["bbox"]
    source_width = sx1 - sx0
    source_height = sy1 - sy0
    target_width = tx1 - tx0
    target_height = ty1 - ty0
    fit = min(target_width / source_width, target_height / source_height)

    source_cx, source_cy = source_geometry["centroid"]
    target_cx, target_cy = target_geometry["centroid"]
    interpolation_margin = margin + 2
    extents = (
        (target_cx - interpolation_margin, source_cx - sx0),
        (canvas - interpolation_margin - target_cx, sx1 - 1 - source_cx),
        (target_cy - interpolation_margin, source_cy - sy0),
        (canvas - interpolation_margin - target_cy, sy1 - 1 - source_cy),
    )
    limits = [available / extent for available, extent in extents if extent > 0]
    return float(min(fit, *limits))


def align_to_kai(
    cleaned: np.ndarray,
    kai: np.ndarray,
    *,
    canvas: int = CANVAS,
    margin: int = MARGIN,
) -> tuple[np.ndarray, dict[str, object]]:
    source_geometry = ink_geometry(cleaned)
    target_geometry = ink_geometry(kai)
    scale = safe_isotropic_scale(
        source_geometry, target_geometry, canvas=canvas, margin=margin
    )
    source_cx, source_cy = source_geometry["centroid"]
    target_cx, target_cy = target_geometry["centroid"]
    matrix = np.array(
        [
            [scale, 0.0, target_cx - scale * source_cx],
            [0.0, scale, target_cy - scale * source_cy],
        ],
        dtype=np.float32,
    )
    aligned = cv2.warpAffine(
        cleaned,
        matrix,
        (canvas, canvas),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    aligned_geometry = ink_geometry(aligned)
    aligned_cx, aligned_cy = aligned_geometry["centroid"]
    ax0, ay0, ax1, ay1 = aligned_geometry["bbox"]
    record = {
        "scale": round(scale, 8),
        "translation": [round(float(matrix[0, 2]), 8), round(float(matrix[1, 2]), 8)],
        "source_bbox": list(source_geometry["bbox"]),
        "source_centroid": [round(source_cx, 6), round(source_cy, 6)],
        "target_bbox": list(target_geometry["bbox"]),
        "target_centroid": [round(target_cx, 6), round(target_cy, 6)],
        "aligned_bbox": list(aligned_geometry["bbox"]),
        "aligned_centroid": [round(aligned_cx, 6), round(aligned_cy, 6)],
        "centroid_error": round(
            float(np.hypot(aligned_cx - target_cx, aligned_cy - target_cy)), 6
        ),
        "margin_breached": bool(
            ax0 < margin
            or ay0 < margin
            or ax1 > canvas - margin
            or ay1 > canvas - margin
        ),
        "canvas_edge_touched": bool(
            ax0 <= 0 or ay0 <= 0 or ax1 >= canvas or ay1 >= canvas
        ),
        "transform": "translation_plus_isotropic_scale",
    }
    return aligned, record


def ink_alpha(gray: np.ndarray) -> np.ndarray:
    return (255.0 - gray.astype(np.float32)) / 255.0


def smooth_alpha(alpha: np.ndarray) -> np.ndarray:
    """Smooth subpixel edges while preserving total continuous ink mass."""

    height, width = alpha.shape
    enlarged = cv2.resize(alpha, (width * 4, height * 4), interpolation=cv2.INTER_CUBIC)
    blurred = cv2.GaussianBlur(enlarged, (0, 0), sigmaX=1.2)
    smoothed = cv2.resize(blurred, (width, height), interpolation=cv2.INTER_AREA)
    target_mass = float(alpha.sum())
    current_mass = float(smoothed.sum())
    if current_mass > 0:
        smoothed *= target_mass / current_mass
    return np.clip(smoothed, 0, 1)


def alpha_image(alpha: np.ndarray) -> np.ndarray:
    return np.uint8(np.clip(255.0 * (1.0 - alpha), 0, 255))


def topology(gray: np.ndarray) -> dict[str, int]:
    mask = (gray < 128).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    components = int(
        sum(int(stats[label, cv2.CC_STAT_AREA]) >= 3 for label in range(1, count))
    )
    contours, hierarchy = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    holes = 0
    if hierarchy is not None:
        holes = int(
            sum(
                hierarchy[0][index][3] >= 0 and cv2.contourArea(contour) >= 3
                for index, contour in enumerate(contours)
            )
        )
    return {"components": components, "holes": holes}


def refine_ink(aligned: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Return edge-smoothed and explicitly one-pixel-repaired alternatives."""

    original_alpha = ink_alpha(aligned)
    smoothed_alpha = smooth_alpha(original_alpha)
    smoothed = alpha_image(smoothed_alpha)

    original_mask = original_alpha >= 0.5
    closed = cv2.morphologyEx(
        original_mask.astype(np.uint8),
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    ).astype(bool)
    added = closed & ~original_mask
    repaired_alpha = original_alpha.copy()
    repaired_alpha[added] = np.maximum(repaired_alpha[added], 0.65)
    repaired = alpha_image(smooth_alpha(repaired_alpha))

    original_mass = float(original_alpha.sum())
    smooth_mass = float(ink_alpha(smoothed).sum())
    repaired_mass = float(ink_alpha(repaired).sum())
    diagnostics = {
        "original_ink_mass": round(original_mass, 6),
        "smoothed_ink_mass": round(smooth_mass, 6),
        "smoothed_mass_change_fraction": round(
            (smooth_mass - original_mass) / original_mass, 8
        ),
        "repair_added_pixels": int(added.sum()),
        "repair_added_fraction": round(float(added.mean()), 8),
        "repaired_mass_change_fraction": round(
            (repaired_mass - original_mass) / original_mass, 8
        ),
        "topology": {
            "gravity": topology(aligned),
            "smoothed": topology(smoothed),
            "micro_repair": topology(repaired),
        },
    }
    diagnostics["smoothed_topology_changed"] = (
        diagnostics["topology"]["smoothed"] != diagnostics["topology"]["gravity"]
    )
    diagnostics["repair_topology_changed"] = (
        diagnostics["topology"]["micro_repair"] != diagnostics["topology"]["gravity"]
    )
    return smoothed, repaired, diagnostics


def overlay(aligned: np.ndarray, kai: np.ndarray) -> Image.Image:
    hand = aligned < 225
    canonical = kai < 225
    result = np.full((CANVAS, CANVAS, 3), 255, dtype=np.uint8)
    result[hand] = (31, 149, 194)
    result[canonical] = (213, 75, 71)
    result[hand & canonical] = (26, 24, 22)
    return Image.fromarray(result, mode="RGB")


def render_sheet(rows: list[dict], path: Path) -> None:
    card_width = 756
    card_height = 190
    cards_per_row = 2
    card_rows = (len(rows) + cards_per_row - 1) // cards_per_row
    sheet = Image.new(
        "RGB", (cards_per_row * card_width, card_rows * card_height), "#f4f1eb"
    )
    draw = ImageDraw.Draw(sheet)
    cjk_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = (
        ImageFont.truetype(str(cjk_path), 27)
        if cjk_path.exists()
        else ImageFont.load_default()
    )
    small = (
        ImageFont.truetype(str(cjk_path), 13)
        if cjk_path.exists()
        else ImageFont.load_default()
    )
    labels = ("GRAVITY", "SMOOTH", "MICRO-REPAIR", "KAI", "REPAIR / KAI")
    for index, row in enumerate(rows):
        card_x = (index % cards_per_row) * card_width
        card_y = (index // cards_per_row) * card_height
        draw.text(
            (card_x + 12, card_y + 9), row["character"], fill="#181512", font=font
        )
        diagnostics = row["refinement"]
        draw.text(
            (card_x + 54, card_y + 17),
            (
                f"scale {row['alignment']['scale']:.3f} · "
                f"repair +{diagnostics['repair_added_pixels']} px · "
                f"topology {'changed' if diagnostics['repair_topology_changed'] else 'same'}"
            ),
            fill="#756d64",
            font=small,
        )
        panels = (
            Image.fromarray(row["aligned"], mode="L").convert("RGB"),
            Image.fromarray(row["smoothed"], mode="L").convert("RGB"),
            Image.fromarray(row["repaired"], mode="L").convert("RGB"),
            Image.fromarray(row["kai"], mode="L").convert("RGB"),
            overlay(row["repaired"], row["kai"]),
        )
        for panel_index, (label, panel) in enumerate(zip(labels, panels, strict=True)):
            x = card_x + 12 + panel_index * 146
            sheet.paste(panel, (x, card_y + 48))
            draw.text((x, card_y + 177), label, fill="#514a43", font=small)
        draw.line(
            (
                card_x,
                card_y + card_height - 1,
                card_x + card_width,
                card_y + card_height - 1,
            ),
            fill="#d4cec5",
        )
    sheet.save(path)


def build_rows(limit: int) -> tuple[list[dict], dict]:
    annotation = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if (
        annotation.get("kind") != "human_image_annotation_dataset"
        or annotation.get("queue_summaries", {}).get("writer_specimen", {}).get("ready")
        is not True
    ):
        raise RuntimeError("Writer-specimen annotations are not ready")
    project_path = resolve_recorded_path(
        annotation["project_path"], DATASET_PATH.parent
    )
    if sha256(project_path) != annotation["project_sha256"]:
        raise RuntimeError("Annotation project fingerprint mismatch")

    adapt = load_module("glyph_alignment_adapt", ADAPT_PATH)
    benchmark = load_module("glyph_alignment_benchmark", BENCHMARK_PATH)
    candidate = adapt.load_candidate()
    accepted = benchmark.gold_records(annotation, "writer_specimen")
    selected = benchmark.specimen_order(accepted)[:limit]
    rows = []
    records = []
    for record in selected:
        frozen = benchmark.frozen_record(record)
        crop_path = resolve_recorded_path(frozen["crop_path"], ROOT)
        native = np.asarray(Image.open(crop_path).convert("L"))
        cleaned_native = adapt.clean_writer_image(native)
        current = adapt.clean_writer_image(adapt.load_gray(crop_path, CANVAS))
        kai = candidate.render_character(frozen["character"], candidate.SOURCE_FONT)
        aligned, alignment = align_to_kai(cleaned_native, kai)
        smoothed, repaired, refinement = refine_ink(aligned)
        rows.append(
            {
                "character": frozen["character"],
                "current": current,
                "aligned": aligned,
                "smoothed": smoothed,
                "repaired": repaired,
                "kai": kai,
                "alignment": alignment,
                "refinement": refinement,
            }
        )
        records.append(
            {
                "item_id": frozen["crop_id"],
                "character": frozen["character"],
                "crop_path": frozen["crop_path"],
                "crop_sha256": frozen["crop_sha256"],
                "alignment": alignment,
                "refinement": refinement,
            }
        )
    return rows, {
        "annotation_dataset_sha256": sha256(DATASET_PATH),
        "annotation_project_sha256": sha256(project_path),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=OUT / "prototype-v2")
    args = parser.parse_args()
    if args.limit <= 0:
        raise ValueError("limit must be positive")
    output_dir = args.output.resolve()
    if output_dir.exists():
        raise FileExistsError(
            f"Refusing to overwrite alignment prototype: {output_dir}"
        )

    rows, provenance = build_rows(args.limit)
    output_dir.mkdir(parents=True)
    comparison_path = output_dir / "comparison.png"
    render_sheet(rows, comparison_path)
    alignments = [record["alignment"] for record in provenance["records"]]
    record = {
        "schema_version": 1,
        "kind": "kai_gravity_alignment_development_prototype",
        "source_was_modified": False,
        "human_evidence_was_modified": False,
        "generated_pixels_are_documentary_evidence": False,
        "invariants": {
            "translation_only_after_isotropic_scaling": True,
            "rotation": False,
            "shear": False,
            "non_uniform_scaling": False,
            "stroke_synthesis": False,
            "canvas": CANVAS,
            "margin": MARGIN,
        },
        "provenance": provenance,
        "summary": {
            "records": len(alignments),
            "mean_centroid_error": round(
                float(np.mean([record["centroid_error"] for record in alignments])), 6
            ),
            "maximum_centroid_error": round(
                float(max(record["centroid_error"] for record in alignments)), 6
            ),
            "margin_breached_records": sum(
                record["margin_breached"] for record in alignments
            ),
            "canvas_edge_touched_records": sum(
                record["canvas_edge_touched"] for record in alignments
            ),
            "smoothed_topology_changed_records": sum(
                record["refinement"]["smoothed_topology_changed"]
                for record in provenance["records"]
            ),
            "repair_topology_changed_records": sum(
                record["refinement"]["repair_topology_changed"]
                for record in provenance["records"]
            ),
        },
        "comparison_path": comparison_path.relative_to(ROOT).as_posix(),
    }
    record_path = output_dir / "record.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    record["comparison_sha256"] = sha256(comparison_path)
    record["record_path"] = record_path.relative_to(ROOT).as_posix()
    print(json.dumps(record["summary"], indent=2))
    print(f"comparison: {comparison_path}")
    print(f"record: {record_path}")


if __name__ == "__main__":
    main()
