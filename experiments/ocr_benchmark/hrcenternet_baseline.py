"""Run the published HRCenterNet implementation as an image-only control."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INPUT_SIZE = 512
OUTPUT_SIZE = 128


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def write_predictions(path: Path, predictions: Iterable[dict[str, object]]) -> None:
    payload = "".join(
        json.dumps(prediction, ensure_ascii=False, separators=(",", ":")) + "\n"
        for prediction in predictions
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)


def resolve_image(record: dict[str, object]) -> Path:
    value = record.get("image")
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {record.get('case_id')!r} has no image path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def load_model(
    source_root: Path,
    checkpoint: Path,
    device_name: str,
) -> tuple[object, object, object, str]:
    source_root = source_root.resolve()
    if not (source_root / "models" / "HRCenterNet.py").is_file():
        raise ValueError(f"HRCenterNet source tree is incomplete: {source_root}")
    sys.path.insert(0, str(source_root))
    try:
        torch = importlib.import_module("torch")
        torchvision = importlib.import_module("torchvision")
        module = importlib.import_module("models.HRCenterNet")
    finally:
        sys.path.pop(0)
    selected_device = (
        "cuda" if device_name == "auto" and torch.cuda.is_available() else device_name
    )
    if selected_device == "auto":
        selected_device = "cpu"
    device = torch.device(selected_device)
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    model = module.HRCenterNet()
    model.load_state_dict(state["model"])
    model = model.to(device)
    model.eval()
    return model, torch, torchvision, str(device)


def predict_page(
    model: object,
    torch: object,
    torchvision: object,
    image_path: Path,
    *,
    threshold: float,
    nms_iou: float,
    coordinate_transform: str,
) -> list[dict[str, object]]:
    image_module = importlib.import_module("PIL.Image")
    image = image_module.open(image_path).convert("RGB")
    page_width, page_height = image.size
    transform = torchvision.transforms.Compose(
        [
            torchvision.transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
            torchvision.transforms.ToTensor(),
        ]
    )
    device = next(model.parameters()).device
    with torch.inference_mode():
        output = model(transform(image).unsqueeze(0).to(device, dtype=torch.float))[0]
    heatmap, offset_y, offset_x, width_map, height_map = output.detach().cpu().numpy()

    flat_indices = np.flatnonzero(heatmap >= threshold)
    if not len(flat_indices):
        return []
    rows, columns = np.divmod(flat_indices, OUTPUT_SIZE)
    scores = heatmap[rows, columns]
    box_widths = width_map[rows, columns] * page_width
    box_heights = height_map[rows, columns] * page_height

    if coordinate_transform == "official":
        center_rows = rows * (page_width / OUTPUT_SIZE) + offset_y[rows, columns] * (
            page_height / OUTPUT_SIZE
        )
        center_columns = columns * (page_height / OUTPUT_SIZE) + offset_x[
            rows, columns
        ] * (page_width / OUTPUT_SIZE)
        tops = center_rows - np.floor_divide(box_widths, 2)
        lefts = center_columns - np.floor_divide(box_heights, 2)
        bottoms = center_rows + np.floor_divide(box_widths, 2)
        rights = center_columns + np.floor_divide(box_heights, 2)
    else:
        center_rows = rows * (page_height / OUTPUT_SIZE) + offset_y[rows, columns] * (
            page_height / OUTPUT_SIZE
        )
        center_columns = columns * (page_width / OUTPUT_SIZE) + offset_x[
            rows, columns
        ] * (page_width / OUTPUT_SIZE)
        tops = center_rows - np.floor_divide(box_heights, 2)
        lefts = center_columns - np.floor_divide(box_widths, 2)
        bottoms = center_rows + np.floor_divide(box_heights, 2)
        rights = center_columns + np.floor_divide(box_widths, 2)
    boxes = np.column_stack([lefts, tops, rights, bottoms]).astype(np.float32)
    kept = torchvision.ops.nms(
        torch.from_numpy(boxes),
        torch.from_numpy(scores.astype(np.float32)),
        nms_iou,
    ).cpu().numpy()

    detections: list[dict[str, object]] = []
    for index in kept:
        left, top, right, bottom = (float(value) for value in boxes[index])
        left = min(max(left, 0.0), float(page_width))
        top = min(max(top, 0.0), float(page_height))
        right = min(max(right, 0.0), float(page_width))
        bottom = min(max(bottom, 0.0), float(page_height))
        if right <= left or bottom <= top:
            continue
        detections.append(
            {
                "bbox": [
                    round(left, 3),
                    round(top, 3),
                    round(right - left, 3),
                    round(bottom - top, 3),
                ],
                "score": round(float(scores[index]), 6),
            }
        )
    return detections


def predict_manifest(
    manifest: Path,
    source_root: Path,
    checkpoint: Path,
    output: Path,
    *,
    threshold: float,
    nms_iou: float,
    device_name: str,
    coordinate_transform: str,
) -> None:
    records = read_jsonl(manifest)
    expected_ids = [str(record.get("case_id", "")) for record in records]
    if not all(expected_ids) or len(expected_ids) != len(set(expected_ids)):
        raise ValueError("manifest case_ids must be non-empty and unique")
    completed: dict[str, dict[str, object]] = {}
    if output.exists():
        completed = {str(record.get("case_id", "")): record for record in read_jsonl(output)}
        unknown = sorted(set(completed) - set(expected_ids))
        if unknown:
            raise ValueError(f"output contains unknown case_ids: {unknown[:5]}")

    model, torch, torchvision, device = load_model(source_root, checkpoint, device_name)
    checkpoint_sha256 = sha256_file(checkpoint)
    source_paths = sorted((source_root / "models").glob("*.py"))
    source_sha256 = {
        str(path.relative_to(source_root)): sha256_file(path) for path in source_paths
    }
    fingerprint_payload = json.dumps(
        {
            "source_sha256": source_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "threshold": threshold,
            "nms_iou": nms_iou,
            "coordinate_transform": coordinate_transform,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    fingerprint = hashlib.sha256(fingerprint_payload).hexdigest()

    for index, record in enumerate(records, start=1):
        case_id = str(record["case_id"])
        if case_id in completed:
            continue
        started = time.perf_counter()
        characters = predict_page(
            model,
            torch,
            torchvision,
            resolve_image(record),
            threshold=threshold,
            nms_iou=nms_iou,
            coordinate_transform=coordinate_transform,
        )
        completed[case_id] = {
            "case_id": case_id,
            "characters": characters,
            "candidate_id": (
                f"hrcenternet/published-checkpoint-{coordinate_transform}-postprocess-v1"
            ),
            "candidate_fingerprint": fingerprint,
            "checkpoint_sha256": checkpoint_sha256,
            "latency_seconds": round(time.perf_counter() - started, 6),
            "threshold": threshold,
            "nms_iou": nms_iou,
            "coordinate_transform": coordinate_transform,
            "device": device,
        }
        write_predictions(output, (completed[value] for value in expected_ids if value in completed))
        print(f"{index}/{len(records)} {case_id}: {len(characters)} boxes", flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.3)
    parser.add_argument("--nms-iou", type=float, default=0.1)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--coordinate-transform",
        choices=("corrected", "official"),
        default="corrected",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    predict_manifest(
        args.manifest.resolve(),
        args.source_root.resolve(),
        args.checkpoint.resolve(),
        args.out.resolve(),
        threshold=args.threshold,
        nms_iou=args.nms_iou,
        device_name=args.device,
        coordinate_transform=args.coordinate_transform,
    )


if __name__ == "__main__":
    main()
