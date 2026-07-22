"""Fit P.3477 and control MX-Font writer adaptations from sparse specimens.

Latent-only adaptation freezes the generator. Decoder modes additionally fit
the explicitly selected reconstruction layers, while the unrelated-font
control keeps the adaptation mechanics identical across writer conditions.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from palimpsest.image_labeling import resolve_recorded_path, sha256


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
BENCHMARK_PATH = OUT / "benchmark.json"
RETRIEVAL = ROOT / "experiments" / "scribe_template_retrieval"
CANDIDATE_PATH = RETRIEVAL / "candidate.py"
SEED = 3477
BATCH_SIZE = 8
DEFAULT_BUDGET = 64
DEFAULT_STEPS = 600
DEFAULT_MODE = "late_decoder"
STYLE_LEARNING_RATE = 0.02
MODEL_LEARNING_RATE = 0.0002
INK_WEIGHT = 2.0
LATENT_WEIGHT = 0.002


def validate_benchmark(benchmark: dict, budget: int) -> None:
    source_records = benchmark.get("source_records")
    required_fingerprints = (
        "annotation_dataset_path",
        "annotation_dataset_sha256",
        "annotation_project_sha256",
        "proposal_sha256",
    )
    if (
        benchmark.get("schema_version") != 2
        or benchmark.get("evidence_status") != "human_attested_gold"
        or not isinstance(source_records, dict)
        or not isinstance(benchmark.get("specimen_budgets"), dict)
        or any(
            not isinstance(source_records.get(field), str)
            for field in required_fingerprints
        )
    ):
        raise RuntimeError(
            "Refusing adaptation without a schema-v2 human-annotated benchmark"
        )

    dataset_path = resolve_recorded_path(
        source_records["annotation_dataset_path"], ROOT
    )
    try:
        dataset_sha256 = sha256(dataset_path)
        annotation = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Refusing adaptation because the human annotation dataset is unavailable"
        ) from error
    if dataset_sha256 != source_records["annotation_dataset_sha256"]:
        raise RuntimeError(
            "Refusing adaptation because the human annotation fingerprint changed"
        )
    if (
        annotation.get("schema_version") != 1
        or annotation.get("kind") != "human_image_annotation_dataset"
        or annotation.get("project_id") != "p3477-generative-hand-font-crops"
        or annotation.get("status") != "human_attested_gold"
        or annotation.get("dataset_ready") is not True
        or not isinstance(annotation.get("records"), list)
    ):
        raise RuntimeError(
            "Refusing adaptation before the human annotation dataset is ready"
        )
    if (
        annotation.get("project_sha256") != source_records["annotation_project_sha256"]
        or annotation.get("metadata", {}).get("proposal_sha256")
        != source_records["proposal_sha256"]
    ):
        raise RuntimeError(
            "Refusing adaptation because benchmark provenance fingerprints disagree"
        )
    if str(budget) not in benchmark["specimen_budgets"]:
        available = ", ".join(benchmark["specimen_budgets"])
        raise ValueError(
            f"Specimen budget {budget} is unavailable; choose one of {available}"
        )

    accepted_by_id = {
        record.get("item_id"): record
        for record in annotation["records"]
        if isinstance(record, dict) and record.get("queue") == "writer_specimen"
    }
    for record in benchmark["specimen_budgets"][str(budget)]:
        source = (
            accepted_by_id.get(record.get("crop_id"))
            if isinstance(record, dict)
            else None
        )
        if (
            source is None
            or record.get("label_status") != "human_attested_gold"
            or record.get("character") != source.get("label")
            or record.get("crop_sha256") != source.get("accepted_image_sha256")
            or not isinstance(record.get("crop_path"), str)
            or not isinstance(source.get("accepted_image_path"), str)
        ):
            raise RuntimeError(
                "Refusing adaptation because a specimen is not fingerprinted human gold"
            )
        crop_path = resolve_recorded_path(record["crop_path"], ROOT)
        source_crop_path = resolve_recorded_path(
            source["accepted_image_path"], dataset_path.parent
        )
        try:
            crop_sha256 = sha256(crop_path)
        except OSError as error:
            raise RuntimeError(
                "Refusing adaptation because an attested specimen is unavailable"
            ) from error
        if crop_path != source_crop_path or crop_sha256 != record["crop_sha256"]:
            raise RuntimeError(
                "Refusing adaptation because an attested specimen fingerprint changed"
            )


def load_candidate():
    spec = importlib.util.spec_from_file_location("font_mx_candidate", CANDIDATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {CANDIDATE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_gray(path: Path, canvas: int) -> np.ndarray:
    return np.asarray(
        Image.open(path).convert("L").resize((canvas, canvas), Image.Resampling.LANCZOS)
    )


def clean_writer_image(gray: np.ndarray) -> np.ndarray:
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    areas = [(label, int(stats[label, cv2.CC_STAT_AREA])) for label in range(1, count)]
    total_area = sum(area for _, area in areas)
    retained = np.zeros_like(ink)
    accumulated = 0
    for label, area in sorted(areas, key=lambda item: (-item[1], item[0])):
        if area < 8:
            continue
        if accumulated >= 0.985 * total_area and area < 20:
            continue
        retained[labels == label] = 255
        accumulated += area
    retained = cv2.morphologyEx(retained, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    return 255 - retained


def condition_images(
    condition: str,
    records: list[dict],
    benchmark: dict,
    candidate,
) -> list[np.ndarray]:
    if condition == "p3477":
        return [
            clean_writer_image(
                load_gray(
                    resolve_recorded_path(record["crop_path"], ROOT), candidate.CANVAS
                )
            )
            for record in records
        ]
    if condition == "wrong_writer":
        font_path = resolve_recorded_path(
            benchmark["wrong_writer_control"]["font_path"], ROOT
        )
        return [
            candidate.render_character(record["character"], font_path)
            for record in records
        ]
    raise ValueError(f"Unknown adaptation condition: {condition}")


def weighted_reconstruction_loss(output, target, initial, adapted, torch):
    import torch.nn.functional as functional

    ink = (target < 0.5).to(target.dtype)
    weight = 1.0 + INK_WEIGHT * ink
    pixel = ((output - target).abs() * weight).sum() / weight.sum()
    coarse = functional.l1_loss(
        functional.avg_pool2d(output, 4), functional.avg_pool2d(target, 4)
    )
    latent = sum((adapted[key] - initial[key]).square().mean() for key in adapted)
    return pixel + 0.5 * coarse + LATENT_WEIGHT * latent


def generate_batch(generator, style, content, indices, torch):
    batch_content = {key: value[indices] for key, value in content.items()}
    expanded_style = {
        key: value.expand(len(indices), *value.shape[1:])
        for key, value in style.items()
    }
    return generator.decode(generator.defactorize([expanded_style, batch_content]))


def mean_loss(generator, style, content, targets, torch) -> float:
    losses = []
    initial = {key: value.detach() for key, value in style.items()}
    with torch.no_grad():
        for offset in range(0, len(targets), BATCH_SIZE):
            indices = torch.arange(
                offset,
                min(offset + BATCH_SIZE, len(targets)),
                device=targets.device,
            )
            output = generate_batch(generator, style, content, indices, torch)
            losses.append(
                float(
                    weighted_reconstruction_loss(
                        output, targets[indices], initial, style, torch
                    ).item()
                )
            )
    return float(np.mean(losses))


def fit_condition(
    condition: str,
    records: list[dict],
    benchmark: dict,
    generator,
    candidate,
    steps: int,
    mode: str,
    device,
    torch,
) -> tuple[dict, dict]:
    characters = [record["character"] for record in records]
    target_images = condition_images(condition, records, benchmark, candidate)
    content_images = [
        candidate.render_character(character, candidate.SOURCE_FONT)
        for character in characters
    ]
    target_tensor = candidate.to_tensor(target_images, torch).to(device)
    content_tensor = candidate.to_tensor(content_images, torch).to(device)

    with torch.no_grad():
        style_encoded = generator.factorize(generator.encode(target_tensor), 0)
        initial = {
            key: value.mean(0, keepdim=True).detach().clone()
            for key, value in style_encoded.items()
        }
        content = {
            key: value.detach()
            for key, value in generator.factorize(
                generator.encode(content_tensor), 1
            ).items()
        }

    for parameter in generator.parameters():
        parameter.requires_grad_(False)
    model_parameters = []
    if mode == "decoder":
        for module in (generator.recon_blocks, generator.decoder):
            for parameter in module.parameters():
                parameter.requires_grad_(True)
                model_parameters.append(parameter)
    elif mode == "late_decoder":
        modules = [
            generator.decoder.skip_layer,
            *generator.decoder.layers[5:],
        ]
        for module in modules:
            for parameter in module.parameters():
                parameter.requires_grad_(True)
                model_parameters.append(parameter)

    adapted = {key: torch.nn.Parameter(value.clone()) for key, value in initial.items()}
    initial_loss = mean_loss(generator, initial, content, target_tensor, torch)
    random = torch.Generator().manual_seed(SEED)
    parameter_groups = [{"params": list(adapted.values()), "lr": STYLE_LEARNING_RATE}]
    if model_parameters:
        parameter_groups.append({"params": model_parameters, "lr": MODEL_LEARNING_RATE})
    optimizer = torch.optim.Adam(parameter_groups)

    for step in range(steps):
        if step % max(1, len(records) // BATCH_SIZE) == 0:
            order = torch.randperm(len(records), generator=random)
        start = (step * BATCH_SIZE) % len(records)
        indices = order[start : start + BATCH_SIZE]
        if len(indices) < BATCH_SIZE:
            indices = torch.cat([indices, order[: BATCH_SIZE - len(indices)]])
        indices = indices.to(device)
        output = generate_batch(generator, adapted, content, indices, torch)
        loss = weighted_reconstruction_loss(
            output, target_tensor[indices], initial, adapted, torch
        )
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if (step + 1) % 50 == 0 or step == 0 or step + 1 == steps:
            print(
                f"{condition} step {step + 1}/{steps} loss={loss.item():.6f}",
                flush=True,
            )

    final_loss = mean_loss(generator, adapted, content, target_tensor, torch)
    final = {key: value.detach().cpu() for key, value in adapted.items()}
    summary = {
        "condition": condition,
        "characters": characters,
        "crop_ids": [record["crop_id"] for record in records],
        "steps": steps,
        "mode": mode,
        "style_learning_rate": STYLE_LEARNING_RATE,
        "model_learning_rate": MODEL_LEARNING_RATE if model_parameters else None,
        "batch_size": BATCH_SIZE,
        "initial_reconstruction_loss": initial_loss,
        "final_reconstruction_loss": final_loss,
        "relative_improvement": (initial_loss - final_loss) / initial_loss,
    }
    model_state = {
        name: value.detach().cpu()
        for name, value in generator.state_dict().items()
        if name.startswith("recon_blocks.") or name.startswith("decoder.")
    }
    payload = {
        "schema_version": 1,
        "experiment": benchmark["experiment"],
        "condition": condition,
        "style_factors": final,
        "mode": mode,
        "model_state": model_state,
        "initial_style_factors": {
            key: value.detach().cpu() for key, value in initial.items()
        },
        "summary": summary,
    }
    return payload, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument(
        "--mode",
        choices=("latent", "decoder", "late_decoder"),
        default=DEFAULT_MODE,
    )
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    if args.budget not in (8, 16, 32, 64):
        raise ValueError("budget must be one of 8, 16, 32, or 64")
    if args.steps <= 0:
        raise ValueError("steps must be positive")

    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    validate_benchmark(benchmark, args.budget)

    import torch

    torch.manual_seed(SEED)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is the default and was requested, but this Torch build cannot use it"
        )
    device = torch.device(args.device)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    records = benchmark["specimen_budgets"][str(args.budget)]
    candidate = load_candidate()

    adapter_dir = OUT / "adapters"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    paths = {}
    for condition in ("p3477", "wrong_writer"):
        generator = candidate.load_generator(torch).to(device)
        payload, summary = fit_condition(
            condition,
            records,
            benchmark,
            generator,
            candidate,
            args.steps,
            args.mode,
            device,
            torch,
        )
        path = (
            adapter_dir
            / f"{condition}-{args.mode}-budget-{args.budget}-steps-{args.steps}.pt"
        )
        torch.save(payload, path)
        summaries[condition] = summary
        paths[condition] = {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256(path),
        }

    record = {
        "schema_version": 1,
        "experiment": benchmark["experiment"],
        "mode": args.mode,
        "model": (
            "MX-Font writer adaptation with optimized shared style factors"
            + (
                " and decoder/reconstruction weights"
                if args.mode == "decoder"
                else " and high-resolution decoder weights"
                if args.mode == "late_decoder"
                else ""
            )
        ),
        "steps": args.steps,
        "seed": SEED,
        "device": str(device),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(device)
        if device.type == "cuda"
        else "cpu",
        "benchmark_sha256": sha256(BENCHMARK_PATH),
        "checkpoint_sha256": benchmark["pretrained_baseline"]["checkpoint_sha256"],
        "target_identity_leakage": False,
        "generated_pixels_are_documentary_evidence": False,
        "target_preprocessing": (
            "Otsu ink mask; tiny residual component removal; 2x2 closing"
        ),
        "adapters": paths,
        "summaries": summaries,
    }
    record_path = OUT / "adaptation.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"adaptation record: {record_path}")


if __name__ == "__main__":
    main()
