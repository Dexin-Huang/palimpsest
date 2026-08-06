"""Train and run the one-class tiled RF-DETR localization candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from importlib import metadata
import sys
import time
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TILE_SIZE = 512
DEFAULT_OVERLAP = 96
DEFAULT_THRESHOLD = 0.25
DEFAULT_NMS_IOU = 0.40
EXPECTED_PRETRAINED_SHA256 = (
    "d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d"
)





from palimpsest.factory.workspace.io import (
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
def tile_origins(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    stride = tile_size - overlap
    origins = list(range(0, length - tile_size + 1, stride))
    final_origin = length - tile_size
    if origins[-1] != final_origin:
        origins.append(final_origin)
    return origins


def box_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    left = np.maximum(box[0], boxes[:, 0])
    top = np.maximum(box[1], boxes[:, 1])
    right = np.minimum(box[2], boxes[:, 2])
    bottom = np.minimum(box[3], boxes[:, 3])
    intersection = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
    box_area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    areas = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
        0.0, boxes[:, 3] - boxes[:, 1]
    )
    union = box_area + areas - intersection
    return np.divide(
        intersection, union, out=np.zeros_like(intersection), where=union > 0
    )


def non_maximum_suppression(
    boxes: np.ndarray, scores: np.ndarray, iou_threshold: float
) -> list[int]:
    if len(boxes) == 0:
        return []
    order = np.argsort(scores, kind="stable")[::-1]
    kept: list[int] = []
    while len(order):
        current = int(order[0])
        kept.append(current)
        if len(order) == 1:
            break
        remaining = order[1:]
        order = remaining[box_iou(boxes[current], boxes[remaining]) <= iou_threshold]
    return kept


def load_detector(checkpoint: Path):
    try:
        from rfdetr import RFDETR
    except ImportError as exc:
        raise RuntimeError(
            "RF-DETR is required. Install `rfdetr[train]==1.8.3` in the experiment environment."
        ) from exc
    return RFDETR.from_checkpoint(str(checkpoint))


def seed_training(seed: int) -> None:
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)


def runtime_versions() -> dict[str, str]:
    packages = (
        "numpy",
        "opencv-python",
        "opencv-python-headless",
        "pytorch-lightning",
        "rfdetr",
        "torch",
        "torchvision",
    )
    versions: dict[str, str] = {"python": sys.version}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def hardware_identity(device: str) -> dict[str, object]:
    import torch

    identity: dict[str, object] = {
        "requested_device": device,
        "cuda_available": torch.cuda.is_available(),
        "cuda_runtime": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
    }
    if device.startswith("cuda") and torch.cuda.is_available():
        index = torch.cuda.current_device()
        properties = torch.cuda.get_device_properties(index)
        identity.update(
            {
                "cuda_device_index": index,
                "cuda_device_name": properties.name,
                "cuda_total_memory_bytes": properties.total_memory,
                "cuda_compute_capability": [properties.major, properties.minor],
            }
        )
    return identity


def train(
    dataset: Path,
    output: Path,
    *,
    epochs: int,
    device: str,
    seed: int,
    candidate_id: str,
    lr_drop: int | None = None,
    brightness_contrast: bool = False,
) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite training run: {output}")
    dataset_record = dataset / "dataset.json"
    if not dataset_record.is_file():
        raise FileNotFoundError(f"missing dataset identity: {dataset_record}")
    output.mkdir(parents=True, exist_ok=True)
    seed_training(seed)
    try:
        from rfdetr import RFDETRNano
    except ImportError as exc:
        raise RuntimeError(
            "RF-DETR is required. Install `rfdetr[train]==1.8.3` in the experiment environment."
        ) from exc
    model = RFDETRNano(resolution=DEFAULT_TILE_SIZE, gradient_checkpointing=True)
    pretrained_path = Path(str(model.model_config.pretrain_weights))
    pretrained_sha256 = sha256_file(pretrained_path)
    if pretrained_sha256 != EXPECTED_PRETRAINED_SHA256:
        raise RuntimeError(
            "RF-DETR Nano pretrained weights changed: "
            f"expected {EXPECTED_PRETRAINED_SHA256}, got {pretrained_sha256}"
        )
    training_settings = {
        "epochs": epochs,
        "batch_size": 1,
        "grad_accum_steps": 8,
        "lr": 1e-4,
        "lr_encoder": 1.5e-4,
        "lr_drop": lr_drop if lr_drop is not None else max(1, int(epochs * 0.75)),
        "early_stopping": False,
        "checkpoint_interval": 1,
        "resolution": DEFAULT_TILE_SIZE,
        "multi_scale": False,
        "expanded_scales": False,
        "tensorboard": False,
        "wandb": False,
        "num_workers": 2,
        "device": device,
        "seed": seed,
    }
    if brightness_contrast:
        training_settings["aug_config"] = {
            "RandomBrightnessContrast": {
                "brightness_limit": 0.1,
                "contrast_limit": 0.1,
                "p": 0.3,
            }
        }
    identity = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "candidate_source_sha256": sha256_file(Path(__file__)),
        "dataset_sha256": sha256_file(dataset_record),
        "dataset_builder_source_sha256": sha256_file(
            Path(__file__).with_name("rfdetr_dataset.py")
        ),
        "pretrained_weights": pretrained_path.resolve().as_posix(),
        "pretrained_sha256": pretrained_sha256,
        "model": {
            "architecture": "RFDETRNano",
            "resolution": DEFAULT_TILE_SIZE,
            "gradient_checkpointing": True,
        },
        "training": training_settings,
        "runtime": runtime_versions(),
        "hardware": hardware_identity(device),
    }
    (output / "experiment_identity.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    model.train(
        dataset_dir=str(dataset),
        output_dir=str(output),
        **training_settings,
        notes=json.dumps(identity, ensure_ascii=True, sort_keys=True),
    )


def predict_page(
    model: object,
    image: np.ndarray,
    *,
    tile_size: int,
    overlap: int,
    threshold: float,
    nms_iou: float,
) -> list[dict[str, object]]:
    height, width = image.shape[:2]
    page_boxes: list[list[float]] = []
    page_scores: list[float] = []
    for y0 in tile_origins(height, tile_size, overlap):
        for x0 in tile_origins(width, tile_size, overlap):
            tile = cv2.cvtColor(
                image[
                    y0 : min(height, y0 + tile_size), x0 : min(width, x0 + tile_size)
                ],
                cv2.COLOR_BGR2RGB,
            )
            detections = model.predict(
                tile,
                threshold=threshold,
                include_source_image=False,
            )
            for xyxy, confidence in zip(
                detections.xyxy, detections.confidence, strict=True
            ):
                left, top, right, bottom = (float(value) for value in xyxy)
                left = min(max(left + x0, 0.0), float(width))
                top = min(max(top + y0, 0.0), float(height))
                right = min(max(right + x0, 0.0), float(width))
                bottom = min(max(bottom + y0, 0.0), float(height))
                if right <= left or bottom <= top:
                    continue
                page_boxes.append([left, top, right, bottom])
                page_scores.append(float(confidence))
    if not page_boxes:
        return []
    boxes = np.asarray(page_boxes, dtype=np.float32)
    scores = np.asarray(page_scores, dtype=np.float32)
    kept = non_maximum_suppression(boxes, scores, nms_iou)
    return [
        {
            "bbox": [
                round(float(boxes[index, 0]), 3),
                round(float(boxes[index, 1]), 3),
                round(float(boxes[index, 2] - boxes[index, 0]), 3),
                round(float(boxes[index, 3] - boxes[index, 1]), 3),
            ],
            "score": round(float(scores[index]), 6),
        }
        for index in kept
    ]


def resolve_image(record: dict[str, object]) -> Path:
    value = record.get("image")
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {record.get('case_id')!r} has no image path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def write_predictions(path: Path, predictions: Iterable[dict[str, object]]) -> None:
    payload = "".join(
        json.dumps(prediction, ensure_ascii=False, separators=(",", ":")) + "\n"
        for prediction in predictions
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)


def predict_manifest(
    manifest: Path,
    checkpoint: Path,
    output: Path,
    *,
    tile_size: int,
    overlap: int,
    threshold: float,
    nms_iou: float,
) -> None:
    records = read_jsonl(manifest)
    expected_ids = [str(record.get("case_id", "")) for record in records]
    if not all(expected_ids) or len(expected_ids) != len(set(expected_ids)):
        raise ValueError("manifest case_ids must be non-empty and unique")
    completed: dict[str, dict[str, object]] = {}
    if output.exists():
        completed = {
            str(record.get("case_id", "")): record for record in read_jsonl(output)
        }
        unknown = sorted(set(completed) - set(expected_ids))
        if unknown:
            raise ValueError(f"output contains unknown case_ids: {unknown[:5]}")

    detector = load_detector(checkpoint)
    source_sha256 = sha256_file(Path(__file__))
    checkpoint_sha256 = sha256_file(checkpoint)
    fingerprint_payload = json.dumps(
        {
            "source_sha256": source_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "tile_size": tile_size,
            "overlap": overlap,
            "threshold": threshold,
            "nms_iou": nms_iou,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    fingerprint = hashlib.sha256(fingerprint_payload).hexdigest()

    for index, record in enumerate(records, start=1):
        case_id = str(record["case_id"])
        if case_id in completed:
            continue
        path = resolve_image(record)
        image = cv2.imdecode(
            np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            raise ValueError(f"cannot decode benchmark image: {path}")
        started = time.perf_counter()
        characters = predict_page(
            detector,
            image,
            tile_size=tile_size,
            overlap=overlap,
            threshold=threshold,
            nms_iou=nms_iou,
        )
        completed[case_id] = {
            "case_id": case_id,
            "characters": characters,
            "candidate_id": "rf_detr/one-class-tiled-v1",
            "candidate_fingerprint": fingerprint,
            "checkpoint_sha256": checkpoint_sha256,
            "latency_seconds": round(time.perf_counter() - started, 6),
            "tile_size": tile_size,
            "overlap": overlap,
            "threshold": threshold,
            "nms_iou": nms_iou,
        }
        write_predictions(
            output, (completed[value] for value in expected_ids if value in completed)
        )
        print(f"{index}/{len(records)} {case_id}: {len(characters)} boxes", flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--dataset", type=Path, required=True)
    train_parser.add_argument("--out", type=Path, required=True)
    train_parser.add_argument("--epochs", type=int, default=5)
    train_parser.add_argument("--device", default="cuda")
    train_parser.add_argument("--seed", type=int, required=True)
    train_parser.add_argument("--candidate-id", required=True)
    train_parser.add_argument("--lr-drop", type=int)
    train_parser.add_argument("--brightness-contrast", action="store_true")

    predict_parser = commands.add_parser("predict")
    predict_parser.add_argument("--manifest", type=Path, required=True)
    predict_parser.add_argument("--checkpoint", type=Path, required=True)
    predict_parser.add_argument("--out", type=Path, required=True)
    predict_parser.add_argument("--tile-size", type=int, default=DEFAULT_TILE_SIZE)
    predict_parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    predict_parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    predict_parser.add_argument("--nms-iou", type=float, default=DEFAULT_NMS_IOU)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "train":
        if args.epochs <= 0:
            raise SystemExit("--epochs must be positive")
        if args.seed < 0:
            raise SystemExit("--seed must be non-negative")
        if args.lr_drop is not None and args.lr_drop <= 0:
            raise SystemExit("--lr-drop must be positive")
        if not args.candidate_id.strip():
            raise SystemExit("--candidate-id must be non-empty")
        train(
            args.dataset.resolve(),
            args.out.resolve(),
            epochs=args.epochs,
            device=args.device,
            seed=args.seed,
            candidate_id=args.candidate_id,
            lr_drop=args.lr_drop,
            brightness_contrast=args.brightness_contrast,
        )
        return 0
    if args.tile_size <= 0:
        raise SystemExit("--tile-size must be positive")
    if args.overlap < 0 or args.overlap >= args.tile_size:
        raise SystemExit("--overlap must be non-negative and smaller than --tile-size")
    if not 0.0 <= args.threshold <= 1.0 or not 0.0 <= args.nms_iou <= 1.0:
        raise SystemExit("--threshold and --nms-iou must be between zero and one")
    predict_manifest(
        args.manifest.resolve(),
        args.checkpoint.resolve(),
        args.out.resolve(),
        tile_size=args.tile_size,
        overlap=args.overlap,
        threshold=args.threshold,
        nms_iou=args.nms_iou,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
