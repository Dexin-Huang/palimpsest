"""Generate frozen MX-Font hypotheses for the P.3477 retrieval experiment.

Three conditions use the same content glyph and model checkpoint:

* correct_writer: four source-ink crops from the reference page;
* no_writer: the same reference characters rendered in generic Kai;
* wrong_writer: the same reference characters rendered in an unrelated font.

Generated images are hypotheses only. The manifest records their model and source
identities and never promotes them to labels or documentary evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parents[2]
HERE = Path(__file__).parent
OUT = HERE / "out"
MANIFEST_PATH = OUT / "manifest.json"
TEMPLATE_DIR = OUT / "templates"
MX_ROOT = ROOT / "tmp" / "mxfont"
MX_WEIGHT = MX_ROOT / "generator.pth"
MX_COMMIT = "93f3c88517f7c904f16da6333adb2588dcdf3cce"
MX_WEIGHT_SHA256 = "dcbcb6438d9b1e3230551bc78fcf64ec5454a01734502bdeac410d2f5c404119"
SOURCE_FONT = Path("C:/Windows/Fonts/simkai.ttf")
WRONG_STYLE_FONT = MX_ROOT / "data" / "ttfs" / "train" / "MaShanZheng-Regular.ttf"
CANVAS = 128
N_REFERENCES = 4
BATCH_SIZE = 8
SEED = 3477
CONDITIONS = ("correct_writer", "no_writer", "wrong_writer")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_character(char: str, font_path: Path) -> np.ndarray:
    font = ImageFont.truetype(str(font_path), 112)
    image = Image.new("L", (CANVAS, CANVAS), 255)
    draw = ImageDraw.Draw(image)
    bounds = draw.textbbox((0, 0), char, font=font)
    x = (CANVAS - (bounds[2] - bounds[0])) // 2 - bounds[0]
    y = (CANVAS - (bounds[3] - bounds[1])) // 2 - bounds[1]
    draw.text((x, y), char, fill=0, font=font)
    return np.asarray(image)


def load_crop(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L").resize((CANVAS, CANVAS)))


def choose_references(records: list[dict], target: str) -> list[dict]:
    ordered = sorted(
        (record for record in records if record["claimed_char"] != target),
        key=lambda record: (record["cell_cost"], record["line_cost"], record["crop_id"]),
    )
    selected: list[dict] = []
    seen_chars: set[str] = set()
    seen_lines: set[int] = set()
    for record in ordered:
        char = record["claimed_char"]
        line = record["line_index"]
        if char in seen_chars or line in seen_lines:
            continue
        selected.append(record)
        seen_chars.add(char)
        seen_lines.add(line)
        if len(selected) == N_REFERENCES:
            return selected
    raise RuntimeError(f"Not enough target-excluded style references for {target!r}")


def to_tensor(gray_images: list[np.ndarray], torch) -> object:
    array = np.stack(gray_images).astype(np.float32) / 255.0
    tensor = torch.from_numpy(array).unsqueeze(1)
    return tensor * 2.0 - 1.0


def load_generator(torch):
    if sha256(MX_WEIGHT) != MX_WEIGHT_SHA256:
        raise RuntimeError("MX-Font checkpoint hash does not match the declared identity")
    sys.path.insert(0, str(MX_ROOT))
    from models import Generator

    generator = Generator(
        1,
        32,
        1,
        style_enc={
            "norm": "in",
            "activ": "relu",
            "pad_type": "zero",
            "skip_scale_var": False,
        },
        experts={
            "n_experts": 6,
            "norm": "in",
            "activ": "relu",
            "pad_type": "zero",
            "skip_scale_var": False,
        },
        emb_num=2,
        dec={
            "norm": "in",
            "activ": "relu",
            "pad_type": "zero",
            "out": "tanh",
        },
    )
    checkpoint = torch.load(MX_WEIGHT, map_location="cpu", weights_only=False)
    if "generator_ema" in checkpoint:
        checkpoint = checkpoint["generator_ema"]
    generator.load_state_dict(checkpoint)
    generator.eval()
    return generator


def style_images(condition: str, references: list[dict]) -> list[np.ndarray]:
    if condition == "correct_writer":
        return [load_crop(OUT / record["crop_path"]) for record in references]
    font_path = SOURCE_FONT if condition == "no_writer" else WRONG_STYLE_FONT
    return [render_character(record["claimed_char"], font_path) for record in references]


def save_output(tensor, path: Path) -> None:
    array = tensor.detach().cpu().clamp(-1, 1).add(1).div(2).mul(255).numpy()
    image = np.uint8(np.clip(array.squeeze(), 0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image, mode="L").save(path)


def encode_content(generator, chars: list[str], torch) -> dict[str, dict[str, object]]:
    encoded: dict[str, dict[str, object]] = {}
    for offset in range(0, len(chars), BATCH_SIZE):
        batch_chars = chars[offset : offset + BATCH_SIZE]
        images = [render_character(char, SOURCE_FONT) for char in batch_chars]
        tensors = to_tensor(images, torch)
        factors = generator.factorize(generator.encode(tensors), 1)
        for index, char in enumerate(batch_chars):
            encoded[char] = {key: value[index : index + 1] for key, value in factors.items()}
        print(f"content {min(offset + len(batch_chars), len(chars))}/{len(chars)}", flush=True)
    return encoded


def generate_condition(
    generator,
    condition: str,
    candidate_chars: list[str],
    references_by_char: dict[str, list[dict]],
    content_factors: dict[str, dict[str, object]],
    torch,
) -> None:
    groups: dict[tuple[str, ...], list[str]] = {}
    records_by_id = {
        record["crop_id"]: record
        for records in references_by_char.values()
        for record in records
    }
    for char in candidate_chars:
        key = tuple(record["crop_id"] for record in references_by_char[char])
        groups.setdefault(key, []).append(char)
    completed = 0
    for reference_ids, group_chars in groups.items():
        references = [records_by_id[crop_id] for crop_id in reference_ids]
        style_tensor = to_tensor(style_images(condition, references), torch)
        style_factors = generator.factorize(generator.encode(style_tensor), 0)
        mean_style = {key: value.mean(0, keepdim=True) for key, value in style_factors.items()}
        for offset in range(0, len(group_chars), BATCH_SIZE):
            batch_chars = group_chars[offset : offset + BATCH_SIZE]
            char_factors = {
                key: torch.cat([content_factors[char][key] for char in batch_chars])
                for key in mean_style
            }
            expanded_style = {
                key: value.expand(len(batch_chars), *value.shape[1:])
                for key, value in mean_style.items()
            }
            generated = generator.decode(
                generator.defactorize([expanded_style, char_factors])
            )
            for char, image in zip(batch_chars, generated):
                save_output(image, TEMPLATE_DIR / condition / f"U{ord(char):05X}.png")
            completed += len(batch_chars)
            print(
                f"{condition} {completed}/{len(candidate_chars)}",
                flush=True,
            )


def main() -> None:
    import torch

    torch.manual_seed(SEED)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    reference_page = manifest["reference_page"]
    reference_records = [
        record for record in manifest["records"] if record["page_id"] == reference_page
    ]
    candidate_chars = sorted(
        {
            record["claimed_char"]
            for record in manifest["records"]
            if record["page_id"] == manifest["held_out_page"]
        }
    )
    generator = load_generator(torch)
    reference_index: dict[str, list[str]] = {}
    references_by_char: dict[str, list[dict]] = {}
    for char in candidate_chars:
        references = choose_references(reference_records, char)
        references_by_char[char] = references
        reference_index[char] = [record["crop_id"] for record in references]
    with torch.inference_mode():
        content_factors = encode_content(generator, candidate_chars, torch)
        for condition in CONDITIONS:
            generate_condition(
                generator,
                condition,
                candidate_chars,
                references_by_char,
                content_factors,
                torch,
            )
    record = {
        "schema_version": 1,
        "model": "MX-Font",
        "implementation_url": "https://github.com/clovaai/mxfont",
        "implementation_commit": MX_COMMIT,
        "checkpoint_path": MX_WEIGHT.relative_to(ROOT).as_posix(),
        "checkpoint_sha256": MX_WEIGHT_SHA256,
        "source_font": str(SOURCE_FONT),
        "source_font_sha256": sha256(SOURCE_FONT),
        "wrong_style_font": WRONG_STYLE_FONT.relative_to(ROOT).as_posix(),
        "wrong_style_font_sha256": sha256(WRONG_STYLE_FONT),
        "reference_count": N_REFERENCES,
        "reference_selection": "lowest silver alignment cost, unique character and line, target excluded",
        "reference_crop_ids_by_target": reference_index,
        "conditions": list(CONDITIONS),
        "candidate_characters": candidate_chars,
        "generated_pixels_are_documentary_evidence": False,
        "seed": SEED,
    }
    record_path = OUT / "generation.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generation record: {record_path}")


if __name__ == "__main__":
    main()
