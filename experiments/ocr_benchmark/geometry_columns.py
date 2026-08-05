"""Group RF-DETR character boxes into registers and columns, then reconcile.

Deterministic geometry only: no model calls. Produces per-page column
structure with expected character counts, a box-overlay JPEG for the
agentic adjudicator, and count-consistency statistics against a loose
full-page transcription when one is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import unicodedata
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OVERLAY_MAX_SIDE = 1600
# A new column starts when the x-interval gap exceeds this fraction of the
# median box width; vertical-text columns are separated by clear gutters.
COLUMN_GAP_FRACTION = 0.45
# A new register (horizontal band of columns) starts at a y-gap taller than
# this multiple of the median box height.
REGISTER_GAP_FACTOR = 1.8
RATIO_LOW = 0.6
RATIO_HIGH = 1.5


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_counted(text: str) -> str:
    """Characters that should correspond to detected boxes: no whitespace."""

    normalized = unicodedata.normalize(
        "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return "".join(character for character in normalized if not character.isspace())


def split_registers(boxes: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    """Split boxes into horizontal bands separated by page-wide y-gaps."""

    if not boxes:
        return []
    median_height = statistics.median(box["h"] for box in boxes)
    ordered = sorted(boxes, key=lambda box: box["y"])
    registers: list[list[dict[str, float]]] = [[ordered[0]]]
    register_bottom = ordered[0]["y"] + ordered[0]["h"]
    for box in ordered[1:]:
        if box["y"] - register_bottom > REGISTER_GAP_FACTOR * median_height:
            registers.append([box])
        else:
            registers[-1].append(box)
        register_bottom = max(register_bottom, box["y"] + box["h"])
    return registers


def split_columns(register: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    """Split one register into vertical columns by x-center gaps.

    Box centers inside one vertical column are nearly identical, while
    adjacent columns sit a full column pitch apart, so sorting centers right
    to left and splitting at gaps wider than half the median box width
    recovers columns even when box edges overlap across the gutter.
    """

    median_width = statistics.median(box["w"] for box in register)
    ordered = sorted(register, key=lambda box: -(box["x"] + box["w"] / 2))
    columns: list[list[dict[str, float]]] = [[ordered[0]]]
    previous_center = ordered[0]["x"] + ordered[0]["w"] / 2
    for box in ordered[1:]:
        center = box["x"] + box["w"] / 2
        if previous_center - center > COLUMN_GAP_FRACTION * median_width:
            columns.append([box])
        else:
            columns[-1].append(box)
        previous_center = center
    return [sorted(column, key=lambda box: box["y"]) for column in columns]


def page_structure(characters: list[dict[str, object]]) -> dict[str, object]:
    boxes = [
        {
            "x": float(bbox[0]),
            "y": float(bbox[1]),
            "w": float(bbox[2]),
            "h": float(bbox[3]),
        }
        for character in characters
        for bbox in [character["bbox"]]
    ]
    registers = split_registers(boxes)
    register_payload = []
    for register_index, register in enumerate(registers):
        columns = split_columns(register)
        register_payload.append(
            {
                "register": register_index,
                "boxes": len(register),
                "columns": [
                    {
                        "column": column_index,
                        "boxes": len(column),
                        "bbox": [
                            round(min(box["x"] for box in column), 1),
                            round(min(box["y"] for box in column), 1),
                            round(
                                max(box["x"] + box["w"] for box in column)
                                - min(box["x"] for box in column),
                                1,
                            ),
                            round(
                                max(box["y"] + box["h"] for box in column)
                                - min(box["y"] for box in column),
                                1,
                            ),
                        ],
                    }
                    for column_index, column in enumerate(columns)
                ],
            }
        )
    column_counts = [
        column["boxes"]
        for register in register_payload
        for column in register["columns"]
    ]
    return {
        "detected_boxes": len(boxes),
        "registers": len(register_payload),
        "columns": len(column_counts),
        "column_box_counts": column_counts,
        "structure": register_payload,
    }


def render_overlay(
    image_path: Path, characters: list[dict[str, object]], output_path: Path
) -> None:
    image = cv2.imdecode(
        np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        raise ValueError(f"cannot decode source image: {image_path}")
    for character in characters:
        x, y, w, h = (float(value) for value in character["bbox"])
        cv2.rectangle(
            image,
            (round(x), round(y)),
            (round(x + w), round(y + h)),
            (0, 200, 0),
            2,
        )
    scale = OVERLAY_MAX_SIDE / max(image.shape[:2])
    if scale < 1.0:
        image = cv2.resize(
            image,
            (round(image.shape[1] * scale), round(image.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, payload = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError(f"failed to encode overlay: {output_path}")
    output_path.write_bytes(payload.tobytes())


def reconcile(
    structure: dict[str, object], transcription: str | None
) -> dict[str, object]:
    if transcription is None:
        return {"transcription_available": False}
    counted = normalize_counted(transcription)
    lines = [line for line in transcription.splitlines() if line.strip()]
    detected = int(structure["detected_boxes"])
    ratio = len(counted) / max(detected, 1)
    return {
        "transcription_available": True,
        "transcription_characters": len(counted),
        "transcription_lines": len(lines),
        "count_ratio": ratio,
        "count_flag": not RATIO_LOW <= ratio <= RATIO_HIGH,
        "line_column_delta": len(lines) - int(structure["columns"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--transcriptions", type=Path, default=None)
    parser.add_argument("--overlays", type=Path, default=None)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    detections = {
        str(record["case_id"]): record for record in read_jsonl(args.detections)
    }
    manifest = read_jsonl(args.manifest)
    transcriptions: dict[str, str] = {}
    if args.transcriptions is not None:
        transcriptions = {
            str(record["case_id"]): str(record["transcription"])
            for record in read_jsonl(args.transcriptions)
        }
    if args.overlays is not None:
        args.overlays.mkdir(parents=True, exist_ok=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for record in manifest:
        case_id = str(record["case_id"])
        detection = detections[case_id]
        characters = detection["characters"]
        if not isinstance(characters, list) or not characters:
            raise ValueError(f"no character detections: {case_id}")
        structure = page_structure(characters)
        row: dict[str, object] = {
            "schema_version": 1,
            "case_id": case_id,
            "strata": record["strata"],
            **structure,
            "reconciliation": reconcile(structure, transcriptions.get(case_id)),
        }
        if args.overlays is not None:
            image_path = Path(str(record["image"]))
            if not image_path.is_absolute():
                image_path = REPOSITORY_ROOT / image_path
            overlay_path = args.overlays / f"{case_id.replace('/', '--')}.jpg"
            render_overlay(image_path, characters, overlay_path)
            row["overlay"] = str(overlay_path)
            row["overlay_sha256"] = sha256_file(overlay_path)
        rows.append(row)
        print(
            f"{case_id}: {structure['detected_boxes']} boxes, "
            f"{structure['registers']} registers, {structure['columns']} columns",
            flush=True,
        )

    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    args.out.write_text(payload, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
