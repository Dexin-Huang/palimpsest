from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from palimpsest.reconstruct.artifacts import BlobRefinementArtifact

from .pipeline import (
    _bbox_intersection_area,
    _bbox_norm_from_px,
    _bbox_px,
    _expand_bbox_px,
    _load_layout_probe,
    _utc_now,
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
        owner_mask = labels == label_idx
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
