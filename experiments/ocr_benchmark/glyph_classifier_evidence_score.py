"""Evaluate a frozen glyph classifier as a top-k visual evidence channel."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

import torch
from PIL import Image

from glyph_classifier_train import build_model, image_transform


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def grouped_metrics(rows: list[dict[str, object]], key: str) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {
        name: {
            "instances": len(group),
            "top1": sum(bool(row["top1_correct"]) for row in group) / len(group),
            "top5": sum(bool(row["top5_correct"]) for row in group) / len(group),
        }
        for name, group in sorted(groups.items())
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--eval-tar", type=Path, required=True)
    parser.add_argument("--eval-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--minimum-coverage", type=float, default=0.98)
    parser.add_argument("--minimum-top1", type=float, default=0.90)
    parser.add_argument("--minimum-top5", type=float, default=0.97)
    parser.add_argument("--minimum-corpus-top1", type=float, default=0.85)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    characters = checkpoint["characters"]
    class_of = {character: index for index, character in enumerate(characters)}
    model = build_model(
        checkpoint["config"]["architecture"], len(characters), pretrained=False
    )
    model.load_state_dict(checkpoint["ema_state"], strict=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    transform = image_transform(training=False)

    metadata = [
        json.loads(line)
        for line in args.eval_manifest.read_text(encoding="utf-8").splitlines()
    ]
    eligible = []
    ineligible = []
    for row in metadata:
        gold_id = class_of.get(row["gold_char"])
        if gold_id is None:
            ineligible.append(row)
            continue
        eligible.append(
            {
                **row,
                "gold_id": gold_id,
                "alternative_id": class_of.get(row["alternative"]),
            }
        )
    with tarfile.open(args.eval_tar, "r") as archive, torch.inference_mode():
        for start in range(0, len(eligible), args.batch_size):
            batch = eligible[start : start + args.batch_size]
            images = []
            for row in batch:
                member = archive.extractfile(row["member"])
                if member is None:
                    raise OSError(f"missing evaluation crop: {row['member']}")
                with Image.open(io.BytesIO(member.read())) as image:
                    images.append(transform(image.convert("RGB")))
            logits = model(torch.stack(images).to(device))
            probabilities = logits.softmax(dim=1)
            top_indices = logits.topk(5, dim=1).indices.cpu().tolist()
            for index, row in enumerate(batch):
                gold_id = row["gold_id"]
                row["top1_char"] = characters[top_indices[index][0]]
                row["top5_chars"] = [characters[item] for item in top_indices[index]]
                row["top1_correct"] = top_indices[index][0] == gold_id
                row["top5_correct"] = gold_id in top_indices[index]
                row["gold_probability"] = float(probabilities[index, gold_id].cpu())
                alternative_id = row["alternative_id"]
                row["pairwise_correct"] = (
                    None
                    if alternative_id is None
                    else bool(logits[index, gold_id] > logits[index, alternative_id])
                )

    coverage = len(eligible) / len(metadata)
    top1 = sum(bool(row["top1_correct"]) for row in eligible) / len(eligible)
    top5 = sum(bool(row["top5_correct"]) for row in eligible) / len(eligible)
    pairwise = [row for row in eligible if row["pairwise_correct"] is not None]
    corpus = grouped_metrics(eligible, "corpus")
    kind = grouped_metrics(eligible, "kind")
    gates = {
        "coverage": coverage >= args.minimum_coverage,
        "top1": top1 >= args.minimum_top1,
        "top5": top5 >= args.minimum_top5,
        "protected_corpora": all(
            values["top1"] >= args.minimum_corpus_top1 for values in corpus.values()
        ),
    }
    predictions_path = args.output / "predictions.jsonl"
    predictions_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in eligible),
        encoding="utf-8",
        newline="\n",
    )
    ineligible_path = args.output / "ineligible.jsonl"
    ineligible_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ineligible),
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "schema_version": 1,
        "preflight": "experiments/ocr_benchmark/glyph_classifier_evidence_preflight_v1.json",
        "checkpoint": {
            "sha256": sha256_file(args.checkpoint),
            "architecture": checkpoint["config"]["architecture"],
            "epoch": checkpoint["epoch"],
            "development": checkpoint["development"],
        },
        "evaluation": {
            "instances": len(metadata),
            "eligible": len(eligible),
            "coverage": coverage,
            "top1": top1,
            "top5": top5,
            "by_kind": kind,
            "by_corpus": corpus,
            "pairwise_instances": len(pairwise),
            "pairwise_accuracy": sum(bool(row["pairwise_correct"]) for row in pairwise)
            / len(pairwise),
            "ineligible": len(ineligible),
        },
        "thresholds": {
            "minimum_coverage": args.minimum_coverage,
            "minimum_top1": args.minimum_top1,
            "minimum_top5": args.minimum_top5,
            "minimum_corpus_top1": args.minimum_corpus_top1,
        },
        "gates": gates,
        "decision": "accepted" if all(gates.values()) else "rejected",
        "artifacts": {
            "predictions_sha256": sha256_file(predictions_path),
            "ineligible_sha256": sha256_file(ineligible_path),
        },
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
