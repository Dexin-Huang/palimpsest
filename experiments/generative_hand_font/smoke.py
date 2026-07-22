"""Run a bounded, training-only P.3477 latent-adaptation smoke test.

This deliberately does not build or weaken the held-out benchmark. It asks only
whether eight fingerprinted human-accepted crops can move MX-Font's shared style
factors toward the observed writer and reduce reconstruction loss. The result is
development evidence, never qualification evidence or documentary reconstruction.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from palimpsest.image_labeling import resolve_recorded_path, sha256

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
DATASET_PATH = OUT / "annotation_dataset.json"
ADAPT_PATH = HERE / "adapt.py"
BENCHMARK_PATH = HERE / "benchmark.py"
RUN_ID = "human-latent-smoke-v1"
BUDGET = 8
DEFAULT_STEPS = 50
SEED = 3477


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_development_records(benchmark) -> tuple[dict, list[dict]]:
    annotation = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if (
        annotation.get("schema_version") != 1
        or annotation.get("kind") != "human_image_annotation_dataset"
        or annotation.get("project_id") != "p3477-generative-hand-font-crops"
        or not isinstance(annotation.get("records"), list)
        or annotation.get("queue_summaries", {}).get("writer_specimen", {}).get("ready")
        is not True
    ):
        raise RuntimeError("Need a ready human-reviewed writer-specimen queue")

    project_path = resolve_recorded_path(
        annotation["project_path"], DATASET_PATH.parent
    )
    if sha256(project_path) != annotation["project_sha256"]:
        raise RuntimeError("Annotation project fingerprint does not match dataset")
    project = json.loads(project_path.read_text(encoding="utf-8"))
    metadata = project["metadata"]
    proposal_path = resolve_recorded_path(metadata["proposal_path"], ROOT)
    if sha256(proposal_path) != metadata["proposal_sha256"]:
        raise RuntimeError("Crop proposal fingerprint does not match annotation")

    accepted = benchmark.gold_records(annotation, "writer_specimen")
    ordered = benchmark.specimen_order(accepted)
    if len(ordered) < BUDGET:
        raise RuntimeError(f"Need {BUDGET} distinct human-accepted characters")
    selected = [benchmark.frozen_record(record) for record in ordered[:BUDGET]]
    if len({record["character"] for record in selected}) != BUDGET:
        raise RuntimeError("Development smoke selection contains duplicate characters")
    return annotation, selected


def tensor_images(tensor) -> list[Image.Image]:
    arrays = tensor.detach().cpu().clamp(-1, 1).add(1).div(2).mul(255).numpy()
    return [Image.fromarray(np.uint8(array.squeeze()), mode="L") for array in arrays]


def generate(generator, candidate, style: dict, characters: list[str], device, torch):
    content_images = [
        candidate.render_character(character, candidate.SOURCE_FONT)
        for character in characters
    ]
    content_tensor = candidate.to_tensor(content_images, torch).to(device)
    with torch.no_grad():
        content = {
            key: value.detach()
            for key, value in generator.factorize(
                generator.encode(content_tensor), 1
            ).items()
        }
        indices = torch.arange(len(characters), device=device)
        output = generator.decode(
            generator.defactorize(
                [
                    {
                        key: value.to(device).expand(len(characters), *value.shape[1:])
                        for key, value in style.items()
                    },
                    {key: value[indices] for key, value in content.items()},
                ]
            )
        )
    return content_images, tensor_images(output)


def render_sheet(
    selected: list[dict],
    targets: list[np.ndarray],
    initial: list[Image.Image],
    adapted: list[Image.Image],
    unseen: list[str],
    unseen_content: list[np.ndarray],
    unseen_initial: list[Image.Image],
    unseen_adapted: list[Image.Image],
) -> Image.Image:
    scale = 1.35
    image_size = round(128 * scale)
    label_width = 150
    header_height = 58
    row_height = image_size + 34
    width = label_width + image_size * 3 + 64
    rows = len(selected) + len(unseen)
    sheet = Image.new("RGB", (width, header_height + rows * row_height), "white")
    draw = ImageDraw.Draw(sheet)
    cjk_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = (
        ImageFont.truetype(str(cjk_path), 24)
        if cjk_path.exists()
        else ImageFont.load_default()
    )
    small = (
        ImageFont.truetype(str(cjk_path), 15)
        if cjk_path.exists()
        else ImageFont.load_default()
    )
    headers = ("REFERENCE", "INITIAL STYLE", "ADAPTED STYLE")
    for column, header in enumerate(headers):
        draw.text(
            (label_width + column * image_size + 12, 18),
            header,
            fill="#25221e",
            font=small,
        )

    def add_row(
        index: int, label: str, kind: str, images: tuple[Image.Image, ...]
    ) -> None:
        y = header_height + index * row_height
        draw.text((16, y + 48), label, fill="#171512", font=font)
        draw.text((54, y + 53), kind, fill="#776e64", font=small)
        for column, image in enumerate(images):
            resized = image.resize((image_size, image_size), Image.Resampling.LANCZOS)
            sheet.paste(resized.convert("RGB"), (label_width + column * image_size, y))
        draw.line((0, y + row_height - 1, width, y + row_height - 1), fill="#ddd7ce")

    for index, (record, target, before, after) in enumerate(
        zip(selected, targets, initial, adapted, strict=True)
    ):
        add_row(
            index,
            record["character"],
            "human target",
            (Image.fromarray(target, mode="L"), before, after),
        )
    offset = len(selected)
    for index, (character, content, before, after) in enumerate(
        zip(unseen, unseen_content, unseen_initial, unseen_adapted, strict=True)
    ):
        add_row(
            offset + index,
            character,
            "unseen content",
            (Image.fromarray(content, mode="L"), before, after),
        )
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    if args.steps <= 0:
        raise ValueError("steps must be positive")

    run_dir = OUT / "runs" / RUN_ID
    if run_dir.exists():
        raise FileExistsError(f"Immutable smoke run already exists: {run_dir}")

    adapt = load_module("generative_hand_font_adapt_smoke", ADAPT_PATH)
    benchmark = load_module("generative_hand_font_benchmark_smoke", BENCHMARK_PATH)
    annotation, selected = load_development_records(benchmark)
    candidate = adapt.load_candidate()

    import torch

    torch.manual_seed(SEED)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA smoke test requested but unavailable")
    device = torch.device(args.device)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True

    generator = candidate.load_generator(torch).to(device)
    payload, summary = adapt.fit_condition(
        "p3477",
        selected,
        {"experiment": "generative-hand-font-v1"},
        generator,
        candidate,
        args.steps,
        "latent",
        device,
        torch,
    )

    selected_characters = [record["character"] for record in selected]
    generation_path = ROOT / "experiments/scribe_template_retrieval/out/generation.json"
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    unseen = [
        character
        for character in generation["candidate_characters"]
        if character not in set(selected_characters)
    ][:BUDGET]
    if len(unseen) != BUDGET:
        raise RuntimeError("Not enough unseen content characters for smoke rendering")

    targets = adapt.condition_images("p3477", selected, {}, candidate)
    _, initial = generate(
        generator,
        candidate,
        payload["initial_style_factors"],
        selected_characters,
        device,
        torch,
    )
    _, adapted = generate(
        generator,
        candidate,
        payload["style_factors"],
        selected_characters,
        device,
        torch,
    )
    unseen_content, unseen_initial = generate(
        generator,
        candidate,
        payload["initial_style_factors"],
        unseen,
        device,
        torch,
    )
    _, unseen_adapted = generate(
        generator,
        candidate,
        payload["style_factors"],
        unseen,
        device,
        torch,
    )

    run_dir.mkdir(parents=True)
    comparison_path = run_dir / "comparison.png"
    render_sheet(
        selected,
        targets,
        initial,
        adapted,
        unseen,
        unseen_content,
        unseen_initial,
        unseen_adapted,
    ).save(comparison_path)

    adapter_path = run_dir / "style-factors.pt"
    torch.save(
        {
            "schema_version": 1,
            "run_id": RUN_ID,
            "style_factors": payload["style_factors"],
            "initial_style_factors": payload["initial_style_factors"],
            "summary": summary,
        },
        adapter_path,
    )
    project_path = resolve_recorded_path(
        annotation["project_path"], DATASET_PATH.parent
    )
    record = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "experiment": "generative-hand-font-v1",
        "evidence_status": "human_accepted_training_only_development_smoke",
        "qualification_eligible": False,
        "held_out_evidence": False,
        "generated_pixels_are_documentary_evidence": False,
        "hypothesis": "Eight human-accepted writer crops can move frozen MX-Font style factors toward P.3477 and reduce training reconstruction loss.",
        "limitation": "Training-set reconstruction and qualitative unseen-content rendering only; this cannot establish generalization or writer identity.",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "budget": BUDGET,
        "steps": args.steps,
        "mode": "latent",
        "device": str(device),
        "device_name": torch.cuda.get_device_name(device)
        if device.type == "cuda"
        else "cpu",
        "source_records": {
            "annotation_dataset_path": DATASET_PATH.relative_to(ROOT).as_posix(),
            "annotation_dataset_sha256": sha256(DATASET_PATH),
            "annotation_project_path": project_path.relative_to(ROOT).as_posix(),
            "annotation_project_sha256": sha256(project_path),
            "proposal_sha256": annotation["metadata"]["proposal_sha256"],
            "adaptation_code_sha256": sha256(ADAPT_PATH),
            "candidate_code_sha256": sha256(adapt.CANDIDATE_PATH),
            "checkpoint_sha256": candidate.MX_WEIGHT_SHA256,
        },
        "selected_records": selected,
        "unseen_content_characters": unseen,
        "summary": summary,
        "artifacts": {
            "style_factors_path": adapter_path.relative_to(ROOT).as_posix(),
            "style_factors_sha256": sha256(adapter_path),
            "comparison_path": comparison_path.relative_to(ROOT).as_posix(),
            "comparison_sha256": sha256(comparison_path),
        },
    }
    record_path = run_dir / "record.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"record: {record_path}")
    print(f"comparison: {comparison_path}")


if __name__ == "__main__":
    main()
