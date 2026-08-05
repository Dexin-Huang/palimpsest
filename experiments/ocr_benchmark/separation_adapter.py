"""Adapt the zero-cost separation2 candidate to OCR localization predictions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SEPARATION_ROOT = REPOSITORY_ROOT / "experiments" / "separation2"
FINGERPRINT_SOURCES = (
    Path(__file__).resolve(),
    SEPARATION_ROOT / "separate.py",
    SEPARATION_ROOT / "prep.py",
    SEPARATION_ROOT / "features.py",
    REPOSITORY_ROOT / "experiments" / "char_inventory" / "refine.py",
    REPOSITORY_ROOT / "experiments" / "m2_exemplars" / "candidate.py",
)


def load_candidate():
    sys.path.insert(0, str(SEPARATION_ROOT))
    spec = importlib.util.spec_from_file_location(
        "ocr_benchmark_separation2", SEPARATION_ROOT / "separate.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load separation2 candidate")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_fingerprint(*, suppress_rules: bool) -> str:
    digest = hashlib.sha256()
    for path in FINGERPRINT_SOURCES:
        digest.update(path.relative_to(REPOSITORY_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    digest.update(f"suppress_horizontal_rules={suppress_rules}".encode())
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, object]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def source_bbox(cell_bbox: tuple[int, int, int, int], prep: dict) -> list[int]:
    frame = prep.get("frame")
    gutter = prep.get("gutter")
    if (
        not isinstance(frame, list)
        or len(frame) != 4
        or not isinstance(gutter, list)
        or len(gutter) != 2
    ):
        raise ValueError("separation result lacks frame/gutter coordinate provenance")
    x, y, width, height = cell_bbox
    return [x + int(frame[0]) + int(gutter[0]), y + int(frame[1]), width, height]


def resolve_image(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest image must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def existing_case_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    case_ids: set[str] = set()
    for record in read_jsonl(path):
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError(f"invalid existing prediction case_id: {case_id!r}")
        case_ids.add(case_id)
    return case_ids


def append_prediction(path: Path, prediction: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(prediction, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        stream.flush()


def suppress_horizontal_rules(page: np.ndarray) -> np.ndarray:
    """Remove only near-continuous rules spanning at least a quarter page."""
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    width = page.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, width // 8), 1))
    candidates = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidates)
    mask = np.zeros_like(gray)
    maximum_height = max(12, page.shape[0] // 50)
    for label in range(1, count):
        x, y, component_width, component_height, _ = stats[label]
        if component_width >= width // 4 and component_height <= maximum_height:
            mask[labels == label] = 255
    if not np.any(mask):
        return page
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    return cv2.inpaint(page, mask, 3, cv2.INPAINT_TELEA)


def execute(manifest: Path, output: Path, *, suppress_rules: bool = False) -> None:
    cases = read_jsonl(manifest)
    expected = {str(case.get("case_id", "")) for case in cases}
    if "" in expected or len(expected) != len(cases):
        raise ValueError("manifest case IDs must be non-empty and unique")
    completed = existing_case_ids(output)
    unknown = completed - expected
    if unknown:
        raise ValueError(f"output contains unknown cases: {sorted(unknown)[:5]}")

    candidate = load_candidate()
    fingerprint = candidate_fingerprint(suppress_rules=suppress_rules)
    for case in cases:
        case_id = str(case["case_id"])
        if case_id in completed:
            continue
        image_path = resolve_image(case.get("image"))
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode benchmark image: {image_path}")
        if suppress_rules:
            def prepare_without_rules(raw_image):
                page, info = candidate.prep.prepare(raw_image)
                if page is None:
                    return None, info
                return suppress_horizontal_rules(page), info

            result = candidate.separate(image, prepare_fn=prepare_without_rules)
        else:
            result = candidate.separate(image)
        prep = result["prep"]
        characters = [
            {
                "bbox": source_bbox(cell.bbox(), prep),
                "score": 1.0,
            }
            for cell in result["cells"]
        ]
        append_prediction(
            output,
            {
                "case_id": case_id,
                "characters": characters,
                "candidate_id": (
                    "separation2/horizontal-rule-suppression-v1"
                    if suppress_rules
                    else "separation2/current-zero-cost"
                ),
                "candidate_fingerprint": fingerprint,
                "kept": result["kept"],
                "junked": result["junked"],
                "prior_killed": result["prior_killed"],
                "columns": result["columns"],
            },
        )
        completed.add(case_id)
        print(f"{len(completed)}/{len(cases)} {case_id}: {len(characters)} boxes")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--suppress-horizontal-rules", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    execute(
        args.manifest.resolve(),
        args.out.resolve(),
        suppress_rules=args.suppress_horizontal_rules,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
