"""Lift detected character ink onto a white page while preserving geometry."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(record)
    return records


def clean_character(
    image: np.ndarray,
    bbox: list[float],
    *,
    pad_fraction: float,
) -> tuple[np.ndarray, tuple[int, int, int, int], int, int]:
    height, width = image.shape[:2]
    x, y, box_width, box_height = bbox
    core_left = max(0, math.floor(x))
    core_top = max(0, math.floor(y))
    core_right = min(width, math.ceil(x + box_width))
    core_bottom = min(height, math.ceil(y + box_height))
    if core_right <= core_left or core_bottom <= core_top:
        raise ValueError(f"invalid bounding box: {bbox}")

    pad = max(1, round(max(box_width, box_height) * pad_fraction))
    left = max(0, core_left - pad)
    top = max(0, core_top - pad)
    right = min(width, core_right + pad)
    bottom = min(height, core_bottom + pad)
    gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    _, raw_ink = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(raw_ink, 8)
    minimum_area = max(2, round(box_width * box_height * 0.001))
    keep = np.zeros(count, dtype=bool)
    for component in range(1, count):
        center_x, center_y = centroids[component]
        absolute_x = left + center_x
        absolute_y = top + center_y
        keep[component] = (
            core_left - 2 <= absolute_x <= core_right + 2
            and core_top - 2 <= absolute_y <= core_bottom + 2
            and stats[component, cv2.CC_STAT_AREA] >= minimum_area
        )

    ink = np.where(keep[labels], np.uint8(255), np.uint8(0))
    kept_components = int(keep.sum())
    if not np.any(ink):
        ink = raw_ink
    lifted = np.where(ink > 0, np.uint8(0), np.uint8(255))
    removed_components = max(0, count - 1 - kept_components)
    return lifted, (left, top, right, bottom), kept_components, removed_components


def render_clean_sheet(
    image: np.ndarray,
    characters: list[dict[str, object]],
    *,
    pad_fraction: float,
) -> tuple[np.ndarray, dict[str, object]]:
    canvas = np.full(image.shape[:2], 255, dtype=np.uint8)
    rendered_boxes = 0
    blank_boxes = 0
    kept_components = 0
    removed_components = 0

    for character in characters:
        bbox = character.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("every character detection must have a four-value bbox")
        lifted, (left, top, right, bottom), kept, removed = clean_character(
            image,
            [float(value) for value in bbox],
            pad_fraction=pad_fraction,
        )
        if np.any(lifted < 255):
            rendered_boxes += 1
        else:
            blank_boxes += 1
        kept_components += kept
        removed_components += removed
        canvas[top:bottom, left:right] = np.minimum(
            canvas[top:bottom, left:right],
            lifted,
        )

    return canvas, {
        "detected_boxes": len(characters),
        "rendered_boxes": rendered_boxes,
        "blank_boxes": blank_boxes,
        "kept_components": kept_components,
        "removed_components": removed_components,
        "foreground_fraction": float(np.mean(canvas < 255)),
        "white_fraction": float(np.mean(canvas == 255)),
    }


def render_manifest(
    manifest_path: Path,
    detections_path: Path,
    output_root: Path,
    *,
    pad_fraction: float,
) -> None:
    manifest = read_jsonl(manifest_path)
    detections = {
        str(record.get("case_id")): record for record in read_jsonl(detections_path)
    }
    expected = [str(record.get("case_id")) for record in manifest]
    if not all(expected) or len(expected) != len(set(expected)):
        raise ValueError("manifest case_ids must be non-empty and unique")
    if set(detections) != set(expected):
        raise ValueError("detection and manifest case_ids differ")

    output_root.mkdir(parents=True, exist_ok=True)
    images_root = output_root / "images"
    images_root.mkdir(exist_ok=True)
    source_sha256 = sha256_file(Path(__file__))
    detection_sha256 = sha256_file(detections_path)
    output_records: list[dict[str, object]] = []

    for record in manifest:
        case_id = str(record["case_id"])
        image_path = Path(str(record["image"]))
        if not image_path.is_absolute():
            image_path = REPOSITORY_ROOT / image_path
        if sha256_file(image_path) != record["sha256"]["image"]:
            raise ValueError(f"image hash mismatch: {case_id}")
        image = cv2.imdecode(
            np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            raise ValueError(f"cannot decode source image: {image_path}")
        characters = detections[case_id].get("characters")
        if not isinstance(characters, list):
            raise ValueError(f"detection has no character list: {case_id}")

        clean_sheet, statistics = render_clean_sheet(
            image,
            characters,
            pad_fraction=pad_fraction,
        )
        output_path = images_root / f"{case_id.replace('/', '--')}.png"
        if not cv2.imwrite(str(output_path), clean_sheet):
            raise RuntimeError(f"failed to write clean sheet: {output_path}")
        output_records.append(
            {
                "schema_version": 1,
                "case_id": case_id,
                "selection_rank": record["selection_rank"],
                "strata": record["strata"],
                "source_path": record["source_path"],
                "source_image_sha256": record["sha256"]["image"],
                "clean_sheet": str(output_path),
                "clean_sheet_sha256": sha256_file(output_path),
                "image_size": [image.shape[1], image.shape[0]],
                "pad_fraction": pad_fraction,
                "renderer_sha256": source_sha256,
                "detections_sha256": detection_sha256,
                "detector_fingerprint": detections[case_id].get(
                    "candidate_fingerprint"
                ),
                **statistics,
            }
        )
        print(
            f"{case_id}: {statistics['rendered_boxes']}/{statistics['detected_boxes']} "
            "boxes lifted",
            flush=True,
        )

    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in output_records
    )
    (output_root / "index.jsonl").write_text(payload, encoding="utf-8", newline="\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pad-fraction", type=float, default=0.08)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not 0.0 <= args.pad_fraction <= 0.25:
        raise SystemExit("--pad-fraction must be between zero and 0.25")
    render_manifest(
        args.manifest.resolve(),
        args.detections.resolve(),
        args.out.resolve(),
        pad_fraction=args.pad_fraction,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
