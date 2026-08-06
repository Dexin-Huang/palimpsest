"""Render the worst localization pages as gold/baseline/challenger overlays."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PANEL_WIDTH = 500
COLORS = {
    "gold": (40, 190, 40),
    "baseline": (0, 140, 255),
    "challenger": (255, 150, 0),
}




from palimpsest.factory.workspace.io import (
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
def index_records(records: list[dict[str, object]], label: str) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{label} has a record without a case_id")
        if case_id in indexed:
            raise ValueError(f"{label} has duplicate case_id {case_id!r}")
        indexed[case_id] = record
    return indexed


def resolve_image(record: dict[str, object]) -> Path:
    value = record.get("image")
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {record.get('case_id')!r} has no image path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def overlay(
    image: np.ndarray,
    characters: object,
    *,
    title: str,
    color: tuple[int, int, int],
) -> np.ndarray:
    canvas = image.copy()
    if not isinstance(characters, list):
        raise ValueError(f"{title} characters must be a list")
    thickness = max(1, image.shape[1] // 1000)
    for character in characters:
        if not isinstance(character, dict):
            raise ValueError(f"{title} character entries must be objects")
        bbox = character.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(value, (int, float)) for value in bbox)
        ):
            raise ValueError(f"{title} character bbox must contain four numbers")
        x, y, width, height = (float(value) for value in bbox)
        cv2.rectangle(
            canvas,
            (round(x), round(y)),
            (round(x + width), round(y + height)),
            color,
            thickness,
        )
    scale = PANEL_WIDTH / canvas.shape[1]
    panel = cv2.resize(
        canvas,
        (PANEL_WIDTH, max(1, round(canvas.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )
    cv2.rectangle(panel, (0, 0), (PANEL_WIDTH, 34), (20, 20, 20), -1)
    cv2.putText(
        panel,
        f"{title} ({len(characters)})",
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return panel


def render_audit(
    manifest_path: Path,
    baseline_path: Path,
    challenger_path: Path,
    challenger_report_path: Path,
    output_root: Path,
    *,
    count: int,
) -> dict[str, object]:
    manifest = index_records(read_jsonl(manifest_path), "manifest")
    baseline = index_records(read_jsonl(baseline_path), "baseline predictions")
    challenger = index_records(read_jsonl(challenger_path), "challenger predictions")
    report = json.loads(challenger_report_path.read_text(encoding="utf-8"))
    per_case = report.get("per_case")
    if not isinstance(per_case, list):
        raise ValueError("challenger report lacks per_case evidence")
    ranked = sorted(
        per_case,
        key=lambda row: (
            float(row.get("detection", {}).get("f1") or 0.0),
            str(row.get("case_id", "")),
        ),
    )[:count]
    if not ranked:
        raise ValueError("challenger report has no cases to audit")

    rows: list[np.ndarray] = []
    audited: list[dict[str, object]] = []
    for report_row in ranked:
        case_id = str(report_row.get("case_id", ""))
        if case_id not in manifest or case_id not in baseline or case_id not in challenger:
            raise ValueError(f"audit inputs do not all contain {case_id!r}")
        source = manifest[case_id]
        image = cv2.imread(str(resolve_image(source)), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode audit image for {case_id!r}")
        panels = [
            overlay(image, source.get("characters"), title="Gold", color=COLORS["gold"]),
            overlay(
                image,
                baseline[case_id].get("characters"),
                title="Baseline",
                color=COLORS["baseline"],
            ),
            overlay(
                image,
                challenger[case_id].get("characters"),
                title="RF-DETR",
                color=COLORS["challenger"],
            ),
        ]
        row = np.hstack(panels)
        label = np.full((42, row.shape[1], 3), 245, dtype=np.uint8)
        detection = report_row.get("detection", {})
        cv2.putText(
            label,
            f"{case_id}   F1={float(detection.get('f1') or 0.0):.4f}   "
            f"P={float(detection.get('precision') or 0.0):.4f}   "
            f"R={float(detection.get('recall') or 0.0):.4f}",
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (25, 25, 25),
            1,
            cv2.LINE_AA,
        )
        rows.append(np.vstack([label, row]))
        audited.append(
            {
                "case_id": case_id,
                "detection": detection,
                "gold_boxes": len(source.get("characters", [])),
                "baseline_boxes": len(baseline[case_id].get("characters", [])),
                "challenger_boxes": len(challenger[case_id].get("characters", [])),
            }
        )

    sheet = np.vstack(rows)
    output_root.mkdir(parents=True, exist_ok=True)
    image_path = output_root / "worst-pages.png"
    if not cv2.imwrite(str(image_path), sheet):
        raise OSError(f"failed to write audit sheet: {image_path}")
    audit = {
        "schema_version": 1,
        "order": "ascending_challenger_detection_f1",
        "colors_bgr": {name: list(color) for name, color in COLORS.items()},
        "cases": audited,
    }
    (output_root / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return audit


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--challenger-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.count <= 0:
        raise SystemExit("--count must be positive")
    audit = render_audit(
        args.manifest.resolve(),
        args.baseline.resolve(),
        args.challenger.resolve(),
        args.challenger_report.resolve(),
        args.out.resolve(),
        count=args.count,
    )
    print(json.dumps(audit["cases"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
