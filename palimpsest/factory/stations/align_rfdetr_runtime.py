"""Isolated tiled RF-DETR inference for the development align variant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
import socket
import sys
import time
from importlib import metadata
from pathlib import Path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_checkpoint(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing RF-DETR checkpoint: {path}")
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Checkpoint hash mismatch for {path}: expected {expected_sha256}, "
            f"got {actual_sha256}"
        )


def _require_package_version(package: str, expected: str) -> None:
    try:
        actual = metadata.version(package)
    except metadata.PackageNotFoundError:
        raise RuntimeError(f"Required package is not installed: {package}") from None
    if actual != expected:
        raise RuntimeError(
            f"{package} version mismatch: expected {expected}, got {actual}"
        )


def _tile_origins(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    stride = tile_size - overlap
    origins = list(range(0, length - tile_size + 1, stride))
    final_origin = length - tile_size
    if origins[-1] != final_origin:
        origins.append(final_origin)
    return origins


def _box_iou(box, boxes):
    import numpy as np

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
        intersection,
        union,
        out=np.zeros_like(intersection),
        where=union > 0,
    )


def _non_maximum_suppression(boxes, scores, iou_threshold: float) -> list[int]:
    import numpy as np

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
        order = remaining[_box_iou(boxes[current], boxes[remaining]) <= iou_threshold]
    return kept


def _load_detector(args: argparse.Namespace):
    import torch
    from rfdetr import RFDETR

    if torch.__version__ != args.torch_version:
        raise RuntimeError(
            f"torch version mismatch: expected {args.torch_version}, "
            f"got {torch.__version__}"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("RF-DETR alignment requires CUDA")
    started = time.perf_counter()
    detector = RFDETR.from_checkpoint(str(args.checkpoint))
    return detector, time.perf_counter() - started


def _predict(
    args: argparse.Namespace,
    *,
    detector=None,
    model_load_seconds: float | None = None,
    source: Path | None = None,
) -> dict[str, object]:
    import cv2
    import numpy as np
    import torch

    source = args.source if source is None else source
    if source is None:
        raise ValueError("RF-DETR inference requires a source image")
    if detector is None:
        detector, model_load_seconds = _load_detector(args)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot decode page image: {source}")
    height, width = image.shape[:2]

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    page_boxes: list[list[float]] = []
    page_scores: list[float] = []
    for y0 in _tile_origins(height, args.tile_size, args.overlap):
        for x0 in _tile_origins(width, args.tile_size, args.overlap):
            tile = cv2.cvtColor(
                image[
                    y0 : min(height, y0 + args.tile_size),
                    x0 : min(width, x0 + args.tile_size),
                ],
                cv2.COLOR_BGR2RGB,
            )
            detections = detector.predict(
                tile,
                threshold=args.threshold,
                include_source_image=False,
            )
            for xyxy, confidence in zip(
                detections.xyxy,
                detections.confidence,
                strict=True,
            ):
                left, top, right, bottom = (float(value) for value in xyxy)
                left = min(max(left + x0, 0.0), float(width))
                top = min(max(top + y0, 0.0), float(height))
                right = min(max(right + x0, 0.0), float(width))
                bottom = min(max(bottom + y0, 0.0), float(height))
                if (
                    not all(
                        math.isfinite(value)
                        for value in (left, top, right, bottom, confidence)
                    )
                    or right <= left
                    or bottom <= top
                ):
                    continue
                page_boxes.append([left, top, right, bottom])
                page_scores.append(float(confidence))
    inferred = time.perf_counter()

    if page_boxes:
        boxes = np.asarray(page_boxes, dtype=np.float32)
        scores = np.asarray(page_scores, dtype=np.float32)
        kept = _non_maximum_suppression(boxes, scores, args.nms_iou)
        output_boxes = [
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
    else:
        output_boxes = []

    return {
        "boxes": output_boxes,
        "image_size": [width, height],
        "model_load_seconds": float(model_load_seconds or 0.0),
        "inference_seconds": inferred - started,
        "peak_vram_bytes": torch.cuda.max_memory_allocated(),
    }


def _serve(args: argparse.Namespace) -> None:
    if args.state_path is None or args.token is None:
        raise ValueError("server mode requires --state-path and --token")
    if args.idle_timeout <= 0:
        raise ValueError("idle timeout must be positive")

    detector, model_load_seconds = _load_detector(args)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(1.0)
    port = int(server.getsockname()[1])
    state = {"pid": os.getpid(), "port": port, "token": args.token}
    args.state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_state = args.state_path.with_name(
        f"{args.state_path.name}.{os.getpid()}.tmp"
    )
    temporary_state.write_text(
        json.dumps(state, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_state, args.state_path)

    last_request = time.monotonic()
    try:
        while time.monotonic() - last_request < args.idle_timeout:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(float(args.idle_timeout))
                with connection.makefile("rwb") as stream:
                    try:
                        line = stream.readline(1024 * 1024)
                        request = json.loads(line)
                        if (
                            not isinstance(request, dict)
                            or set(request) != {"token", "source"}
                            or request["token"] != args.token
                            or not isinstance(request["source"], str)
                        ):
                            raise ValueError("invalid RF-DETR worker request")
                        response = _predict(
                            args,
                            detector=detector,
                            model_load_seconds=model_load_seconds,
                            source=Path(request["source"]),
                        )
                    except Exception as error:
                        response = {"error": f"{type(error).__name__}: {error}"}
                    stream.write(
                        (json.dumps(response, separators=(",", ":")) + "\n").encode(
                            "utf-8"
                        )
                    )
                    stream.flush()
            last_request = time.monotonic()
    finally:
        server.close()
        try:
            current = json.loads(args.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict) and current.get("token") == args.token:
            args.state_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("checkpoint_sha256")
    parser.add_argument("rfdetr_version")
    parser.add_argument("torch_version")
    parser.add_argument("torchvision_version")
    parser.add_argument("--tile-size", type=int, required=True)
    parser.add_argument("--overlap", type=int, required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--nms-iou", type=float, required=True)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--token")
    parser.add_argument("--idle-timeout", type=int, default=120)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.tile_size <= 0:
        raise ValueError("tile size must be positive")
    if args.overlap < 0 or args.overlap >= args.tile_size:
        raise ValueError("overlap must be non-negative and smaller than tile size")
    if not 0.0 <= args.threshold <= 1.0 or not 0.0 <= args.nms_iou <= 1.0:
        raise ValueError("threshold and NMS IoU must be between zero and one")
    _verify_checkpoint(args.checkpoint, args.checkpoint_sha256)
    _require_package_version("rfdetr", args.rfdetr_version)
    _require_package_version("torchvision", args.torchvision_version)
    if args.serve:
        _serve(args)
    else:
        result = _predict(args)
        print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
