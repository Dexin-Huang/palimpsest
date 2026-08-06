"""Score unlabeled character localization against immutable box ground truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BOOTSTRAP_SEED = 361_004
BOOTSTRAP_SAMPLES = 10_000




from palimpsest.factory.workspace.io import (
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
def safe_rate(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def bbox_iou(first: list[float], second: list[float]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        raise ValueError("bounding-box width and height must be positive")
    intersection_width = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    intersection_height = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    intersection = intersection_width * intersection_height
    union = aw * ah + bw * bh - intersection
    return 0.0 if union <= 0 else intersection / union


def validate_character(entry: object, *, predicted: bool) -> dict[str, object]:
    if not isinstance(entry, dict):
        raise ValueError("character entries must be objects")
    bbox = entry.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(not isinstance(value, (int, float)) for value in bbox)
    ):
        raise ValueError("character bbox must contain four numbers")
    validated: dict[str, object] = {"bbox": [float(v) for v in bbox]}
    bbox_iou(validated["bbox"], validated["bbox"])
    if predicted:
        score = entry.get("score", 1.0)
        if not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("character score must be finite")
        validated["score"] = float(score)
    return validated


def localization_observations(
    cases: list[dict[str, object]], predictions: dict[str, dict[str, object]]
) -> tuple[int, int, int, float, int]:
    gold_by_case: dict[str, list[dict[str, object]]] = {}
    ranked: list[tuple[float, str, dict[str, object]]] = []
    for case in cases:
        case_id = str(case["case_id"])
        gold_by_case[case_id] = [
            validate_character(entry, predicted=False) for entry in case.get("characters", [])
        ]
        raw_predictions = predictions[case_id].get("characters", [])
        if raw_predictions is None:
            raw_predictions = []
        if not isinstance(raw_predictions, list):
            raise ValueError(f"prediction {case_id!r} characters must be a list")
        for entry in raw_predictions:
            character = validate_character(entry, predicted=True)
            ranked.append((float(character["score"]), case_id, character))

    matched: dict[str, set[int]] = defaultdict(set)
    true_positive_flags: list[int] = []
    false_positive_flags: list[int] = []
    for _, case_id, prediction in sorted(ranked, key=lambda item: item[0], reverse=True):
        best_index = None
        best_iou = 0.5
        for index, gold in enumerate(gold_by_case[case_id]):
            if index in matched[case_id]:
                continue
            overlap = bbox_iou(prediction["bbox"], gold["bbox"])
            if overlap >= best_iou:
                best_iou = overlap
                best_index = index
        is_match = best_index is not None
        if is_match:
            matched[case_id].add(best_index)
        true_positive_flags.append(int(is_match))
        false_positive_flags.append(int(not is_match))

    true_positives = sum(true_positive_flags)
    false_positives = sum(false_positive_flags)
    gold_total = sum(len(entries) for entries in gold_by_case.values())
    false_negatives = gold_total - true_positives
    ap50 = average_precision(true_positive_flags, false_positive_flags, gold_total)
    return true_positives, false_positives, false_negatives, ap50, len(ranked)


def average_precision(
    true_positive_flags: list[int], false_positive_flags: list[int], gold_total: int
) -> float:
    if gold_total == 0:
        return 0.0
    cumulative_true = 0
    cumulative_false = 0
    recalls = [0.0]
    precisions = [1.0]
    for true_positive, false_positive in zip(
        true_positive_flags, false_positive_flags, strict=True
    ):
        cumulative_true += true_positive
        cumulative_false += false_positive
        recalls.append(cumulative_true / gold_total)
        precisions.append(cumulative_true / (cumulative_true + cumulative_false))
    recalls.append(1.0)
    precisions.append(0.0)
    for index in range(len(precisions) - 2, -1, -1):
        precisions[index] = max(precisions[index], precisions[index + 1])
    return sum(
        (recalls[index] - recalls[index - 1]) * precisions[index]
        for index in range(1, len(recalls))
        if recalls[index] != recalls[index - 1]
    )


def localization_metrics(
    cases: list[dict[str, object]], predictions: dict[str, dict[str, object]]
) -> dict[str, object] | None:
    available_cases = sum("characters" in predictions[str(case["case_id"])] for case in cases)
    if available_cases == 0:
        return None
    true_positives, false_positives, false_negatives, ap50, predicted_total = (
        localization_observations(cases, predictions)
    )
    precision = safe_rate(true_positives, true_positives + false_positives)
    recall = safe_rate(true_positives, true_positives + false_negatives)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return {
        "cases_with_predictions": available_cases,
        "case_coverage": rounded(available_cases / len(cases)),
        "iou_threshold": 0.5,
        "detection": {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "predicted": predicted_total,
            "precision": rounded(precision),
            "recall": rounded(recall),
            "f1": rounded(f1),
            "ap50": rounded(ap50),
        },
    }



def index_predictions(
    records: list[dict[str, object]], expected_ids: set[str]
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every prediction requires a non-empty case_id")
        if case_id in indexed:
            raise ValueError(f"duplicate prediction case_id: {case_id}")
        if case_id not in expected_ids:
            raise ValueError(f"unknown prediction case_id: {case_id}")
        indexed[case_id] = record
    missing = sorted(expected_ids - indexed.keys())
    if missing:
        raise ValueError(f"missing {len(missing)} predictions; first missing: {missing[:5]}")
    return indexed


def select_cases(cases: list[dict[str, object]], smoke_per_corpus: int | None) -> list[dict[str, object]]:
    if smoke_per_corpus is None:
        return cases
    selected: list[dict[str, object]] = []
    by_corpus: dict[str, list[dict[str, object]]] = defaultdict(list)
    for case in cases:
        strata = case.get("strata")
        if not isinstance(strata, list) or not strata:
            raise ValueError(f"case {case.get('case_id')!r} lacks a primary corpus stratum")
        by_corpus[str(strata[0])].append(case)
    for corpus in sorted(by_corpus):
        ranked = sorted(by_corpus[corpus], key=lambda case: int(case.get("selection_rank", 0)))
        selected.extend(ranked[:smoke_per_corpus])
    return selected




def score(
    manifest_path: Path,
    prediction_path: Path,
    *,
    smoke_per_corpus: int | None = None,
) -> dict[str, object]:
    all_cases = read_jsonl(manifest_path)
    cases = select_cases(all_cases, smoke_per_corpus)
    if not cases:
        raise ValueError("selected manifest contains no cases")
    case_ids = [str(case.get("case_id", "")) for case in cases]
    if any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("manifest case IDs must be non-empty and unique")
    predictions = index_predictions(read_jsonl(prediction_path), set(case_ids))
    missing_localization = [
        case_id for case_id in case_ids if "characters" not in predictions[case_id]
    ]
    if missing_localization:
        raise ValueError(
            f"missing character predictions for {len(missing_localization)} cases; "
            f"first missing: {missing_localization[:5]}"
        )

    rows: list[dict[str, object]] = []
    for case in cases:
        case_id = str(case["case_id"])
        case_localization = localization_metrics([case], predictions)
        if case_localization is None:
            raise ValueError(f"prediction {case_id!r} lacks localization evidence")
        rows.append(
            {
                "case_id": case_id,
                "strata": case.get("strata", []),
                "detection": case_localization["detection"],
            }
        )

    localization = localization_metrics(cases, predictions)
    if localization is None:
        raise ValueError("predictions contain no localization evidence")
    localization_slices: dict[str, object] = {}
    primary_strata = sorted(
        {str(row["strata"][0]) for row in rows if isinstance(row["strata"], list) and row["strata"]}
    )
    for stratum in primary_strata:
        slice_cases = [
            case
            for case in cases
            if isinstance(case.get("strata"), list)
            and case["strata"]
            and str(case["strata"][0]) == stratum
        ]
        slice_localization = localization_metrics(slice_cases, predictions)
        if slice_localization is None:
            raise ValueError(f"protected slice {stratum!r} lacks localization evidence")
        localization_slices[stratum] = slice_localization["detection"]

    detection_summary = dict(localization["detection"])
    detection_summary.update(
        {
            "cases": len(cases),
            "case_coverage": localization["case_coverage"],
            "iou_threshold": localization["iou_threshold"],
        }
    )
    return {
        "schema_version": 2,
        "objective": "unlabeled_character_localization",
        "manifest": manifest_path.resolve().as_posix(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "predictions": prediction_path.resolve().as_posix(),
        "predictions_sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        "selection": {"smoke_per_corpus": smoke_per_corpus},
        "summary": detection_summary,
        "protected_slices": localization_slices,
        "localization": localization,
        "per_case": rows,
    }


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def compare_reports(baseline: dict[str, object], challenger: dict[str, object]) -> dict[str, object]:
    baseline_rows = {row["case_id"]: row for row in baseline["per_case"]}
    challenger_rows = {row["case_id"]: row for row in challenger["per_case"]}
    if baseline_rows.keys() != challenger_rows.keys():
        raise ValueError("baseline and challenger reports cover different cases")
    case_ids = sorted(baseline_rows)

    def f1(row: dict[str, object]) -> float:
        value = row["detection"]["f1"]
        return 0.0 if value is None else float(value)

    deltas = [
        f1(challenger_rows[case_id]) - f1(baseline_rows[case_id])
        for case_id in case_ids
    ]
    generator = random.Random(BOOTSTRAP_SEED)
    bootstrap_means = [
        statistics.fmean(deltas[generator.randrange(len(deltas))] for _ in deltas)
        for _ in range(BOOTSTRAP_SAMPLES)
    ]
    baseline_summary = baseline["summary"]
    challenger_summary = challenger["summary"]
    slice_deltas = {
        name: rounded(
            (float(challenger["protected_slices"][name]["f1"] or 0.0))
            - (float(baseline["protected_slices"][name]["f1"] or 0.0))
        )
        for name in baseline["protected_slices"]
    }
    ci_low = percentile(bootstrap_means, 0.025)
    ci_high = percentile(bootstrap_means, 0.975)
    coverage_regressed = float(challenger_summary["case_coverage"]) < float(
        baseline_summary["case_coverage"]
    )
    protected_regressed = any(
        delta is not None and delta < -0.01 for delta in slice_deltas.values()
    )
    if ci_low > 0 and not coverage_regressed and not protected_regressed:
        decision = "challenger_wins"
    elif ci_high < 0 or coverage_regressed or protected_regressed:
        decision = "baseline_wins"
    else:
        decision = "inconclusive_prefer_baseline"
    return {
        "schema_version": 2,
        "objective": "unlabeled_character_localization",
        "decision": decision,
        "paired_cases": len(case_ids),
        "primary_delta": {
            "metric": "per_case_detection_f1",
            "direction": "higher_is_better",
            "challenger_minus_baseline_mean": rounded(statistics.fmean(deltas)),
            "bootstrap_95_ci": [rounded(ci_low), rounded(ci_high)],
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "seed": BOOTSTRAP_SEED,
        },
        "summary_deltas": {
            name: rounded(
                float(challenger_summary[name] or 0.0)
                - float(baseline_summary[name] or 0.0)
            )
            for name in ("precision", "recall", "f1", "ap50")
        },
        "protected_slice_f1_deltas": slice_deltas,
        "baseline_predictions_sha256": baseline["predictions_sha256"],
        "challenger_predictions_sha256": challenger["predictions_sha256"],
    }


def write_report(path: Path | None, report: dict[str, object]) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        print(payload, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    print(json.dumps(report.get("summary", report), ensure_ascii=False, indent=2))
    print(f"wrote {path}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_parser = subparsers.add_parser("score", help="score one complete prediction set")
    score_parser.add_argument("--manifest", type=Path, required=True)
    score_parser.add_argument("--predictions", type=Path, required=True)
    score_parser.add_argument("--out", type=Path)
    score_parser.add_argument("--smoke-per-corpus", type=int)

    compare_parser = subparsers.add_parser("compare", help="paired baseline/challenger comparison")
    compare_parser.add_argument("--manifest", type=Path, required=True)
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--challenger", type=Path, required=True)
    compare_parser.add_argument("--out", type=Path)
    compare_parser.add_argument("--smoke-per-corpus", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.smoke_per_corpus is not None and args.smoke_per_corpus <= 0:
        raise SystemExit("--smoke-per-corpus must be positive")
    if args.command == "score":
        report = score(
            args.manifest.resolve(),
            args.predictions.resolve(),
            smoke_per_corpus=args.smoke_per_corpus,
        )
    else:
        baseline = score(
            args.manifest.resolve(),
            args.baseline.resolve(),
            smoke_per_corpus=args.smoke_per_corpus,
        )
        challenger = score(
            args.manifest.resolve(),
            args.challenger.resolve(),
            smoke_per_corpus=args.smoke_per_corpus,
        )
        report = compare_reports(baseline, challenger)
    write_report(args.out.resolve() if args.out else None, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
