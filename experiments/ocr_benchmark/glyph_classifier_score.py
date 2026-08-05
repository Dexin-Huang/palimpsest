"""Score a glyph checkpoint on the local-only adjudication pack."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

import torch
from PIL import Image

from glyph_classifier_train import build_model, image_transform

SEED = 361004


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def accuracy(rows: list[dict[str, object]], field: str) -> float:
    return sum(bool(row[field]) for row in rows) / len(rows)


def grouped_metrics(rows: list[dict[str, object]], key: str) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {
        name: {
            "instances": len(group),
            "classifier_accuracy": accuracy(group, "classifier_correct"),
            "gemini_accuracy": accuracy(group, "gemini_correct"),
            "delta": accuracy(group, "classifier_correct")
            - accuracy(group, "gemini_correct"),
        }
        for name, group in sorted(groups.items())
    }


def paired_bootstrap(
    rows: list[dict[str, object]], *, samples: int, seed: int
) -> list[float]:
    rng = random.Random(seed)
    differences = [
        int(bool(row["classifier_correct"])) - int(bool(row["gemini_correct"]))
        for row in rows
    ]
    means = []
    for _ in range(samples):
        means.append(
            sum(rng.choice(differences) for _ in differences) / len(differences)
        )
    means.sort()
    return [means[round(0.025 * (samples - 1))], means[round(0.975 * (samples - 1))]]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--eval-tar", type=Path, required=True)
    parser.add_argument("--eval-manifest", type=Path, required=True)
    parser.add_argument("--gemini-adjudications", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--minimum-coverage", type=float, default=0.95)
    parser.add_argument("--minimum-accuracy", type=float, default=0.85)
    parser.add_argument("--minimum-control-accuracy", type=float, default=0.95)
    parser.add_argument("--minimum-corpus-delta", type=float, default=-0.01)
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
    gemini = {
        row["instance_id"]: row
        for row in map(
            json.loads,
            args.gemini_adjudications.read_text(encoding="utf-8").splitlines(),
        )
        if not row.get("failed")
    }
    with tarfile.open(args.eval_tar, "r") as archive:
        payloads = {}
        for row in metadata:
            member = archive.extractfile(row["member"])
            if member is None:
                raise OSError(f"missing evaluation crop: {row['member']}")
            payloads[row["instance_id"]] = member.read()

    covered = []
    uncovered = []
    for row in metadata:
        gold_id = class_of.get(row["gold_char"])
        alternative_id = class_of.get(row["alternative"])
        adjudication = gemini.get(row["instance_id"])
        if gold_id is None or alternative_id is None or adjudication is None:
            uncovered.append(
                {
                    **row,
                    "gold_in_vocabulary": gold_id is not None,
                    "alternative_in_vocabulary": alternative_id is not None,
                    "gemini_available": adjudication is not None,
                }
            )
            continue
        covered.append(
            {
                **row,
                "gold_id": gold_id,
                "alternative_id": alternative_id,
                "gemini_correct": adjudication["chosen_char"] == row["gold_char"],
            }
        )

    with torch.inference_mode():
        for start in range(0, len(covered), args.batch_size):
            batch = covered[start : start + args.batch_size]
            images = []
            for row in batch:
                with Image.open(io.BytesIO(payloads[row["instance_id"]])) as image:
                    images.append(transform(image.convert("RGB")))
            logits = model(torch.stack(images).to(device))
            probabilities = logits.softmax(dim=1)
            top1 = logits.argmax(dim=1).cpu().tolist()
            for index, row in enumerate(batch):
                gold_score = float(logits[index, row["gold_id"]].cpu())
                alternative_score = float(logits[index, row["alternative_id"]].cpu())
                row["classifier_correct"] = gold_score > alternative_score
                row["pair_margin"] = gold_score - alternative_score
                row["gold_probability"] = float(
                    probabilities[index, row["gold_id"]].cpu()
                )
                row["alternative_probability"] = float(
                    probabilities[index, row["alternative_id"]].cpu()
                )
                row["top1_char"] = characters[top1[index]]
                row["top1_correct"] = top1[index] == row["gold_id"]

    coverage = len(covered) / len(metadata)
    classifier_accuracy = accuracy(covered, "classifier_correct")
    gemini_accuracy = accuracy(covered, "gemini_correct")
    corpus = grouped_metrics(covered, "corpus")
    kind = grouped_metrics(covered, "kind")
    gates = {
        "coverage": coverage >= args.minimum_coverage,
        "accuracy": classifier_accuracy >= args.minimum_accuracy,
        "control_accuracy": (
            "control" in kind
            and kind["control"]["classifier_accuracy"] >= args.minimum_control_accuracy
        ),
        "protected_corpora": all(
            values["delta"] >= args.minimum_corpus_delta for values in corpus.values()
        ),
    }
    report = {
        "schema_version": 1,
        "checkpoint": {
            "path": str(args.checkpoint),
            "sha256": sha256_file(args.checkpoint),
            "architecture": checkpoint["config"]["architecture"],
            "epoch": checkpoint["epoch"],
            "development": checkpoint["development"],
        },
        "evaluation": {
            "instances": len(metadata),
            "covered": len(covered),
            "coverage": coverage,
            "classifier_forced_choice_accuracy": classifier_accuracy,
            "gemini_forced_choice_accuracy_on_covered": gemini_accuracy,
            "paired_delta": classifier_accuracy - gemini_accuracy,
            "paired_bootstrap_95_ci": paired_bootstrap(
                covered, samples=args.bootstrap_samples, seed=SEED
            ),
            "classifier_top1_accuracy": accuracy(covered, "top1_correct"),
            "by_kind": kind,
            "by_corpus": corpus,
            "uncovered": len(uncovered),
        },
        "thresholds": {
            "minimum_coverage": args.minimum_coverage,
            "minimum_accuracy": args.minimum_accuracy,
            "minimum_control_accuracy": args.minimum_control_accuracy,
            "minimum_corpus_delta": args.minimum_corpus_delta,
        },
        "gates": gates,
        "decision": "accepted" if all(gates.values()) else "rejected",
    }
    prediction_path = args.output / "predictions.jsonl"
    prediction_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in covered),
        encoding="utf-8",
        newline="\n",
    )
    (args.output / "uncovered.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in uncovered),
        encoding="utf-8",
        newline="\n",
    )
    report["artifacts"] = {
        "predictions_sha256": sha256_file(prediction_path),
        "uncovered_sha256": sha256_file(args.output / "uncovered.jsonl"),
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
