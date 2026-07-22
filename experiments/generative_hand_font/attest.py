"""Build and serve the P.3477 manifest-driven image annotation project."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from palimpsest.image_labeling import (
    PROJECT_SCHEMA_VERSION,
    portable_path,
    serve_project,
    sha256,
)


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
SOURCE = ROOT / "experiments" / "scribe_template_retrieval" / "out"
PREPARE_PATH = ROOT / "experiments" / "scribe_template_retrieval" / "prepare.py"
SOURCE_MANIFEST_PATH = SOURCE / "manifest.json"
PROPOSAL_PATH = OUT / "crop_proposals_v2.json"
PROJECT_PATH = OUT / "annotation_project.json"
FIRST_PASS_PATH = OUT / "luna_first_pass.json"
EVENT_PATH = OUT / "annotation_events.jsonl"
DATASET_PATH = OUT / "annotation_dataset.json"
ACCEPTED_IMAGE_DIR = OUT / "annotation_images"
PAGE_ROLES = {
    "page_0000": "writer_specimen",
    "page_0001": "held_out_evaluation",
}
QUEUE_MINIMUMS = {
    "writer_specimen": 24,
    "held_out_evaluation": 16,
}
MAX_PROPOSALS_PER_PAGE = 220


def load_prepare():
    spec = importlib.util.spec_from_file_location(
        "font_gold_crop_prepare", PREPARE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {PREPARE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def clamp_bbox(
    bbox: list[int] | tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> list[int]:
    x, y, width, height = (int(value) for value in bbox)
    x0 = min(max(0, x), image_width - 1)
    y0 = min(max(0, y), image_height - 1)
    x1 = min(max(x0 + 1, x + width), image_width)
    y1 = min(max(y0 + 1, y + height), image_height)
    return [x0, y0, x1 - x0, y1 - y0]


def fuse_fragment_cells(cells: list, glyph_height: float) -> list[list]:
    """Merge vertically adjacent fragments whose centers cannot be separate glyphs."""

    ordered = sorted(cells, key=lambda cell: (cell.y0 + cell.y1, cell.x0))
    groups: list[list] = []
    for cell in ordered:
        center = (cell.y0 + cell.y1) / 2
        if groups:
            prior = groups[-1]
            prior_center = np.mean([(item.y0 + item.y1) / 2 for item in prior])
            if center - prior_center < glyph_height * 0.48:
                prior.append(cell)
                continue
        groups.append([cell])
    return groups


def group_bbox(group: list) -> list[int]:
    x0 = min(cell.x0 for cell in group)
    y0 = min(cell.y0 for cell in group)
    x1 = max(cell.x1 for cell in group)
    y1 = max(cell.y1 for cell in group)
    return [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]


def refine_bbox(
    image: np.ndarray,
    initial_bbox: list[int],
    glyph_height: float,
) -> list[int]:
    """Recover detached stroke components near a detected glyph without binarizing output."""

    image_height, image_width = image.shape[:2]
    x, y, width, height = initial_bbox
    margin = max(5, round(glyph_height * 0.20))
    search = clamp_bbox(
        [x - margin, y - margin, width + 2 * margin, height + 2 * margin],
        image_width,
        image_height,
    )
    sx, sy, sw, sh = search
    gray = cv2.cvtColor(image[sy : sy + sh, sx : sx + sw], cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(ink, 8)
    core_margin = glyph_height * 0.12
    selected = []
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < 3:
            continue
        center_x = sx + float(centroids[index, 0])
        center_y = sy + float(centroids[index, 1])
        if (
            x - core_margin <= center_x <= x + width + core_margin
            and y - core_margin <= center_y <= y + height + core_margin
        ):
            component_x = sx + int(stats[index, cv2.CC_STAT_LEFT])
            component_y = sy + int(stats[index, cv2.CC_STAT_TOP])
            component_w = int(stats[index, cv2.CC_STAT_WIDTH])
            component_h = int(stats[index, cv2.CC_STAT_HEIGHT])
            selected.append([component_x, component_y, component_w, component_h])
    if not selected:
        return clamp_bbox(initial_bbox, image_width, image_height)
    x0 = min(item[0] for item in selected)
    y0 = min(item[1] for item in selected)
    x1 = max(item[0] + item[2] for item in selected)
    y1 = max(item[1] + item[3] for item in selected)
    pad = max(3, round(glyph_height * 0.08))
    return clamp_bbox(
        [x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad],
        image_width,
        image_height,
    )


def raw_crop(image: np.ndarray, bbox: list[int]) -> np.ndarray:
    x, y, width, height = bbox
    crop = image[y : y + height, x : x + width]
    if crop.size == 0:
        raise ValueError(f"Empty crop for bbox {bbox}")
    return crop


def proposal_score(
    image: np.ndarray,
    bbox: list[int],
    glyph_height: float,
) -> tuple[float, dict] | None:
    crop = raw_crop(image, bbox)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink_fraction = float(np.mean(ink > 0))
    x, y, width, height = bbox
    del x, y
    aspect_ratio = width / max(height, 1)
    height_ratio = height / max(glyph_height, 1.0)
    edge = max(1, round(min(crop.shape[:2]) * 0.04))
    edge_mask = np.zeros_like(ink, dtype=bool)
    edge_mask[:edge, :] = True
    edge_mask[-edge:, :] = True
    edge_mask[:, :edge] = True
    edge_mask[:, -edge:] = True
    edge_ink = float(np.sum((ink > 0) & edge_mask) / max(1, np.sum(ink > 0)))
    if not (
        0.035 <= ink_fraction <= 0.62
        and 0.30 <= aspect_ratio <= 1.65
        and 0.48 <= height_ratio <= 1.65
        and edge_ink <= 0.28
    ):
        return None
    score = 1.0
    score -= min(abs(ink_fraction - 0.20) / 0.35, 1.0) * 0.25
    score -= min(abs(aspect_ratio - 0.82) / 0.9, 1.0) * 0.20
    score -= min(abs(height_ratio - 1.0) / 0.8, 1.0) * 0.20
    score -= min(edge_ink / 0.28, 1.0) * 0.35
    diagnostics = {
        "ink_fraction": round(ink_fraction, 6),
        "edge_ink_fraction": round(edge_ink, 6),
        "aspect_ratio": round(aspect_ratio, 6),
        "height_ratio": round(height_ratio, 6),
    }
    return round(score, 6), diagnostics


def intersection_over_union(first: list[int], second: list[int]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0


def silver_hypothesis(
    manifest_records: list[dict],
    page_id: str,
    column_index: int,
    bbox: list[int],
) -> dict | None:
    candidates = [
        record
        for record in manifest_records
        if record["page_id"] == page_id
        and int(record["column_index"]) == column_index
        and int(record["consumed_cells"]) == 1
    ]
    ranked = sorted(
        (
            (intersection_over_union(bbox, list(record["bbox"])), record)
            for record in candidates
        ),
        key=lambda item: (-item[0], item[1]["crop_id"]),
    )
    if not ranked or ranked[0][0] < 0.30:
        return None
    return ranked[0][1]


def build_proposals() -> dict:
    prepare = load_prepare()
    manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    records = []
    pages = {}
    for page_id, role in PAGE_ROLES.items():
        page_path = (
            ROOT
            / "library"
            / "gallica_pelliot_chinois_3477"
            / "page_image_clean"
            / f"{page_id}.jpg"
        )
        image = cv2.imread(str(page_path))
        if image is None:
            raise FileNotFoundError(page_path)
        columns, glyph_height = prepare.detect_columns(image)
        candidates = []
        for column_index, column in enumerate(columns):
            groups = fuse_fragment_cells(column, glyph_height)
            for slot_index, group in enumerate(groups):
                detected_bbox = group_bbox(group)
                bbox = refine_bbox(image, detected_bbox, glyph_height)
                scored = proposal_score(image, bbox, glyph_height)
                if scored is None:
                    continue
                score, diagnostics = scored
                proposal_id = f"{page_id}_cv2_c{column_index:02d}_s{slot_index:02d}"
                hypothesis = silver_hypothesis(
                    manifest["records"], page_id, column_index, bbox
                )
                candidates.append(
                    {
                        "proposal_id": proposal_id,
                        "page_id": page_id,
                        "role": role,
                        "column_index": column_index,
                        "slot_index": slot_index,
                        "detected_bbox": detected_bbox,
                        "initial_bbox": bbox,
                        "cv_score": score,
                        "cv_diagnostics": diagnostics,
                        "silver_hypothesis": (
                            hypothesis["claimed_char"] if hypothesis else None
                        ),
                        "silver_source_crop_id": (
                            hypothesis["crop_id"] if hypothesis else None
                        ),
                        "silver_label_trusted": False,
                    }
                )
        candidates.sort(
            key=lambda item: (
                -float(item["cv_score"]),
                int(item["column_index"]),
                int(item["slot_index"]),
            )
        )
        selected = candidates[:MAX_PROPOSALS_PER_PAGE]
        records.extend(selected)
        pages[page_id] = {
            "role": role,
            "image_path": page_path.relative_to(ROOT).as_posix(),
            "image_sha256": sha256(page_path),
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "detected_columns": len(columns),
            "detected_slots": sum(
                len(fuse_fragment_cells(column, glyph_height)) for column in columns
            ),
            "eligible_proposals": len(candidates),
            "queued_proposals": len(selected),
            "glyph_height": float(glyph_height),
        }
    record = {
        "schema_version": 2,
        "experiment": "generative-hand-font-v1",
        "purpose": "continuous human crop refinement and Unicode attestation",
        "method": (
            "label-independent CV columns; fragment fusion; detached-stroke bbox "
            "recovery; lossless native-resolution source crops"
        ),
        "source_manifest_path": SOURCE_MANIFEST_PATH.relative_to(ROOT).as_posix(),
        "source_manifest_sha256": sha256(SOURCE_MANIFEST_PATH),
        "silver_hypotheses_are_trusted": False,
        "pages": pages,
        "records": records,
    }
    PROPOSAL_PATH.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def load_or_build_proposals(rebuild: bool = False) -> dict:
    if rebuild:
        if EVENT_PATH.exists():
            raise RuntimeError("Cannot rebuild proposals after annotations exist")
        return build_proposals()
    if PROPOSAL_PATH.exists():
        return json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    return build_proposals()


def build_annotation_project(
    proposals: dict,
    luna_predictions: dict[str, dict] | None = None,
    luna_record: dict | None = None,
) -> dict:
    proposal_sha256 = sha256(PROPOSAL_PATH)
    items = []
    for proposal in proposals["records"]:
        page = proposals["pages"][proposal["page_id"]]
        first_pass = None
        if luna_predictions is not None:
            prediction = luna_predictions[proposal["proposal_id"]]
            if prediction["label"] is not None:
                first_pass = {
                    "label": prediction["label"],
                    "source": prediction["source"],
                    "confidence": prediction["confidence"],
                    "trusted": False,
                }
        elif proposal.get("silver_hypothesis"):
            first_pass = {
                "label": proposal["silver_hypothesis"],
                "source": "Automated sequence-alignment first pass",
                "trusted": False,
            }
        items.append(
            {
                "id": proposal["proposal_id"],
                "queue": proposal["role"],
                "image_path": page["image_path"],
                "image_sha256": page["image_sha256"],
                "image_width": page["width"],
                "image_height": page["height"],
                "crop_mode": "required",
                "initial_bbox": proposal["initial_bbox"],
                "first_pass": first_pass,
                "metadata": {
                    "experiment": "generative-hand-font-v1",
                    "proposal_id": proposal["proposal_id"],
                    "page_id": proposal["page_id"],
                    "role": proposal["role"],
                    "column_index": proposal["column_index"],
                    "slot_index": proposal["slot_index"],
                    "detected_bbox": proposal["detected_bbox"],
                    "cv_score": proposal["cv_score"],
                    "cv_diagnostics": proposal["cv_diagnostics"],
                    "silver_source_crop_id": proposal.get("silver_source_crop_id"),
                },
            }
        )
    project = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "id": "p3477-generative-hand-font-crops",
        "title": "P.3477 Image Annotation Lab",
        "instructions": (
            "Review the automated crop and first-pass character. Keep either "
            "when correct; drag the box or type a new character to override it."
        ),
        "asset_root": portable_path(ROOT, PROJECT_PATH.parent),
        "crop_mode": "required",
        "label": {
            "name": "Exact traditional-Chinese character",
            "required": True,
            "max_length": 1,
            "pattern": "^[\u3400-\u9fff]$",
            "placeholder": "Type one Chinese character",
        },
        "queues": [
            {
                "id": "writer_specimen",
                "label": "Training specimens",
                "minimum_distinct_labels": QUEUE_MINIMUMS["writer_specimen"],
            },
            {
                "id": "held_out_evaluation",
                "label": "Held-out evaluation",
                "minimum_distinct_labels": QUEUE_MINIMUMS["held_out_evaluation"],
            },
        ],
        "skip_reasons": [
            {
                "id": "unclear",
                "label": "Character is unclear — skip",
            },
            {
                "id": "bad_box",
                "label": "Image cannot be cropped cleanly — skip",
            },
        ],
        "metadata": {
            "experiment": "generative-hand-font-v1",
            "proposal_path": PROPOSAL_PATH.relative_to(ROOT).as_posix(),
            "proposal_sha256": proposal_sha256,
            "source_manifest_path": proposals["source_manifest_path"],
            "source_manifest_sha256": proposals["source_manifest_sha256"],
            "first_pass_is_training_truth": False,
            "first_pass_source": (
                "luna_agent_visual_first_pass"
                if luna_record is not None
                else "automated_sequence_alignment"
            ),
        },
        "items": items,
    }
    if luna_record is not None:
        project["metadata"]["first_pass_sidecar_path"] = FIRST_PASS_PATH.relative_to(
            ROOT
        ).as_posix()
        project["metadata"]["first_pass_sidecar_sha256"] = sha256(FIRST_PASS_PATH)
        project["metadata"]["first_pass_source_project_sha256"] = luna_record[
            "source_project_sha256"
        ]
        project["metadata"]["first_pass_agent"] = luna_record["agent"]
    PROJECT_PATH.write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return project


def load_luna_first_pass(project: dict) -> tuple[dict[str, dict], dict]:
    record = json.loads(FIRST_PASS_PATH.read_text(encoding="utf-8"))
    if record.get("schema_version") != 1 or record.get("project_id") != project["id"]:
        raise ValueError("Luna first pass has the wrong project contract")
    if record.get("source_project_sha256") != sha256(PROJECT_PATH):
        raise ValueError("Luna first pass targets a different annotation project")
    predictions = record.get("predictions")
    if not isinstance(predictions, list):
        raise ValueError("Luna first pass predictions must be a list")
    expected_ids = [item["id"] for item in project["items"]]
    actual_ids = [prediction.get("item_id") for prediction in predictions]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Luna predictions must match every project item in order")
    by_id = {}
    for prediction in predictions:
        label = prediction.get("label")
        if label is not None and not (
            isinstance(label, str) and len(label) == 1 and "\u3400" <= label <= "\u9fff"
        ):
            raise ValueError("Luna labels must be one CJK character or null")
        confidence = prediction.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Luna confidence must be between zero and one")
        if prediction.get("source") != "luna_agent_visual_first_pass":
            raise ValueError("Luna prediction source is invalid")
        by_id[prediction["item_id"]] = prediction
    return by_id, record


def prepare_annotation_project(proposals: dict) -> dict:
    proposal_sha256 = sha256(PROPOSAL_PATH)
    if EVENT_PATH.exists() and not PROJECT_PATH.exists():
        raise FileNotFoundError(
            "Annotation events exist without their immutable project manifest"
        )

    if PROJECT_PATH.exists():
        project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
        if project.get("metadata", {}).get("proposal_sha256") != proposal_sha256:
            if EVENT_PATH.exists():
                raise ValueError(
                    "Annotation project points to a different proposal set"
                )
            project = build_annotation_project(proposals)
    else:
        project = build_annotation_project(proposals)

    if EVENT_PATH.exists() or not FIRST_PASS_PATH.exists():
        return project
    sidecar_sha256 = sha256(FIRST_PASS_PATH)
    if project.get("metadata", {}).get("first_pass_sidecar_sha256") == sidecar_sha256:
        return project
    predictions, luna_record = load_luna_first_pass(project)
    return build_annotation_project(proposals, predictions, luna_record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3478)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--rebuild-proposals", action="store_true")
    args = parser.parse_args()

    proposals = load_or_build_proposals(args.rebuild_proposals)
    prepare_annotation_project(proposals)
    serve_project(
        PROJECT_PATH,
        EVENT_PATH,
        DATASET_PATH,
        ACCEPTED_IMAGE_DIR,
        args.host,
        args.port,
        not args.no_open,
    )


if __name__ == "__main__":
    main()
