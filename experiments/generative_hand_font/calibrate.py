"""Choose the strongest writer residual that preserves target-character content."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
GENERATION_PATH = OUT / "generation.json"
PREPARE_PATH = ROOT / "experiments" / "scribe_template_retrieval" / "prepare.py"
CALIBRATED_DIR = OUT / "templates" / "p3477_calibrated"
ALPHAS = tuple(round(value, 2) for value in np.arange(0.05, 1.01, 0.05))
MINIMUM_TOP5 = 0.98
MAXIMUM_CATASTROPHIC_RATE = 0.01


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def load_prepare():
    spec = importlib.util.spec_from_file_location("font_calibration_prepare", PREPARE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {PREPARE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_gray(path: str) -> np.ndarray:
    image = cv2.imread(str(resolve(path)), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(path)
    return image


def blend(unadapted: np.ndarray, adapted: np.ndarray, alpha: float) -> np.ndarray:
    result = (1.0 - alpha) * unadapted.astype(np.float32)
    result += alpha * adapted.astype(np.float32)
    return np.uint8(np.clip(np.rint(result), 0, 255))


def content_metrics(
    characters: list[str],
    images: dict[str, np.ndarray],
    gallery: np.ndarray,
    prepare,
) -> dict:
    features = np.stack([prepare.shape_feature(images[char]) for char in characters])
    scores = features @ gallery.T
    ranks = []
    for index, row in enumerate(scores):
        order = np.argsort(-row, kind="stable")
        ranks.append(int(np.flatnonzero(order == index)[0]) + 1)
    ranks_array = np.asarray(ranks)
    return {
        "top1": float(np.mean(ranks_array == 1)),
        "top5": float(np.mean(ranks_array <= 5)),
        "catastrophic_rank_gt_20": float(np.mean(ranks_array > 20)),
        "mean_rank": float(np.mean(ranks_array)),
    }


def content_eligible(metrics: dict) -> bool:
    return (
        metrics["top5"] >= MINIMUM_TOP5
        and metrics["catastrophic_rank_gt_20"] < MAXIMUM_CATASTROPHIC_RATE
    )


def main() -> None:
    generation = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    prepare = load_prepare()
    index = {
        system: {
            record["character"]: record
            for record in generation["outputs"][system]
        }
        for system in ("p3477_adapted", "p3477_unadapted", "kai")
    }
    characters = sorted(index["kai"])
    adapted = {
        char: load_gray(index["p3477_adapted"][char]["path"])
        for char in characters
    }
    unadapted = {
        char: load_gray(index["p3477_unadapted"][char]["path"])
        for char in characters
    }
    gallery = np.stack(
        [
            prepare.shape_feature(load_gray(index["kai"][char]["path"]))
            for char in characters
        ]
    )

    sweep = []
    selected_alpha = None
    selected_images = None
    for alpha in ALPHAS:
        images = {
            char: blend(unadapted[char], adapted[char], alpha)
            for char in characters
        }
        metrics = content_metrics(characters, images, gallery, prepare)
        eligible = content_eligible(metrics)
        sweep.append({"alpha": alpha, **metrics, "eligible": eligible})
        if eligible:
            selected_alpha = alpha
            selected_images = images
    if selected_alpha is None or selected_images is None:
        raise RuntimeError("No nonzero writer residual satisfies the content gate")

    CALIBRATED_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for character in characters:
        path = CALIBRATED_DIR / f"U{ord(character):05X}.png"
        Image.fromarray(selected_images[character], mode="L").save(path)
        records.append(
            {
                "character": character,
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256(path),
            }
        )
    generation["outputs"]["p3477_full_strength"] = generation["outputs"][
        "p3477_adapted"
    ]
    generation["outputs"]["p3477_calibrated"] = records
    generation["conditions"] = [
        "p3477_calibrated",
        "p3477_full_strength",
        "p3477_unadapted",
        "wrong_writer_adapted",
        "kai",
    ]
    generation["writer_strength_calibration"] = {
        "method": "linear residual between unadapted and fully adapted glyphs",
        "selection_evidence": "canonical-content preservation only; no held-out manuscript targets",
        "minimum_top5": MINIMUM_TOP5,
        "maximum_catastrophic_rate": MAXIMUM_CATASTROPHIC_RATE,
        "selected_alpha": selected_alpha,
        "sweep": sweep,
    }
    GENERATION_PATH.write_text(
        json.dumps(generation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            generation["writer_strength_calibration"],
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"calibrated templates: {CALIBRATED_DIR}")


if __name__ == "__main__":
    main()
