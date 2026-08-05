"""Classify detected character boxes into primary text and commentary layers.

Premodern Chinese prints set interlinear commentary at roughly half the width
of primary characters, so a page's box widths separate into two clusters when
commentary is present. Classification is deterministic: an optimal 1-D
two-split of sorted box widths, accepted as two layers only when the small
cluster is materially narrower and populous enough to be a real layer.

Column indices and bounding boxes are recomputed with the same clustering as
``geometry_columns`` and asserted equal against its output, so layer tags line
up with recorded column keys.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from geometry_columns import read_jsonl, split_columns, split_registers

# Commentary is ~0.5x primary width; clusters closer than this are one layer.
MAX_WIDTH_RATIO = 0.72
# A real commentary layer covers at least this fraction of the page's boxes.
MIN_LAYER_FRACTION = 0.15


def two_split(widths: list[float]) -> tuple[float, float, float] | None:
    """Return (split_width, small_center, large_center) minimizing within-
    cluster sum of squares, or None when a page has too few boxes."""

    ordered = sorted(widths)
    count = len(ordered)
    if count < 8:
        return None
    prefix = [0.0]
    prefix_squares = [0.0]
    for width in ordered:
        prefix.append(prefix[-1] + width)
        prefix_squares.append(prefix_squares[-1] + width * width)

    def sse(start: int, stop: int) -> float:
        size = stop - start
        total = prefix[stop] - prefix[start]
        squares = prefix_squares[stop] - prefix_squares[start]
        return squares - total * total / size

    best: tuple[float, int] | None = None
    for split in range(2, count - 1):
        cost = sse(0, split) + sse(split, count)
        if best is None or cost < best[0]:
            best = (cost, split)
    split = best[1]
    small = ordered[:split]
    large = ordered[split:]
    return (
        (small[-1] + large[0]) / 2,
        statistics.fmean(small),
        statistics.fmean(large),
    )


def classify_page(characters: list[dict[str, object]]) -> dict[str, object]:
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
    split = two_split([box["w"] for box in boxes])
    two_layer = False
    threshold = 0.0
    centers = None
    if split is not None:
        threshold, small_center, large_center = split
        small_fraction = sum(box["w"] < threshold for box in boxes) / len(boxes)
        two_layer = (
            small_center / large_center <= MAX_WIDTH_RATIO
            and MIN_LAYER_FRACTION <= small_fraction <= 1 - MIN_LAYER_FRACTION
        )
        centers = [small_center, large_center]
    for box in boxes:
        box["layer"] = "commentary" if two_layer and box["w"] < threshold else "primary"

    columns_payload = []
    for register_index, register in enumerate(split_registers(boxes)):
        for column_index, column in enumerate(split_columns(register)):
            primary = sum(box["layer"] == "primary" for box in column)
            columns_payload.append(
                {
                    "register": register_index,
                    "column": column_index,
                    "boxes": len(column),
                    "primary_boxes": primary,
                    "commentary_boxes": len(column) - primary,
                    "layer": "primary" if primary * 2 >= len(column) else "commentary",
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
            )
    return {
        "two_layer": two_layer,
        "width_split": round(threshold, 2) if two_layer else None,
        "width_centers": [round(center, 2) for center in centers]
        if centers is not None
        else None,
        "primary_boxes": sum(box["layer"] == "primary" for box in boxes),
        "commentary_boxes": sum(box["layer"] == "commentary" for box in boxes),
        "columns": columns_payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    detections = {
        str(record["case_id"]): record for record in read_jsonl(args.detections)
    }
    geometry = {str(record["case_id"]): record for record in read_jsonl(args.geometry)}

    rows = []
    for case_id, page in geometry.items():
        classified = classify_page(detections[case_id]["characters"])
        recorded = [
            (register["register"], column["column"], column["bbox"], column["boxes"])
            for register in page["structure"]
            for column in register["columns"]
        ]
        recomputed = [
            (column["register"], column["column"], column["bbox"], column["boxes"])
            for column in classified["columns"]
        ]
        if recorded != recomputed:
            raise AssertionError(
                f"column structure diverged from recorded geometry: {case_id}"
            )
        rows.append({"schema_version": 1, "case_id": case_id, **classified})
        print(
            f"{case_id}: {'two-layer' if classified['two_layer'] else 'one-layer'} "
            f"primary={classified['primary_boxes']} "
            f"commentary={classified['commentary_boxes']}",
            flush=True,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
