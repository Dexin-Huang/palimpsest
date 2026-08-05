"""Aggregate frozen toolbelt7 development evidence and evaluate its gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty evidence set")
    return sum(values) / len(values)


def _corpus(case_id: str) -> str:
    if "-mth1000-" in case_id:
        return "MTH1000"
    if "-mth1200-" in case_id:
        return "MTH1200"
    if "-tkh-" in case_id:
        return "TKH"
    raise ValueError(f"unknown MTHv2 corpus in {case_id}")


def _paired_success(case: dict[str, object]) -> bool:
    return all(bool(case[side]["succeeded"]) for side in ("baseline", "challenger"))


def _failed_sides(case: dict[str, object]) -> list[dict[str, str]]:
    return [
        {
            "run_id": str(case["run_id"]),
            "case_id": str(case["case_id"]),
            "side": side,
        }
        for side in ("baseline", "challenger")
        if not case[side]["succeeded"]
    ]


def _load_cases(run_ids: list[str]) -> list[dict[str, object]]:
    cases: dict[str, dict[str, object]] = {}
    for run_id in run_ids:
        path = (
            REPOSITORY_ROOT
            / "library"
            / "evaluations"
            / "runs"
            / run_id
            / "report.json"
        )
        report = json.loads(path.read_text(encoding="utf-8"))
        for raw_case in report["cases"]:
            case = {
                "run_id": run_id,
                "report": path,
                "suite_fingerprint": report["suite"]["fingerprint"],
                "recovery_failures": [],
                **raw_case,
            }
            case_id = str(case["case_id"])
            existing = cases.get(case_id)
            if existing is None:
                cases[case_id] = case
                continue
            existing_success = _paired_success(existing)
            case_success = _paired_success(case)
            if existing_success == case_success:
                raise ValueError(f"duplicate case across run reports: {case_id}")
            if case_success:
                case["recovery_failures"] = [
                    *existing["recovery_failures"],
                    *_failed_sides(existing),
                ]
                cases[case_id] = case
            else:
                existing["recovery_failures"].extend(_failed_sides(case))
    return list(cases.values())


def _index_rows(
    rows: list[dict[str, object]], *, include_run: bool
) -> dict[tuple[str, ...], dict[str, object]]:
    result: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        parts = []
        if include_run:
            parts.append(str(row["run_id"]))
        parts.extend((str(row["case_id"]), str(row["side"])))
        key = tuple(parts)
        if key in result:
            raise ValueError(f"duplicate evidence row: {key}")
        result[key] = row
    return result


def score(args: argparse.Namespace) -> dict[str, object]:
    preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
    policy = preflight["gates"]["development"]
    cases = _load_cases(args.runs)
    observed_suite_fingerprints = {str(case["suite_fingerprint"]) for case in cases}
    recall_rows = _index_rows(_jsonl(args.recall), include_run=True)
    judgment_rows = _index_rows(_jsonl(args.judgments), include_run=True)
    recovery = json.loads(args.recovery.read_text(encoding="utf-8"))

    fingerprints: dict[str, set[str]] = defaultdict(set)
    costs: dict[str, float] = defaultdict(float)
    recalls: dict[str, list[float]] = defaultdict(list)
    judgments: dict[str, list[float]] = defaultdict(list)
    by_corpus: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    failures: list[dict[str, str]] = []
    inspection_rows: list[dict[str, object]] = []
    single_layer_adoptions = 0
    two_layer_pages = 0
    selected_evidence_keys: set[tuple[str, ...]] = set()

    for case in cases:
        run_id = str(case["run_id"])
        case_id = str(case["case_id"])
        failures.extend(case["recovery_failures"])
        corpus = _corpus(case_id)
        for side in ("baseline", "challenger"):
            entry = case[side]
            fingerprints[side].add(str(entry["candidate_fingerprint"]))
            costs[side] += float(entry.get("cost_usd") or 0.0)
            if not entry["succeeded"]:
                failures.append({"case_id": case_id, "side": side})
                continue
            key = (run_id, case_id, side)
            recall = recall_rows.get(key)
            judgment = judgment_rows.get(key)
            if recall is None or judgment is None:
                failures.append({"case_id": case_id, "side": f"{side}_evidence"})
                continue
            combined = float(recall["with_commentary"]["gold_recall"])
            judged = judgment.get("gold_match_fraction")
            if judgment.get("failed") or judged is None:
                failures.append({"case_id": case_id, "side": f"{side}_judge"})
                continue
            selected_evidence_keys.add(key)
            recalls[side].append(combined)
            judgments[side].append(float(judged))
            by_corpus[corpus][f"{side}_recall"].append(combined)
            by_corpus[corpus][f"{side}_judged"].append(float(judged))

        challenger = case["challenger"]
        if not challenger["succeeded"]:
            continue
        baseline_payload = json.loads(
            Path(case["baseline"]["output_path"]).read_text(encoding="utf-8")
        )
        baseline_two_layer = bool(baseline_payload["toolbelt"]["two_layer"])
        output_path = Path(challenger["output_path"])
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        doc_root = output_path.parent.parent
        workspaces = list((doc_root / "runs" / "transcribe_omp_toolbelt7").glob("*"))
        private_manifest_present = (
            len(workspaces) == 1
            and (workspaces[0] / ".glyph-inspection-private.json").is_file()
        )
        readable_bulk_manifest_absent = (
            len(workspaces) == 1
            and not (workspaces[0] / "evidence" / "inspection.json").exists()
        )
        toolbelt = payload["toolbelt"]
        inspection = toolbelt["inspection"]
        usage = inspection["usage"]
        reorder = toolbelt["reorder"]
        two_layer = bool(toolbelt["two_layer"])
        two_layer_pages += int(two_layer)
        if not two_layer and reorder["adopted"]:
            single_layer_adoptions += 1
        inspection_rows.append(
            {
                "case_id": case_id,
                "corpus": corpus,
                "classifier_enabled": inspection["classifier_enabled"],
                "calls": usage["calls"],
                "unique_glyphs": usage["unique_glyphs"],
                "inline_crop_reads": usage["inline_crop_reads"],
                "private_manifest_present": private_manifest_present,
                "readable_bulk_manifest_absent": readable_bulk_manifest_absent,
                "baseline_two_layer": baseline_two_layer,
                "reorder_two_layer": reorder["two_layer"],
                "seq_ratio_before": reorder["seq_ratio_before"],
                "seq_ratio_after": reorder["seq_ratio_after"],
                "two_layer": two_layer,
                "reorder_triggered": reorder["triggered"],
                "reorder_adopted": reorder["adopted"],
            }
        )

    identities_stable = all(len(values) == 1 for values in fingerprints.values())
    if not identities_stable:
        raise ValueError("candidate fingerprints changed across run chunks")
    resolved_fingerprints = {
        side: next(iter(values)) for side, values in fingerprints.items()
    }

    baseline_recall = _mean(recalls["baseline"])
    challenger_recall = _mean(recalls["challenger"])
    baseline_judged = _mean(judgments["baseline"])
    challenger_judged = _mean(judgments["challenger"])
    total_calls = sum(int(row["calls"]) for row in inspection_rows)
    maximum_calls = max((int(row["calls"]) for row in inspection_rows), default=0)
    expected_two_layer = set(
        preflight["ordering_expectation"]["full_expected_two_layer_cases"]
    )
    baseline_two_layer = {
        str(row["case_id"]) for row in inspection_rows if row["baseline_two_layer"]
    }
    challenger_two_layer = {
        str(row["case_id"]) for row in inspection_rows if row["two_layer"]
    }
    single_layer_triggers = sum(
        int(bool(row["reorder_triggered"]))
        for row in inspection_rows
        if not row["two_layer"]
    )
    transition_invariant = all(
        (
            not row["reorder_adopted"]
            if not row["reorder_triggered"]
            else bool(row["two_layer"])
            and bool(row["reorder_two_layer"])
            and bool(row["reorder_adopted"])
            and row["seq_ratio_before"] is not None
            and row["seq_ratio_after"] is not None
            and float(row["seq_ratio_after"]) > float(row["seq_ratio_before"])
        )
        for row in inspection_rows
    )
    gates = {
        "completed_pairs": len(cases) == int(policy["completed_pairs"])
        and all(_paired_success(case) for case in cases),
        "candidate_identities_stable": identities_stable
        and resolved_fingerprints["baseline"]
        == preflight["baseline"].get(
            "fingerprint_at_preflight", preflight["baseline"].get("fingerprint")
        )
        and resolved_fingerprints["challenger"]
        == preflight["challenger"]["fingerprint"],
        "classifier_enabled": len(inspection_rows) == int(policy["completed_pairs"])
        and all(bool(row["classifier_enabled"]) for row in inspection_rows),
        "inspection_used": total_calls
        >= int(policy["challenger_total_inspection_calls_minimum"]),
        "inspection_bounded": maximum_calls
        <= int(policy["challenger_inspection_calls_per_page_maximum"]),
        "inline_crop_reads": sum(
            int(row["inline_crop_reads"]) for row in inspection_rows
        )
        == total_calls,
        "classifier_airlock": all(
            bool(row["private_manifest_present"])
            and bool(row["readable_bulk_manifest_absent"])
            for row in inspection_rows
        ),
        "combined_gold_recall": challenger_recall
        >= float(policy["challenger_mean_sequence_recall_minimum"]),
        "combined_gold_recall_delta": challenger_recall - baseline_recall
        >= float(policy["challenger_mean_sequence_recall_delta_minimum"]),
        "blinded_judged_mean": challenger_judged
        >= float(policy["challenger_blinded_judged_mean_minimum"]),
        "blinded_judged_delta": challenger_judged - baseline_judged
        >= float(policy["challenger_blinded_judged_delta_minimum"]),
        "single_layer_reorder": single_layer_adoptions
        == int(policy["single_layer_reorder_adoptions"]),
        "expected_two_layer_cases": baseline_two_layer
        == expected_two_layer
        == challenger_two_layer,
        "single_layer_reorder_triggers": single_layer_triggers
        == int(policy["single_layer_reorder_triggers"]),
        "two_layer_transition_invariant": transition_invariant,
        "catastrophic_failures": len(failures)
        <= int(policy["catastrophic_failures_maximum"]),
    }
    run_reports = {
        run_id: json.loads(
            (
                REPOSITORY_ROOT
                / "library"
                / "evaluations"
                / "runs"
                / run_id
                / "report.json"
            ).read_text(encoding="utf-8")
        )
        for run_id in args.runs
    }
    execution_reported_cost = sum(
        float(report["aggregates"]["cost_ceiling"]["total_known_cost_usd"])
        for report in run_reports.values()
    )
    partial_timeout_cost = float(
        recovery["failure"]["reported_partial_session_cost_usd"]
    )
    judge_cost = sum(
        float(row.get("cost_usd") or 0.0) for row in judgment_rows.values()
    )
    spent_before_v3 = float(preflight["cost_guard"]["spent_before_v3_usd"])
    cumulative_known_cost = (
        spent_before_v3 + execution_reported_cost + partial_timeout_cost + judge_cost
    )
    authorized_total = float(
        preflight["cost_guard"]["maximum_approved_total_spend_usd"]
    )
    excluded_evidence_keys = sorted(
        (set(recall_rows) | set(judgment_rows)) - selected_evidence_keys
    )
    technical_decision = "accepted" if all(gates.values()) else "rejected"
    methodology_deviations = []
    if observed_suite_fingerprints != {str(preflight["suite_fingerprint"])}:
        methodology_deviations.append(
            {
                "kind": "declared_suite_fingerprint_mismatch",
                "declared": preflight["suite_fingerprint"],
                "observed": sorted(observed_suite_fingerprints),
                "effect": (
                    "The run reports retain the actual consistent suite identity, "
                    "but the preflight declaration was not exact."
                ),
            }
        )
    if "ordering_expectation" not in preflight:
        methodology_deviations.append(
            {
                "kind": "unauditable_smoke_ordering_gate",
                "effect": (
                    "The preflight names no exact runtime ordering cases or outcomes."
                ),
            }
        )
    if failures:
        methodology_deviations.extend(
            [
                {
                    "kind": "recovery_pair_substitution",
                    "record": str(args.recovery),
                    "record_sha256": _sha256(args.recovery),
                    "effect": (
                        "The successful c2b pair supplies the selected quality row "
                        "for GL-1054-1-12; the original c2 timeout remains a "
                        "catastrophic failure."
                    ),
                },
                {
                    "kind": "paid_dispatch_after_unknown_cost",
                    "effect": (
                        "The c2 timeout left one in-flight model request unpriced. "
                        "Recovery and judge calls continued despite the preflight "
                        "stop condition; exact compliance with the 26 USD "
                        "authorization is therefore unresolved."
                    ),
                },
                {
                    "kind": "recovery_command_ceiling_overshoot",
                    "declared_usd": recovery["recovery_run"][
                        "declared_command_ceiling_usd"
                    ],
                    "observed_usd": recovery["recovery_run"]["known_cost_usd"],
                    "overshoot_usd": recovery["recovery_run"][
                        "command_ceiling_overshoot_usd"
                    ],
                    "effect": (
                        "Two already-dispatched workers completed after aggregate "
                        "spend crossed the command ceiling."
                    ),
                },
            ]
        )
    if technical_decision == "rejected":
        decision = "rejected"
    elif methodology_deviations:
        decision = "inconclusive_due_to_preflight_deviations"
    else:
        decision = "accepted"
    return {
        "schema_version": 1,
        "record_kind": "bounded-experiment-result",
        "record_id": "transcribe-toolbelt7-priority-development-v3",
        "station": "transcribe",
        "stage": "development",
        "state": "completed",
        "created_at": args.created_at,
        "operator": preflight["operator"],
        "preflight": str(args.preflight),
        "preflight_sha256": _sha256(args.preflight),
        "recovery": {
            "path": str(args.recovery),
            "sha256": _sha256(args.recovery),
        },
        "run_ids": args.runs,
        "report_sha256": {
            run_id: _sha256(
                REPOSITORY_ROOT
                / "library"
                / "evaluations"
                / "runs"
                / run_id
                / "report.json"
            )
            for run_id in args.runs
        },
        "suite_fingerprints": sorted(observed_suite_fingerprints),
        "candidate_fingerprints": resolved_fingerprints,
        "evaluation": {
            "cases": len(cases),
            "failures": failures,
            "combined_gold_recall": {
                "baseline": baseline_recall,
                "challenger": challenger_recall,
                "delta": challenger_recall - baseline_recall,
            },
            "blinded_judged_match": {
                "baseline": baseline_judged,
                "challenger": challenger_judged,
                "delta": challenger_judged - baseline_judged,
            },
            "by_corpus": {
                corpus: {key: _mean(values) for key, values in evidence.items()}
                for corpus, evidence in sorted(by_corpus.items())
            },
            "inspection": {
                "total_calls": total_calls,
                "maximum_calls_per_page": maximum_calls,
                "pages_with_calls": sum(
                    int(int(row["calls"]) > 0) for row in inspection_rows
                ),
                "total_unique_glyphs": sum(
                    int(row["unique_glyphs"]) for row in inspection_rows
                ),
                "pages": inspection_rows,
            },
            "ordering": {
                "two_layer_pages": two_layer_pages,
                "single_layer_reorder_adoptions": single_layer_adoptions,
                "expected_two_layer_cases": sorted(expected_two_layer),
                "baseline_two_layer_cases": sorted(baseline_two_layer),
                "challenger_two_layer_cases": sorted(challenger_two_layer),
                "single_layer_reorder_triggers": single_layer_triggers,
                "two_layer_transition_invariant": transition_invariant,
            },
            "cost_usd": dict(costs),
        },
        "evidence": {
            "recall": {"path": str(args.recall), "sha256": _sha256(args.recall)},
            "judgments": {
                "path": str(args.judgments),
                "sha256": _sha256(args.judgments),
            },
            "selection": {
                "input_rows": len(judgment_rows),
                "selected_rows": len(selected_evidence_keys),
                "excluded_rows": [
                    {"run_id": key[0], "case_id": key[1], "side": key[2]}
                    for key in excluded_evidence_keys
                ],
                "rule": (
                    "A successful recovery pair replaces an incomplete pair for "
                    "quality aggregation; the incomplete attempt remains failure "
                    "and operational evidence."
                ),
            },
        },
        "budget": {
            "authorized_total_usd": authorized_total,
            "spent_before_v3_usd": spent_before_v3,
            "v3_execution_reported_cost_usd": execution_reported_cost,
            "timeout_partial_session_cost_usd": partial_timeout_cost,
            "judge_cost_usd": judge_cost,
            "cumulative_known_cost_usd": cumulative_known_cost,
            "remaining_against_known_cost_usd": authorized_total
            - cumulative_known_cost,
            "in_flight_timeout_request_cost": "unknown",
            "within_authorized_total": None,
            "compliance": "unresolved_due_to_unknown_in_flight_request_cost",
            "further_paid_dispatch_permitted": False,
        },
        "methodology_deviations": methodology_deviations,
        "gates": gates,
        "technical_decision": technical_decision,
        "decision": decision,
        "production_state": "unchanged",
        "next_permitted_action": (
            "Record the development verdict and stop; production qualification, "
            "proposal, canary, and refresh require separate authorization and evidence."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--recall", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["decision"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
