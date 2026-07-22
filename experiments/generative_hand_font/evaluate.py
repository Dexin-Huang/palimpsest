"""Evaluate writer reconstruction on untouched, character-disjoint P.3477 ink."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
BENCHMARK_PATH = OUT / "benchmark.json"
GENERATION_PATH = OUT / "generation.json"
REPORT_PATH = OUT / "report.json"
BLIND_SHEET_PATH = OUT / "blind_identity_review.png"
BLIND_KEY_PATH = OUT / "blind_identity_key.json"
PREPARE_PATH = ROOT / "experiments" / "scribe_template_retrieval" / "prepare.py"
ADAPT_PATH = HERE / "adapt.py"
PRIMARY = "p3477_calibrated"
SYSTEMS = (
    PRIMARY,
    "p3477_full_strength",
    "p3477_unadapted",
    "wrong_writer_adapted",
    "kai",
)
SEED = 3477
BOOTSTRAP_SAMPLES = 10_000
CANVAS = 128
THRESHOLD = 200


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
    spec = importlib.util.spec_from_file_location("font_evaluation_prepare", PREPARE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {PREPARE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_adapt():
    spec = importlib.util.spec_from_file_location("font_evaluation_adapt", ADAPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {ADAPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_gray(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(path)
    return cv2.resize(image, (CANVAS, CANVAS), interpolation=cv2.INTER_AREA)


def ink_center(gray: np.ndarray) -> tuple[float, float]:
    ink = np.float32(255 - gray) / 255.0
    moments = cv2.moments(ink)
    if moments["m00"] <= 0:
        return (CANVAS / 2, CANVAS / 2)
    return (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])


def symmetric_chamfer(real: np.ndarray, candidate: np.ndarray) -> float:
    real_ink = real < THRESHOLD
    candidate_ink = candidate < THRESHOLD
    if not real_ink.any() or not candidate_ink.any():
        return 1.0
    real_distance = cv2.distanceTransform(
        np.uint8(~real_ink), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
    )
    candidate_distance = cv2.distanceTransform(
        np.uint8(~candidate_ink), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
    )
    return float(
        (
            real_distance[candidate_ink].mean()
            + candidate_distance[real_ink].mean()
        )
        / (2 * CANVAS)
    )


def align_to_real(real: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, float]:
    real_x, real_y = ink_center(real)
    candidate_x, candidate_y = ink_center(candidate)
    best_image = candidate
    best_distance = float("inf")
    for scale in np.linspace(0.85, 1.15, 7):
        transform = cv2.getRotationMatrix2D((candidate_x, candidate_y), 0, float(scale))
        transform[0, 2] += real_x - candidate_x
        transform[1, 2] += real_y - candidate_y
        aligned = cv2.warpAffine(
            candidate,
            transform,
            (CANVAS, CANVAS),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        distance = symmetric_chamfer(real, aligned)
        if distance < best_distance:
            best_image = aligned
            best_distance = distance
    return best_image, best_distance


def intersection_over_union(first: np.ndarray, second: np.ndarray) -> float:
    first_ink = first < THRESHOLD
    second_ink = second < THRESHOLD
    union = np.logical_or(first_ink, second_ink).sum()
    return float(np.logical_and(first_ink, second_ink).sum() / union) if union else 1.0


def template_index(generation: dict) -> dict[str, dict[str, dict]]:
    return {
        system: {
            record["character"]: record
            for record in generation["outputs"][system]
        }
        for system in SYSTEMS
    }


def observations(benchmark: dict, generation: dict, prepare, clean) -> list[dict]:
    templates = template_index(generation)
    output = []
    for target in benchmark["targets"]["strict_unseen_from_reference_page"]:
        real = clean(load_gray(resolve(target["crop_path"])))
        real_feature = prepare.shape_feature(real)
        systems = {}
        for system in SYSTEMS:
            template = templates[system][target["character"]]
            candidate = load_gray(resolve(template["path"]))
            aligned, chamfer = align_to_real(real, candidate)
            feature_distance = 1.0 - float(
                np.dot(real_feature, prepare.shape_feature(aligned))
            )
            systems[system] = {
                "path": template["path"],
                "chamfer": chamfer,
                "feature_distance": feature_distance,
                "iou": intersection_over_union(real, aligned),
            }
        output.append(
            {
                "crop_id": target["crop_id"],
                "character": target["character"],
                "line_index": target["line_index"],
                "crop_path": target["crop_path"],
                "systems": systems,
            }
        )
    return output


def aggregate(items: list[dict], system: str) -> dict:
    metrics = [item["systems"][system] for item in items]
    return {
        "count": len(metrics),
        "mean_chamfer": float(np.mean([item["chamfer"] for item in metrics])),
        "mean_feature_distance": float(
            np.mean([item["feature_distance"] for item in metrics])
        ),
        "mean_iou": float(np.mean([item["iou"] for item in metrics])),
    }


def character_block_bootstrap(
    items: list[dict], baseline: str, challenger: str
) -> dict:
    blocks: dict[str, list[float]] = defaultdict(list)
    for item in items:
        blocks[item["character"]].append(
            item["systems"][baseline]["feature_distance"]
            - item["systems"][challenger]["feature_distance"]
        )
    values = [np.asarray(block, dtype=np.float32) for block in blocks.values()]
    observed = float(np.mean([value.mean() for value in values]))
    random = np.random.default_rng(SEED)
    samples = np.empty(BOOTSTRAP_SAMPLES, dtype=np.float32)
    for index in range(BOOTSTRAP_SAMPLES):
        selected = random.integers(0, len(values), size=len(values))
        samples[index] = float(np.mean([values[item].mean() for item in selected]))
    return {
        "baseline": baseline,
        "challenger": challenger,
        "delta": observed,
        "ci95": [
            float(np.quantile(samples, 0.025)),
            float(np.quantile(samples, 0.975)),
        ],
        "blocks": len(values),
        "samples": BOOTSTRAP_SAMPLES,
    }


def win_rate(items: list[dict], challenger: str, baseline: str) -> float:
    return float(
        np.mean(
            [
                item["systems"][challenger]["feature_distance"]
                < item["systems"][baseline]["feature_distance"]
                for item in items
            ]
        )
    )


def content_ranks(generation: dict, prepare) -> dict[str, dict]:
    templates = template_index(generation)
    characters = sorted(templates["kai"])
    gallery = np.stack(
        [prepare.shape_feature(load_gray(resolve(templates["kai"][char]["path"]))) for char in characters]
    )
    result = {}
    for system in SYSTEMS:
        features = np.stack(
            [
                prepare.shape_feature(
                    load_gray(resolve(templates[system][char]["path"]))
                )
                for char in characters
            ]
        )
        scores = features @ gallery.T
        ranks = []
        for index, row in enumerate(scores):
            order = np.argsort(-row, kind="stable")
            ranks.append(int(np.flatnonzero(order == index)[0]) + 1)
        result[system] = {
            "count": len(ranks),
            "top1": float(np.mean(np.asarray(ranks) == 1)),
            "top5": float(np.mean(np.asarray(ranks) <= 5)),
            "catastrophic_rank_gt_20": float(np.mean(np.asarray(ranks) > 20)),
            "mean_rank": float(np.mean(ranks)),
        }
    return result


def repeated_real_calibration(
    benchmark: dict, items: list[dict], prepare, clean
) -> dict:
    by_character: dict[str, list[dict]] = defaultdict(list)
    for target in benchmark["targets"]["strict_unseen_from_reference_page"]:
        by_character[target["character"]].append(target)
    real_distances = {}
    for character, records in by_character.items():
        if len(records) < 2:
            continue
        distances = []
        for first, second in combinations(records, 2):
            first_image = clean(load_gray(resolve(first["crop_path"])))
            second_image = clean(load_gray(resolve(second["crop_path"])))
            aligned, _ = align_to_real(first_image, second_image)
            first_feature = prepare.shape_feature(first_image)
            distances.append(
                1.0 - float(np.dot(first_feature, prepare.shape_feature(aligned)))
            )
        real_distances[character] = float(np.median(distances))
    ratios = []
    for item in items:
        calibration = real_distances.get(item["character"])
        if calibration is None or calibration <= 1e-6:
            continue
        ratios.append(
            item["systems"][PRIMARY]["feature_distance"] / calibration
        )
    return {
        "characters": len(real_distances),
        "observations": len(ratios),
        "median_generated_to_real_variation_ratio": float(np.median(ratios))
        if ratios
        else None,
        "fraction_within_two_times_real_variation": float(
            np.mean(np.asarray(ratios) <= 2.0)
        )
        if ratios
        else None,
    }


def blind_sheet(items: list[dict]) -> dict:
    unique = {}
    for item in items:
        unique.setdefault(item["character"], item)
    ranked = sorted(
        unique.values(),
        key=lambda item: item["systems"]["p3477_unadapted"]["feature_distance"]
        - item["systems"][PRIMARY]["feature_distance"],
        reverse=True,
    )
    selected = ranked[:6] + ranked[len(ranked) // 2 : len(ranked) // 2 + 3] + ranked[-3:]
    random = np.random.default_rng(SEED)
    candidates = [PRIMARY, "p3477_unadapted", "wrong_writer_adapted"]
    key = {}
    cell = 150
    gap = 20
    label_height = 34
    columns = 5
    width = gap + columns * (cell + gap)
    height = 105 + len(selected) * (cell + label_height + gap)
    canvas = Image.new("RGB", (width, height), "#f2efe7")
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 26)
    body = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15)
    cjk = ImageFont.truetype("C:/Windows/Fonts/simkai.ttf", 20)
    draw.text((gap, 18), "Blind writer-identity reconstruction review", font=title, fill="#171717")
    draw.text(
        (gap, 55),
        "Real held-out ink and Kai are disclosed. A/B/C are randomized per row; key is stored separately.",
        font=body,
        fill="#4a4740",
    )
    headers = ["Real ink", "Kai content", "Candidate A", "Candidate B", "Candidate C"]
    for column, header in enumerate(headers):
        draw.text((gap + column * (cell + gap), 82), header, font=body, fill="#242424")
    for row, item in enumerate(selected):
        order = list(random.permutation(candidates))
        key[item["crop_id"]] = {
            "character": item["character"],
            "A": order[0],
            "B": order[1],
            "C": order[2],
        }
        paths = [
            item["crop_path"],
            item["systems"]["kai"]["path"],
            *[item["systems"][system]["path"] for system in order],
        ]
        y = 105 + row * (cell + label_height + gap)
        for column, path in enumerate(paths):
            x = gap + column * (cell + gap)
            image = Image.open(resolve(path)).convert("L").resize((cell, cell), Image.Resampling.LANCZOS).convert("RGB")
            canvas.paste(image, (x, y))
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline="#56524a", width=2)
        draw.text(
            (gap, y + cell + 4),
            f"target {item['character']} · crop {item['crop_id']}",
            font=cjk,
            fill="#242424",
        )
    canvas.save(BLIND_SHEET_PATH, optimize=True)
    BLIND_KEY_PATH.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "sheet_path": BLIND_SHEET_PATH.relative_to(ROOT).as_posix(),
        "sheet_sha256": sha256(BLIND_SHEET_PATH),
        "key_path": BLIND_KEY_PATH.relative_to(ROOT).as_posix(),
        "key_sha256": sha256(BLIND_KEY_PATH),
        "rows": len(selected),
        "human_review_status": "pending",
    }


def main() -> None:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    generation = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    prepare = load_prepare()
    adapt = load_adapt()
    items = observations(
        benchmark, generation, prepare, adapt.clean_writer_image
    )
    metrics = {system: aggregate(items, system) for system in SYSTEMS}
    bootstrap = {
        baseline: character_block_bootstrap(items, baseline, PRIMARY)
        for baseline in ("p3477_unadapted", "wrong_writer_adapted", "kai")
    }
    content = content_ranks(generation, prepare)
    relative_feature_improvement = (
        metrics["p3477_unadapted"]["mean_feature_distance"]
        - metrics[PRIMARY]["mean_feature_distance"]
    ) / metrics["p3477_unadapted"]["mean_feature_distance"]
    relative_chamfer_improvement = (
        metrics["p3477_unadapted"]["mean_chamfer"]
        - metrics[PRIMARY]["mean_chamfer"]
    ) / metrics["p3477_unadapted"]["mean_chamfer"]
    checks = {
        "positive_ci_vs_unadapted": bootstrap["p3477_unadapted"]["ci95"][0] > 0,
        "positive_ci_vs_wrong_writer": bootstrap["wrong_writer_adapted"]["ci95"][0] > 0,
        "closer_than_kai_on_70_percent": win_rate(items, PRIMARY, "kai") >= 0.70,
        "ten_percent_reconstruction_improvement": relative_chamfer_improvement
        >= 0.10,
        "content_regression_within_two_points": content[PRIMARY]["top5"]
        >= content["p3477_unadapted"]["top5"] - 0.02,
        "catastrophic_content_below_one_percent": content[PRIMARY][
            "catastrophic_rank_gt_20"
        ]
        < 0.01,
    }
    report = {
        "schema_version": 1,
        "experiment": benchmark["experiment"],
        "decision": "awaiting_blind_review"
        if all(checks.values())
        else "writer_identity_gate_failed",
        "evidence_status": benchmark["evidence_status"],
        "qualification_eligible": False,
        "generated_pixels_are_documentary_evidence": False,
        "target_preprocessing": (
            "Otsu ink mask; tiny residual component removal; 2x2 closing"
        ),
        "strict_unseen_crops": len(items),
        "strict_unseen_characters": len({item["character"] for item in items}),
        "metrics": metrics,
        "paired_character_bootstrap": bootstrap,
        "win_rates": {
            baseline: win_rate(items, PRIMARY, baseline)
            for baseline in ("p3477_unadapted", "wrong_writer_adapted", "kai")
        },
        "relative_feature_improvement": relative_feature_improvement,
        "relative_chamfer_improvement": relative_chamfer_improvement,
        "content_preservation": content,
        "real_variation_calibration": repeated_real_calibration(
            benchmark, items, prepare, adapt.clean_writer_image
        ),
        "success_checks": checks,
        "blind_review": blind_sheet(items),
        "observations": items,
        "benchmark_sha256": sha256(BENCHMARK_PATH),
        "generation_sha256": sha256(GENERATION_PATH),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "metrics": metrics,
                "bootstrap": bootstrap,
                "win_rates": report["win_rates"],
                "relative_feature_improvement": relative_feature_improvement,
                "relative_chamfer_improvement": relative_chamfer_improvement,
                "content_preservation": content,
                "real_variation_calibration": report["real_variation_calibration"],
                "success_checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"report: {REPORT_PATH}")
    print(f"blind review: {BLIND_SHEET_PATH}")


if __name__ == "__main__":
    main()
