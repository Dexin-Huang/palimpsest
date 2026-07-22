"""Generate the frozen repertoire from adapted and causal-control writer latents."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
BENCHMARK_PATH = OUT / "benchmark.json"
ADAPTATION_PATH = OUT / "adaptation.json"
CANDIDATE_PATH = ROOT / "experiments" / "scribe_template_retrieval" / "candidate.py"
TEMPLATE_DIR = OUT / "templates"
BATCH_SIZE = 16
SEED = 3477
CONDITIONS = ("p3477_adapted", "p3477_unadapted", "wrong_writer_adapted")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def load_candidate():
    spec = importlib.util.spec_from_file_location("font_generation_candidate", CANDIDATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {CANDIDATE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def save_tensor(tensor, path: Path) -> None:
    array = tensor.detach().cpu().clamp(-1, 1).add(1).div(2).mul(255).numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.uint8(np.clip(array.squeeze(), 0, 255)), mode="L").save(path)


def load_adapter(record: dict, condition: str, torch, device) -> dict:
    identity = record["adapters"][condition]
    path = resolve(identity["path"])
    if sha256(path) != identity["sha256"]:
        raise RuntimeError(f"Adapter hash mismatch: {path}")
    return torch.load(path, map_location=device, weights_only=True)


def generator_for_adapter(candidate, payload: dict, torch, device):
    generator = candidate.load_generator(torch).to(device)
    model_state = payload.get("model_state", {})
    if model_state:
        _, unexpected = generator.load_state_dict(model_state, strict=False)
        if unexpected:
            raise RuntimeError(f"Unexpected adapted model keys: {unexpected}")
    generator.eval()
    return generator


def generate_condition(
    generator,
    characters: list[str],
    style: dict,
    condition: str,
    candidate,
    torch,
    device,
) -> list[dict]:
    records = []
    style = {key: value.to(device) for key, value in style.items()}
    for offset in range(0, len(characters), BATCH_SIZE):
        batch = characters[offset : offset + BATCH_SIZE]
        content_images = [
            candidate.render_character(character, candidate.SOURCE_FONT)
            for character in batch
        ]
        content_tensor = candidate.to_tensor(content_images, torch).to(device)
        with torch.inference_mode():
            content = generator.factorize(generator.encode(content_tensor), 1)
            expanded_style = {
                key: value.expand(len(batch), *value.shape[1:])
                for key, value in style.items()
            }
            output = generator.decode(
                generator.defactorize([expanded_style, content])
            )
        for character, image in zip(batch, output):
            path = TEMPLATE_DIR / condition / f"U{ord(character):05X}.png"
            save_tensor(image, path)
            records.append(
                {
                    "character": character,
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(path),
                }
            )
        print(
            f"{condition} {min(offset + len(batch), len(characters))}/{len(characters)}",
            flush=True,
        )
    return records


def generate_kai(characters: list[str], candidate) -> list[dict]:
    records = []
    for character in characters:
        path = TEMPLATE_DIR / "kai" / f"U{ord(character):05X}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(
            candidate.render_character(character, candidate.SOURCE_FONT), mode="L"
        ).save(path)
        records.append(
            {
                "character": character,
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256(path),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()

    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is the default and was requested, but this Torch build cannot use it"
        )
    device = torch.device(args.device)
    torch.manual_seed(SEED)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(SEED)

    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    adaptation = json.loads(ADAPTATION_PATH.read_text(encoding="utf-8"))
    if adaptation["benchmark_sha256"] != sha256(BENCHMARK_PATH):
        raise RuntimeError("Adaptation record does not match the frozen benchmark")
    characters = benchmark["output_repertoire"]
    candidate = load_candidate()

    p3477 = load_adapter(adaptation, "p3477", torch, device)
    wrong = load_adapter(adaptation, "wrong_writer", torch, device)
    conditions = (
        (
            "p3477_adapted",
            generator_for_adapter(candidate, p3477, torch, device),
            p3477["style_factors"],
        ),
        (
            "p3477_unadapted",
            candidate.load_generator(torch).to(device).eval(),
            p3477["initial_style_factors"],
        ),
        (
            "wrong_writer_adapted",
            generator_for_adapter(candidate, wrong, torch, device),
            wrong["style_factors"],
        ),
    )
    outputs = {}
    for condition, generator, style in conditions:
        outputs[condition] = generate_condition(
            generator,
            characters,
            style,
            condition,
            candidate,
            torch,
            device,
        )
    outputs["kai"] = generate_kai(characters, candidate)

    record = {
        "schema_version": 1,
        "experiment": benchmark["experiment"],
        "model": adaptation["model"],
        "device": str(device),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(device)
        if device.type == "cuda"
        else "cpu",
        "benchmark_sha256": sha256(BENCHMARK_PATH),
        "adaptation_sha256": sha256(ADAPTATION_PATH),
        "checkpoint_sha256": adaptation["checkpoint_sha256"],
        "conditions": list(CONDITIONS) + ["kai"],
        "generated_pixels_are_documentary_evidence": False,
        "outputs": outputs,
    }
    path = OUT / "generation.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generation record: {path}")


if __name__ == "__main__":
    main()
