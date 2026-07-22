"""Evaluate writer-conditioned hypotheses on untouched P.3477 source crops.

All systems receive the same held-out crops and candidate inventory. Labels come
from the silver source/transcription alignment manifest, never from generated
images. The paired block bootstrap resamples manuscript lines so adjacent crops
do not masquerade as independent evidence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from prepare import FONT_PATH, OUT, rendered_template, shape_feature

MANIFEST_PATH = OUT / "manifest.json"
GENERATION_PATH = OUT / "generation.json"
TEMPLATE_DIR = OUT / "templates"
REPORT_PATH = OUT / "report.json"
SEED = 3477
BOOTSTRAP_SAMPLES = 10_000
SYNTHESIS_WEIGHT = 0.5
MINIMUM_EFFECT = 0.05
HIGH_CONFIDENCE_MARGIN = 0.10


def normalized_mean(features: list[np.ndarray]) -> np.ndarray:
    mean = np.mean(features, axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm else mean


def crop_feature(record: dict) -> np.ndarray:
    gray = cv2.imread(str(OUT / record["crop_path"]), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(OUT / record["crop_path"])
    return shape_feature(gray)


def generated_feature(condition: str, char: str) -> np.ndarray:
    path = TEMPLATE_DIR / condition / f"U{ord(char):05X}.png"
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(path)
    return shape_feature(gray)


def rank_observations(
    queries: list[dict],
    query_features: np.ndarray,
    candidates: list[str],
    score_matrices: dict[str, np.ndarray],
) -> dict[str, list[dict]]:
    observations: dict[str, list[dict]] = {}
    for system, scores in score_matrices.items():
        system_observations: list[dict] = []
        for index, (record, row) in enumerate(zip(queries, scores)):
            order = np.argsort(-row, kind="stable")
            truth_index = candidates.index(record["claimed_char"])
            rank = int(np.flatnonzero(order == truth_index)[0]) + 1
            predicted_index = int(order[0])
            second_index = int(order[1]) if len(order) > 1 else predicted_index
            system_observations.append(
                {
                    "crop_id": record["crop_id"],
                    "page_id": record["page_id"],
                    "line_index": record["line_index"],
                    "bbox": record["bbox"],
                    "truth": record["claimed_char"],
                    "predicted": candidates[predicted_index],
                    "rank": rank,
                    "reciprocal_rank": 1.0 / rank,
                    "top1": rank == 1,
                    "top5": rank <= 5,
                    "truth_score": round(float(row[truth_index]), 6),
                    "prediction_score": round(float(row[predicted_index]), 6),
                    "prediction_margin": round(
                        float(row[predicted_index] - row[second_index]), 6
                    ),
                    "ink_density": round(
                        float(np.mean(1.0 - cv2.imread(
                            str(OUT / record["crop_path"]), cv2.IMREAD_GRAYSCALE
                        ) / 255.0)),
                        6,
                    ),
                    "query_feature_norm": round(float(np.linalg.norm(query_features[index])), 6),
                }
            )
        observations[system] = system_observations
    return observations


def risk_at_coverage(items: list[dict], coverage: float) -> dict:
    accepted_count = max(1, int(np.ceil(len(items) * coverage)))
    accepted = sorted(
        items,
        key=lambda item: (-item["prediction_margin"], item["crop_id"]),
    )[:accepted_count]
    return {
        "coverage": coverage,
        "accepted": accepted_count,
        "risk": float(np.mean([not item["top1"] for item in accepted])),
        "minimum_margin": accepted[-1]["prediction_margin"],
    }


def aggregate(items: list[dict]) -> dict:
    if not items:
        return {"count": 0, "top1": None, "top5": None, "mrr": None}
    wrong_high_confidence = [
        item
        for item in items
        if not item["top1"] and item["prediction_margin"] >= HIGH_CONFIDENCE_MARGIN
    ]
    return {
        "count": len(items),
        "top1": float(np.mean([item["top1"] for item in items])),
        "top5": float(np.mean([item["top5"] for item in items])),
        "mrr": float(np.mean([item["reciprocal_rank"] for item in items])),
        "mean_truth_score": float(np.mean([item["truth_score"] for item in items])),
        "high_confidence_wrong_rate": len(wrong_high_confidence) / len(items),
        "risk_at_50_percent_coverage": risk_at_coverage(items, 0.5),
        "risk_at_80_percent_coverage": risk_at_coverage(items, 0.8),
    }


def paired_block_bootstrap(
    baseline: list[dict],
    challenger: list[dict],
    selected_ids: set[str],
) -> dict:
    paired = [
        (base, candidate)
        for base, candidate in zip(baseline, challenger)
        if base["crop_id"] in selected_ids
    ]
    blocks: dict[int, list[float]] = defaultdict(list)
    for base, candidate in paired:
        blocks[base["line_index"]].append(float(candidate["top1"]) - float(base["top1"]))
    block_values = [np.asarray(values, dtype=np.float32) for values in blocks.values()]
    if not block_values:
        return {"delta": None, "ci95": [None, None], "blocks": 0, "samples": 0}
    observed = float(np.mean(np.concatenate(block_values)))
    rng = np.random.default_rng(SEED)
    deltas = np.empty(BOOTSTRAP_SAMPLES, dtype=np.float32)
    for sample in range(BOOTSTRAP_SAMPLES):
        selected = rng.integers(0, len(block_values), size=len(block_values))
        deltas[sample] = np.mean(np.concatenate([block_values[index] for index in selected]))
    lower, upper = np.quantile(deltas, [0.025, 0.975])
    return {
        "delta": observed,
        "ci95": [float(lower), float(upper)],
        "blocks": len(block_values),
        "samples": BOOTSTRAP_SAMPLES,
    }


def slice_ids(queries: list[dict], reference_chars: set[str]) -> dict[str, set[str]]:
    densities = np.asarray(
        [
            np.mean(
                1.0
                - cv2.imread(str(OUT / record["crop_path"]), cv2.IMREAD_GRAYSCALE)
                / 255.0
            )
            for record in queries
        ]
    )
    faint_threshold = float(np.quantile(densities, 0.25))
    return {
        "all": {record["crop_id"] for record in queries},
        "seen_on_reference_page": {
            record["crop_id"] for record in queries if record["claimed_char"] in reference_chars
        },
        "absent_from_reference_page": {
            record["crop_id"] for record in queries if record["claimed_char"] not in reference_chars
        },
        "faint_proxy_bottom_density_quartile": {
            record["crop_id"]
            for record, density in zip(queries, densities)
            if density <= faint_threshold
        },
        "silver_high_confidence_cell_cost_le_0_6": {
            record["crop_id"] for record in queries if record["cell_cost"] <= 0.6
        },
        "merged_geometry": {
            record["crop_id"] for record in queries if record["consumed_cells"] > 1
        },
    }


def summarize_slices(
    observations: dict[str, list[dict]], slices: dict[str, set[str]]
) -> dict:
    result: dict[str, dict] = {}
    for slice_name, ids in slices.items():
        result[slice_name] = {}
        for system, items in observations.items():
            result[slice_name][system] = aggregate(
                [item for item in items if item["crop_id"] in ids]
            )
    return result


def render_failures(
    queries: list[dict], observations: dict[str, list[dict]], system: str
) -> Path:
    by_id = {record["crop_id"]: record for record in queries}
    failures = sorted(
        (item for item in observations[system] if not item["top1"]),
        key=lambda item: (-item["prediction_margin"], item["rank"], item["crop_id"]),
    )[:48]
    tile = 160
    columns = 8
    rows = (len(failures) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile, rows * (tile + 40)), "#e7e3da")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(FONT_PATH), 22)
    small = ImageFont.truetype(str(FONT_PATH), 16)
    for index, failure in enumerate(failures):
        x = (index % columns) * tile
        y = (index // columns) * (tile + 40)
        record = by_id[failure["crop_id"]]
        crop = Image.open(OUT / record["crop_path"]).convert("L").resize((tile, tile))
        sheet.paste(crop.convert("RGB"), (x, y))
        draw.text((x + 4, y + tile + 1), failure["truth"], fill="#18351d", font=font)
        draw.text((x + 36, y + tile + 1), "→", fill="#665f56", font=small)
        draw.text((x + 58, y + tile + 1), failure["predicted"], fill="#7f2424", font=font)
        draw.text((x + 95, y + tile + 7), f"r{failure['rank']}", fill="#665f56", font=small)
    path = OUT / "worst_failures.png"
    sheet.save(path)
    return path


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    generation = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    reference_records = [
        record
        for record in manifest["records"]
        if record["page_id"] == manifest["reference_page"]
    ]
    queries = [
        record
        for record in manifest["records"]
        if record["page_id"] == manifest["held_out_page"]
        and record["claimed_char"] in generation["candidate_characters"]
    ]
    candidates = generation["candidate_characters"]
    candidate_index = {char: index for index, char in enumerate(candidates)}
    query_features = np.stack([crop_feature(record) for record in queries])
    kai_features = np.stack([rendered_template(char)[1] for char in candidates])

    reference_by_char: dict[str, list[np.ndarray]] = defaultdict(list)
    for record in reference_records:
        reference_by_char[record["claimed_char"]].append(crop_feature(record))
    baseline_features = np.stack(
        [
            normalized_mean(reference_by_char[char])
            if char in reference_by_char
            else kai_features[candidate_index[char]]
            for char in candidates
        ]
    )
    generated_features = {
        condition: np.stack([generated_feature(condition, char) for char in candidates])
        for condition in generation["conditions"]
    }

    generic_scores = query_features @ kai_features.T
    baseline_scores = query_features @ baseline_features.T
    correct_scores = query_features @ generated_features["correct_writer"].T
    no_writer_scores = query_features @ generated_features["no_writer"].T
    wrong_writer_scores = query_features @ generated_features["wrong_writer"].T
    score_matrices = {
        "generic_kai": generic_scores,
        "baseline_real_or_kai": baseline_scores,
        "correct_writer_generation": correct_scores,
        "no_writer_generation": no_writer_scores,
        "wrong_writer_generation": wrong_writer_scores,
        "challenger_correct_writer": (
            1.0 - SYNTHESIS_WEIGHT
        ) * baseline_scores + SYNTHESIS_WEIGHT * correct_scores,
        "challenger_no_writer": (
            1.0 - SYNTHESIS_WEIGHT
        ) * baseline_scores + SYNTHESIS_WEIGHT * no_writer_scores,
        "challenger_wrong_writer": (
            1.0 - SYNTHESIS_WEIGHT
        ) * baseline_scores + SYNTHESIS_WEIGHT * wrong_writer_scores,
    }
    observations = rank_observations(queries, query_features, candidates, score_matrices)
    reference_chars = set(reference_by_char)
    slices = slice_ids(queries, reference_chars)
    summaries = summarize_slices(observations, slices)
    bootstrap = {
        slice_name: paired_block_bootstrap(
            observations["baseline_real_or_kai"],
            observations["challenger_correct_writer"],
            ids,
        )
        for slice_name, ids in slices.items()
    }
    unseen_ids = slices["absent_from_reference_page"]
    style_causal_bootstrap = {
        "correct_vs_no_writer_strict_unseen": paired_block_bootstrap(
            observations["challenger_no_writer"],
            observations["challenger_correct_writer"],
            unseen_ids,
        ),
        "correct_vs_wrong_writer_strict_unseen": paired_block_bootstrap(
            observations["challenger_wrong_writer"],
            observations["challenger_correct_writer"],
            unseen_ids,
        ),
    }
    all_summary = summaries["all"]
    faint_summary = summaries["faint_proxy_bottom_density_quartile"]
    primary = bootstrap["all"]
    success_checks = {
        "minimum_top1_effect": primary["delta"] is not None
        and primary["delta"] >= MINIMUM_EFFECT,
        "positive_ci95": primary["ci95"][0] is not None and primary["ci95"][0] > 0,
        "correct_beats_no_writer": style_causal_bootstrap[
            "correct_vs_no_writer_strict_unseen"
        ]["ci95"][0]
        > 0,
        "correct_beats_wrong_writer": style_causal_bootstrap[
            "correct_vs_wrong_writer_strict_unseen"
        ]["ci95"][0]
        > 0,
        "top5_regression_within_two_points": all_summary["challenger_correct_writer"]["top5"]
        >= all_summary["baseline_real_or_kai"]["top5"] - 0.02,
        "faint_regression_within_five_points": faint_summary["challenger_correct_writer"]["top1"]
        >= faint_summary["baseline_real_or_kai"]["top1"] - 0.05,
        "no_high_confidence_wrong_increase": all_summary["challenger_correct_writer"][
            "high_confidence_wrong_rate"
        ]
        <= all_summary["baseline_real_or_kai"]["high_confidence_wrong_rate"],
        "risk_at_80_percent_coverage_not_worse": all_summary[
            "challenger_correct_writer"
        ]["risk_at_80_percent_coverage"]["risk"]
        <= all_summary["baseline_real_or_kai"]["risk_at_80_percent_coverage"]["risk"],
    }
    report = {
        "schema_version": 1,
        "experiment": manifest["experiment"],
        "decision": "passed_silver_metric_gate" if all(success_checks.values()) else "did_not_pass",
        "evidence_status": manifest["evidence_status"],
        "generated_pixels_are_documentary_evidence": False,
        "human_gold_status": "pending",
        "qualification_eligible": False,
        "candidate_inventory_size": len(candidates),
        "held_out_crop_count": len(queries),
        "synthesis_weight": SYNTHESIS_WEIGHT,
        "metrics": summaries,
        "paired_block_bootstrap": bootstrap,
        "writer_causal_bootstrap": style_causal_bootstrap,
        "success_checks": success_checks,
        "observations": observations,
        "model_identity": generation,
        "manifest_summary": manifest["summary"],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = render_failures(queries, observations, "challenger_correct_writer")
    print(json.dumps({
        "decision": report["decision"],
        "held_out_crop_count": len(queries),
        "all_metrics": all_summary,
        "primary_bootstrap": primary,
        "success_checks": success_checks,
    }, ensure_ascii=False, indent=2))
    print(f"report: {REPORT_PATH}")
    print(f"worst failures: {failures}")


if __name__ == "__main__":
    main()
