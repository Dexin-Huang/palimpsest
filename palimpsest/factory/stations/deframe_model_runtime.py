"""Isolated inference runtime for development-only model-backed deframe variants.

The factory process deliberately does not import these optional model stacks.
A deframe station starts this module with the dedicated Python environment,
and this process returns only source-relative crop geometry as JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checkpoint(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing model checkpoint: {path}")
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Checkpoint hash mismatch for {path}: expected {expected_sha256}, "
            f"got {actual_sha256}"
        )


def _union_bbox(boxes: list[tuple[float, float, float, float]]) -> list[int]:
    if not boxes:
        raise RuntimeError("Model produced no manuscript-scale mask")
    return [
        round(min(box[0] for box in boxes)),
        round(min(box[1] for box in boxes)),
        round(max(box[2] for box in boxes)),
        round(max(box[3] for box in boxes)),
    ]


def _run_fastsam(args: argparse.Namespace) -> dict[str, object]:
    import torch
    import ultralytics
    from PIL import Image
    from ultralytics import FastSAM

    if ultralytics.__version__ != args.runtime_version:
        raise RuntimeError(
            f"Ultralytics version mismatch: expected {args.runtime_version}, "
            f"got {ultralytics.__version__}"
        )
    if torch.__version__ != args.torch_version:
        raise RuntimeError(
            f"PyTorch version mismatch: expected {args.torch_version}, "
            f"got {torch.__version__}"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("FastSAM development candidate requires CUDA")

    with Image.open(args.source) as image:
        width, height = image.size
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = FastSAM(str(args.checkpoint))
    loaded = time.perf_counter()
    result = model(
        str(args.source),
        device=0,
        imgsz=args.image_size,
        conf=args.confidence,
        iou=args.iou,
        retina_masks=False,
        verbose=False,
    )[0]
    finished = time.perf_counter()

    selected: list[tuple[float, float, float, float]] = []
    if result.boxes is not None:
        for coordinates, confidence in zip(
            result.boxes.xyxy.detach().cpu().tolist(),
            result.boxes.conf.detach().cpu().tolist(),
            strict=True,
        ):
            x0, y0, x1, y1 = coordinates
            box_fraction = max(0.0, x1 - x0) * max(0.0, y1 - y0) / (width * height)
            height_fraction = max(0.0, y1 - y0) / height
            if (
                confidence >= args.confidence
                and box_fraction >= args.min_box_fraction
                and height_fraction >= args.min_box_height_fraction
            ):
                selected.append((x0, y0, x1, y1))

    return {
        "bbox": _union_bbox(selected),
        "selected_masks": len(selected),
        "model_load_seconds": loaded - started,
        "inference_seconds": finished - loaded,
        "peak_vram_bytes": torch.cuda.max_memory_allocated(),
    }


def _run_efficientvit(args: argparse.Namespace) -> dict[str, object]:
    source_root = args.efficientvit_source.resolve()
    head_path = source_root / ".git" / "HEAD"
    if not head_path.is_file():
        raise FileNotFoundError(f"Missing EfficientViT source checkout: {source_root}")
    revision = head_path.read_text(encoding="utf-8").strip()
    if revision != args.source_revision:
        raise RuntimeError(
            f"EfficientViT revision mismatch: expected {args.source_revision}, "
            f"got {revision}"
        )
    sys.path.insert(0, str(source_root))

    import numpy as np
    import torch
    from PIL import Image
    from efficientvit.models.efficientvit.sam import (
        EfficientViTSamAutomaticMaskGenerator,
    )
    from efficientvit.sam_model_zoo import create_sam_model

    if torch.__version__ != args.torch_version:
        raise RuntimeError(
            f"PyTorch version mismatch: expected {args.torch_version}, "
            f"got {torch.__version__}"
        )
    if not torch.cuda.is_available():
        raise RuntimeError("EfficientViT development candidate requires CUDA")

    with Image.open(args.source) as image:
        rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = create_sam_model("l0", weight_url=str(args.checkpoint)).cuda().eval()
    loaded = time.perf_counter()
    generator = EfficientViTSamAutomaticMaskGenerator(
        model,
        points_per_side=args.points_per_side,
        points_per_batch=args.points_per_batch,
        pred_iou_thresh=args.predicted_iou_threshold,
        stability_score_thresh=args.stability_threshold,
        box_nms_thresh=args.box_nms_threshold,
        output_mode="uncompressed_rle",
    )
    masks = generator.generate(rgb)
    finished = time.perf_counter()

    selected: list[tuple[float, float, float, float]] = []
    for mask in masks:
        x, y, box_width, box_height = mask["bbox"]
        box_fraction = box_width * box_height / (width * height)
        mask_fraction = mask["area"] / (width * height)
        height_fraction = box_height / height
        if (
            box_fraction >= args.min_box_fraction
            and mask_fraction >= args.min_mask_fraction
            and height_fraction >= args.min_box_height_fraction
        ):
            selected.append((x, y, x + box_width, y + box_height))

    return {
        "bbox": _union_bbox(selected),
        "selected_masks": len(selected),
        "model_load_seconds": loaded - started,
        "inference_seconds": finished - loaded,
        "peak_vram_bytes": torch.cuda.max_memory_allocated(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=("fastsam-s", "efficientvit-sam-l0"))
    parser.add_argument("source", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("checkpoint_sha256")
    parser.add_argument("torch_version")
    parser.add_argument("--runtime-version")
    parser.add_argument("--efficientvit-source", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--image-size", type=int)
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--iou", type=float)
    parser.add_argument("--points-per-side", type=int)
    parser.add_argument("--points-per-batch", type=int)
    parser.add_argument("--predicted-iou-threshold", type=float)
    parser.add_argument("--stability-threshold", type=float)
    parser.add_argument("--box-nms-threshold", type=float)
    parser.add_argument("--min-box-fraction", type=float, required=True)
    parser.add_argument("--min-mask-fraction", type=float)
    parser.add_argument("--min-box-height-fraction", type=float, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    _verify_checkpoint(args.checkpoint, args.checkpoint_sha256)
    if args.variant == "fastsam-s":
        result = _run_fastsam(args)
    else:
        result = _run_efficientvit(args)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
