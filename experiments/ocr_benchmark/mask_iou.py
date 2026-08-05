"""Score character-box predictions with HRCenterNet's page-mask IoU metric."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(record)
    return records


def index_records(
    records: list[dict[str, object]],
    *,
    label: str,
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{label} record requires a non-empty case_id")
        if case_id in indexed:
            raise ValueError(f"duplicate {label} case_id: {case_id}")
        indexed[case_id] = record
    return indexed


def resolve_image(record: dict[str, object]) -> Path:
    value = record.get("image")
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {record.get('case_id')!r} has no image path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def box_mask(
    entries: object,
    *,
    width: int,
    height: int,
    label: str,
) -> np.ndarray:
    if not isinstance(entries, list):
        raise ValueError(f"{label} characters must be an array")
    mask = np.zeros((height, width), dtype=np.uint8)
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{label} character {index} must be an object")
        bbox = entry.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox)
        ):
            raise ValueError(f"{label} character {index} has an invalid bbox")
        x, y, box_width, box_height = (float(value) for value in bbox)
        left = min(max(int(x), 0), width)
        top = min(max(int(y), 0), height)
        right = min(max(int(x + box_width), 0), width)
        bottom = min(max(int(y + box_height), 0), height)
        if right <= left or bottom <= top:
            continue
        cv2.rectangle(mask, (left, top), (right - 1, bottom - 1), 1, thickness=-1)
    return mask


def corpus(record: dict[str, object]) -> str:
    strata = record.get("strata")
    if isinstance(strata, list) and strata and isinstance(strata[0], str):
        return strata[0]
    case_id = str(record.get("case_id", ""))
    parts = case_id.split("/")
    return parts[1] if len(parts) > 2 else "unknown"


def score(manifest: Path, predictions: Path) -> dict[str, object]:
    cases = index_records(read_jsonl(manifest), label="manifest")
    outputs = index_records(read_jsonl(predictions), label="prediction")
    missing = sorted(set(cases) - set(outputs))
    unknown = sorted(set(outputs) - set(cases))
    if missing or unknown:
        raise ValueError(
            f"prediction coverage mismatch: missing={missing[:5]}, unknown={unknown[:5]}"
        )

    observations: list[dict[str, object]] = []
    by_corpus: dict[str, list[float]] = defaultdict(list)
    total_intersection = 0
    total_union = 0
    for case_id, case in cases.items():
        image = cv2.imread(str(resolve_image(case)), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"cannot decode benchmark image for {case_id}")
        height, width = image.shape
        expected = box_mask(
            case.get("characters"),
            width=width,
            height=height,
            label=f"gold {case_id}",
        )
        predicted = box_mask(
            outputs[case_id].get("characters"),
            width=width,
            height=height,
            label=f"prediction {case_id}",
        )
        intersection = int(np.count_nonzero(expected & predicted))
        union = int(np.count_nonzero(expected | predicted))
        page_iou = 1.0 if union == 0 else intersection / union
        case_corpus = corpus(case)
        by_corpus[case_corpus].append(page_iou)
        total_intersection += intersection
        total_union += union
        observations.append(
            {
                "case_id": case_id,
                "corpus": case_corpus,
                "intersection_pixels": intersection,
                "union_pixels": union,
                "page_mask_iou": page_iou,
            }
        )

    return {
        "schema_version": 1,
        "metric": "character_box_union_page_mask_iou",
        "cases": len(cases),
        "mean_page_mask_iou": float(
            np.mean([observation["page_mask_iou"] for observation in observations])
        ),
        "pixel_micro_iou": (
            1.0 if total_union == 0 else total_intersection / total_union
        ),
        "corpora": {
            name: {
                "cases": len(values),
                "mean_page_mask_iou": float(np.mean(values)),
            }
            for name, values in sorted(by_corpus.items())
        },
        "observations": observations,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = score(args.manifest.resolve(), args.predictions.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in report.items() if key != "observations"}, indent=2))
    print(f"wrote {args.out.resolve()}")


if __name__ == "__main__":
    main()
