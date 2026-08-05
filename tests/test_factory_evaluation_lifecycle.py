from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from palimpsest.factory.evaluation.candidate import (
    canonical_json,
    load_candidate,
)
from palimpsest.factory.evaluation.store import EvaluationStore
from palimpsest.factory.evaluation.suite import load_suite
from palimpsest.factory.evaluation.metrics import Metric, MetricDirection
from palimpsest.factory.evaluation.promotion import (
    CanaryOutcome,
    PromotionError,
    commit_recipe_decision,
    create_promotion_record,
    create_rollback_proposal,
    create_rollback_record,
    load_promotion_history,
    propose_recipe_change,
    record_canary_evidence,
    to_evaluation_promotion_index,
)
from palimpsest.factory.evaluation.runner import (
    filesystem_asset_resolver,
    run_evaluation,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path


APPROVER = "Dexin Huang <dh3172@columbia.edu>"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _gold_quality(output: dict[str, object], gold: dict[str, object]) -> float:
    return 1.0 - abs(float(output["score"]) - float(gold["target"]))


def _write_resources(root: Path) -> tuple[Path, Path, Path]:
    assets = root / "assets"
    cases = root / "cases"
    candidates = root / "candidates"
    assets.mkdir(parents=True)
    cases.mkdir()
    candidates.mkdir()

    manifest_records: list[dict[str, object]] = []
    for number in (1, 2):
        image_bytes = f"isolated-image-{number}".encode()
        regions_bytes = canonical_json(
            {
                "doc_id": f"lifecycle_doc_{number}",
                "page_id": f"p{number}",
                "route": "full_page",
                "regions": [],
            }
        ).encode()
        gold_bytes = canonical_json({"target": 1.0}).encode()
        image_path = assets / f"image-{number}.bin"
        regions_path = assets / f"regions-{number}.json"
        gold_path = assets / f"gold-{number}.json"
        image_path.write_bytes(image_bytes)
        regions_path.write_bytes(regions_bytes)
        gold_path.write_bytes(gold_bytes)
        manifest_records.append(
            {
                "schema_version": 1,
                "case_id": f"lifecycle-case-{number}",
                "doc_id": f"lifecycle_doc_{number}",
                "page_id": f"p{number}",
                "pages": [
                    {
                        "page_id": f"p{number}",
                        "url": f"https://example.test/p{number}.jpg",
                        "order": number,
                    }
                ],
                "inputs": {
                    "page_image_clean": {
                        "path": f"assets/{image_path.name}",
                        "sha256": _sha256(image_bytes),
                    },
                    "page_regions": {
                        "path": f"assets/{regions_path.name}",
                        "sha256": _sha256(regions_bytes),
                    },
                },
                "references": {
                    "gold": {
                        "path": f"assets/{gold_path.name}",
                        "sha256": _sha256(gold_bytes),
                    }
                },
                "strata": [],
                "license": "synthetic test fixture",
                "adjudication": {"method": "deterministic_gold", "version": 1},
            }
        )

    manifest_path = cases / "lifecycle.jsonl"
    manifest_path.write_text(
        "".join(f"{canonical_json(record)}\n" for record in manifest_records),
        encoding="utf-8",
    )
    suite_path = root / "suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "read/lifecycle/v1",
                "station": "read",
                "mission": "deterministic evaluation-to-promotion lifecycle",
                "case_manifest": "lifecycle.jsonl",
                "qualification_eligible": True,
                "primary_metrics": {
                    "gold_quality": {
                        "direction": "maximize",
                        "minimum_effect": 0.1,
                        "confidence": 0.95,
                    }
                },
                "hard_limits": {},
                "protected_slices": [],
                "slice_policy": {"minimum_cases": 1, "maximum_regression": 0.0},
                "operational_limits": {},
                "judges": [],
                "downstream_probes": [],
                "promotion": {
                    "minimum_completed_cases": 2,
                    "paired_bootstrap_samples": 200,
                    "seed": 3477,
                    "require_all_hard_limits": True,
                    "require_all_downstream_probes": True,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    candidate_template = {
        "schema_version": 1,
        "station": "read",
        "variant": "default",
        "prompt": "read/la/diplomatic",
        "params": {
            "temperature": 0.7,
            "media_resolution": "low",
            "max_output_tokens": 32768,
            "thinking_level": "low",
            "secondary_model": "token-plan/qwen3.8-max",
            "secondary_thinking_level": None,
            "adjudicator_model": "anthropic/claude-fable-5",
            "adjudicator_thinking_level": "high",
        },
        "options": {},
    }
    baseline_path = candidates / "baseline.yaml"
    challenger_path = candidates / "challenger.yaml"
    baseline_path.write_text(
        yaml.safe_dump(
            {
                **candidate_template,
                "id": "read/lifecycle-baseline",
                "model": "qwen3.8-max-001",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    challenger_path.write_text(
        yaml.safe_dump(
            {
                **candidate_template,
                "id": "read/lifecycle-challenger",
                "model": "qwen3.8-max-002",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return suite_path, baseline_path, challenger_path


def test_evaluation_to_promotion_and_exact_rollback_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    suite_path, baseline_path, challenger_path = _write_resources(resources)
    metric = Metric("gold_quality", MetricDirection.MAXIMIZE, _gold_quality)
    suite = load_suite(
        suite_path,
        cases_root=resources / "cases",
        asset_root=resources,
        metric_resolver={metric.name: metric},
        probe_resolver={},
        judge_resolver={},
    )
    baseline = load_candidate(baseline_path)
    challenger = load_candidate(challenger_path)

    assert baseline.fingerprint != challenger.fingerprint
    assert suite.fingerprint not in {baseline.fingerprint, challenger.fingerprint}
    assert baseline.can_auto_qualify and challenger.can_auto_qualify

    class DeterministicExecutor:
        def execute(self, spec):
            output_path = artifact_path(
                spec.doc_id,
                "page_transcription",
                spec.page_id,
                Path(spec.library_root),
            )
            transcription = (
                "challenger"
                if spec.config_fingerprint == challenger.fingerprint
                else "baseline"
            )
            atomic_write_json(
                output_path,
                {
                    "doc_id": spec.doc_id,
                    "page_id": spec.page_id,
                    "page_seq": int(spec.page_id.removeprefix("p")),
                    "canvas_id": "",
                    "text": transcription,
                    "route": "full_page",
                    "regions": [],
                    "candidate_readings": [
                        {
                            "role": "primary",
                            "requested_model": spec.model,
                            "model": spec.model,
                            "raw_text": transcription,
                            "text": transcription,
                        },
                        {
                            "role": "secondary",
                            "requested_model": spec.params["secondary_model"],
                            "model": spec.params["secondary_model"],
                            "raw_text": transcription,
                            "text": transcription,
                        },
                    ],
                    "adjudication_status": "agreement",
                    "adjudication_requested_model": spec.params["adjudicator_model"],
                    "adjudication_model": None,
                    "adjudication_reasoning": "",
                    "unresolved": [],
                    "adjudication_error": None,
                    "score": 0.9
                    if spec.config_fingerprint == challenger.fingerprint
                    else 0.4,
                },
            )
            return SimpleNamespace(
                output_path=str(output_path),
                tokens_in=2,
                tokens_out=1,
                cost_usd=0.001,
                process_stats=None,
            )

    monkeypatch.setattr(
        "palimpsest.factory.evaluation.runner.make_executor",
        lambda _name: DeterministicExecutor(),
    )

    db_path = tmp_path / "evaluation.sqlite3"
    store = EvaluationStore(db_path)
    result = run_evaluation(
        run_id="lifecycle-evaluation-1",
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        store=store,
        run_root=tmp_path / "evaluation-runs",
        asset_resolver=filesystem_asset_resolver(resources, tmp_path / "objects"),
        executor="inline",
        environment={"fixture": "offline-deterministic"},
    )
    report = read_json(result.report_path)

    assert report["status"] == "completed"
    assert report["decision"] == "qualified"
    assert report["qualification"] == {"decision": "qualified", "reasons": []}
    comparison = report["aggregates"]["metrics"]["gold_quality"]["comparison"]
    assert comparison["decision"] == "pass"
    assert comparison["paired_delta"] == pytest.approx(0.5)
    indexed_run = store.run("lifecycle-evaluation-1")
    assert indexed_run is not None
    assert indexed_run.report_fingerprint == report["report_fingerprint"]
    assert indexed_run.suite_fingerprint == suite.fingerprint
    assert db_path.is_file()

    recipe_root = tmp_path / "recipes"
    recipe_root.mkdir()
    production_source = Path(
        "palimpsest/factory/recipes/latin_manuscript.yaml"
    ).read_text(encoding="utf-8")
    baseline_source = (
        production_source.replace("${PALIMPSEST_MODEL_READING}", baseline.model or "")
        .replace(
            "${PALIMPSEST_MODEL_READING_SECONDARY}",
            str(baseline.params["secondary_model"]),
        )
        .replace(
            "${PALIMPSEST_MODEL_ADJUDICATOR}",
            str(baseline.params["adjudicator_model"]),
        )
    )
    recipe_path = recipe_root / "latin_manuscript.yaml"
    recipe_path.write_text(baseline_source, encoding="utf-8")
    baseline_semantics = yaml.safe_load(recipe_path.read_bytes())

    proposal = propose_recipe_change(
        report=report,
        recipe_root=recipe_root,
        recipe="latin_manuscript",
        station="read",
        current_candidate=baseline,
        next_candidate=challenger,
    )
    canary = record_canary_evidence(
        work_order_id="lifecycle-canary-order",
        doc_id="lifecycle-canary-doc",
        run_id="lifecycle-canary-1",
        recipe_hash=proposal.proposed_recipe_hash,
        refreshed_station="read",
        status="passed",
        downstream_outcomes=(
            CanaryOutcome("downstream cells completed", "passed"),
            CanaryOutcome("publication artifacts isolated", "passed"),
        ),
        known_cost_usd=0.002,
        unknown_cost=False,
        book_valid=True,
        epub_valid=True,
        site_valid=True,
        human_review_required=True,
        human_review_passed=True,
    )

    tampered_canary = replace(canary, canary_fingerprint="0" * 64)
    with pytest.raises(PromotionError, match="fingerprint does not match"):
        create_promotion_record(
            proposal,
            canary=tampered_canary,
            approved_by=APPROVER,
            created_at="2026-07-21T12:00:00Z",
        )
    assert yaml.safe_load(recipe_path.read_bytes()) == baseline_semantics

    promotion = create_promotion_record(
        proposal,
        canary=canary,
        approved_by=APPROVER,
        created_at="2026-07-21T12:00:00Z",
    )
    history_root = tmp_path / "promotion-history"
    commit_recipe_decision(
        proposal,
        promotion,
        recipe_root=recipe_root,
        history_root=history_root,
    )
    store.record_promotion(to_evaluation_promotion_index(promotion))
    promoted_semantics = yaml.safe_load(recipe_path.read_bytes())
    promoted_read = next(
        slot for slot in promoted_semantics["line"] if slot["station"] == "read"
    )
    assert promoted_read["model"] == challenger.model

    rollback_proposal = create_rollback_proposal(
        promotion,
        recipe_root=recipe_root,
        current_candidate=challenger,
        previous_candidate=baseline,
    )
    rollback = create_rollback_record(
        rollback_proposal,
        promotion=promotion,
        approved_by=APPROVER,
        created_at="2026-07-21T13:00:00Z",
    )
    commit_recipe_decision(
        rollback_proposal,
        rollback,
        recipe_root=recipe_root,
        history_root=history_root,
    )
    store.record_promotion(to_evaluation_promotion_index(rollback))

    restored_semantics = yaml.safe_load(recipe_path.read_bytes())
    restored_read = next(
        slot for slot in restored_semantics["line"] if slot["station"] == "read"
    )
    assert restored_read == next(
        slot for slot in baseline_semantics["line"] if slot["station"] == "read"
    )
    assert rollback.previous_candidate_fingerprint == challenger.fingerprint
    assert rollback.next_candidate_fingerprint == baseline.fingerprint
    assert load_promotion_history(history_root) == (promotion, rollback)
    indexed_promotions = store.promotions()
    assert [item.action for item in indexed_promotions] == ["promote", "rollback"]
    assert [item.promotion_id for item in indexed_promotions] == [
        promotion.promotion_id,
        rollback.promotion_id,
    ]
    assert indexed_promotions[0].canary_run == canary.run_id
    assert indexed_promotions[1].canary_run is None
    assert indexed_promotions[0].evaluation_run == indexed_promotions[1].evaluation_run
    assert indexed_promotions[0].evaluation_run == report["run_id"]
    store.close()
