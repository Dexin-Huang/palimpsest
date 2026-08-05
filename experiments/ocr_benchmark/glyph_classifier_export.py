"""Export an accepted glyph checkpoint to ONNX and verify it on real crops."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from glyph_classifier_train import build_model, image_transform


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--validation-tar", type=Path, required=True)
    parser.add_argument("--validation-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-samples", type=int, default=32)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    characters = checkpoint["characters"]
    model = build_model(
        checkpoint["config"]["architecture"], len(characters), pretrained=False
    )
    model.load_state_dict(checkpoint["ema_state"], strict=True)
    model.eval()
    onnx_path = args.output / "glyph_classifier.onnx"
    torch.onnx.export(
        model,
        (torch.zeros(1, 3, 64, 64),),
        onnx_path,
        export_params=True,
        opset_version=17,
        do_constant_folding=True,
        input_names=["image"],
        output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
        dynamo=False,
    )
    classes_path = args.output / "classes.json"
    classes_path.write_text(
        json.dumps(characters, ensure_ascii=False), encoding="utf-8", newline="\n"
    )

    rows = [
        json.loads(line)
        for line in args.validation_manifest.read_text(encoding="utf-8").splitlines()
    ][: args.validation_samples]
    transform = image_transform(training=False)
    tensors = []
    with tarfile.open(args.validation_tar, "r") as archive:
        for row in rows:
            member = archive.extractfile(row["member"])
            if member is None:
                raise OSError(f"missing validation crop: {row['member']}")
            with Image.open(io.BytesIO(member.read())) as image:
                tensors.append(transform(image.convert("RGB")))
    batch = torch.stack(tensors)
    with torch.inference_mode():
        torch_logits = model(batch).numpy()
    network = cv2.dnn.readNetFromONNX(str(onnx_path))
    network.setInput(np.asarray(batch.numpy(), dtype=np.float32))
    opencv_logits = np.asarray(network.forward(), dtype=np.float32)
    absolute = np.abs(torch_logits - opencv_logits)
    top1_agreement = float(
        np.mean(torch_logits.argmax(axis=1) == opencv_logits.argmax(axis=1))
    )
    report = {
        "schema_version": 1,
        "checkpoint_sha256": sha256_file(args.checkpoint),
        "onnx": {
            "sha256": sha256_file(onnx_path),
            "bytes": onnx_path.stat().st_size,
            "opset": 17,
            "dynamic_batch": True,
        },
        "classes": {
            "sha256": sha256_file(classes_path),
            "bytes": classes_path.stat().st_size,
            "count": len(characters),
        },
        "validation": {
            "samples": len(rows),
            "maximum_absolute_logit_difference": float(absolute.max()),
            "mean_absolute_logit_difference": float(absolute.mean()),
            "top1_agreement": top1_agreement,
        },
        "decision": (
            "accepted"
            if float(absolute.max()) <= 1e-3 and top1_agreement == 1.0
            else "rejected"
        ),
    }
    (args.output / "export_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(report, indent=2))
    return 0 if report["decision"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
