from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from palimpsest.factory.evaluation.candidate import (
    RecordError,
    canonical_json,
    load_candidate,
)
from palimpsest.factory.evaluation.judge import load_judge
from palimpsest.factory.evaluation.store import (
    EvaluationPromotionIndex,
    EvaluationStore,
)
from palimpsest.factory.evaluation.suite import (
    load_case_manifest,
    load_suite,
    validate_candidate_suite,
)
from palimpsest.factory.evaluation.report import report_fingerprint


FIXED = "a" * 64


def _station(
    *,
    name: str = "read",
    variant: str = "direct/v1",
    grain: str = "page",
    consumes: tuple[str, ...] = ("page_image_clean",),
    uses_model: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        variant=variant,
        grain=grain,
        consumes=consumes,
        optional_consumes=(),
        produces="page_transcription" if name == "read" else "document_analysis",
        uses_model=uses_model,
        param_keys=frozenset({"temperature"}),
        option_keys=frozenset({"language"}),
        implementation_fingerprint="implementation-v1",
    )


def _prompt(name: str, text: str = "Read exactly what is visible") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def _candidate_record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "id": "read/direct-model-v1",
        "station": "read",
        "variant": "direct/v1",
        "model": "gemini-2.5-flash-001",
        "prompt": "read/la/diplomatic",
        "params": {"temperature": 0.1},
        "options": {"language": "la"},
        "notes": "tracked candidate",
    }
    record.update(changes)
    return record


def _write_yaml(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")


def test_candidate_resolves_strict_immutable_identity_and_all_behavior_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.yaml"
    station = _station()
    registry = {"read": {"direct/v1": station}}
    _write_yaml(path, _candidate_record())

    candidate = load_candidate(
        path,
        registry=registry,
        prompt_resolver=lambda name: _prompt(name),
    )

    assert candidate.station == "read"
    assert candidate.variant == "direct/v1"
    assert candidate.grain == "page"
    assert candidate.consumes == ("page_image_clean",)
    assert candidate.prompt_hash == _prompt("read/la/diplomatic").sha256
    assert candidate.can_auto_qualify is True
    with pytest.raises(TypeError):
        candidate.params["temperature"] = 0.2  # type: ignore[index]

    fingerprints = {candidate.fingerprint}
    mutations = (
        {"model": "gemini-2.5-pro-001"},
        {"prompt": "read/la/other"},
        {"params": {"temperature": 0.2}},
        {"options": {"language": "grc"}},
        {"variant": "direct/v2"},
    )
    for index, mutation in enumerate(mutations):
        mutated_path = tmp_path / f"candidate-{index}.yaml"
        _write_yaml(mutated_path, _candidate_record(**mutation))
        mutated_station = _station(variant=str(mutation.get("variant", "direct/v1")))
        resolved = load_candidate(
            mutated_path,
            registry={"read": {mutated_station.variant: mutated_station}},
            prompt_resolver=lambda name: _prompt(name, text=f"resolved:{name}"),
        )
        fingerprints.add(resolved.fingerprint)
    assert len(fingerprints) == len(mutations) + 1


def test_candidate_rejects_unknown_shape_config_environment_and_model_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.yaml"
    registry = {"read": {"direct/v1": _station()}}

    bad_records = (
        (_candidate_record(extra=True), "Unknown candidate keys"),
        (_candidate_record(params={"undeclared": 1}), "Unknown station params"),
        (_candidate_record(prompt="${PROMPT}"), "Environment interpolation"),
    )
    for record, message in bad_records:
        _write_yaml(path, record)
        with pytest.raises(RecordError, match=message):
            load_candidate(path, registry=registry, prompt_resolver=_prompt)

    _write_yaml(path, _candidate_record())
    with pytest.raises(RecordError, match="Unknown station variant"):
        load_candidate(path, registry={}, prompt_resolver=_prompt)

    deterministic = _station(uses_model=False)
    with pytest.raises(RecordError, match="Deterministic station variants"):
        load_candidate(
            path,
            registry={"read": {"direct/v1": deterministic}},
            prompt_resolver=_prompt,
        )

    duplicate = (
        yaml.safe_dump(_candidate_record(), sort_keys=False) + "notes: duplicate\n"
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(RecordError, match="Duplicate YAML key"):
        load_candidate(path, registry=registry, prompt_resolver=_prompt)


def test_moving_candidate_and_judge_are_explicitly_non_qualifying(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate.yaml"
    _write_yaml(candidate_path, _candidate_record(model="gemini-flash-latest"))
    candidate = load_candidate(
        candidate_path,
        registry={"read": {"direct/v1": _station()}},
        prompt_resolver=_prompt,
    )
    assert candidate.model_identity == "moving"
    assert candidate.can_auto_qualify is False

    judge_path = tmp_path / "judge.yaml"
    _write_yaml(
        judge_path,
        {
            "schema_version": 1,
            "id": "read-pairwise/judge-v1",
            "model": "gemini-pro-latest",
            "prompt": "evaluation/read/pairwise",
            "response_schema": "pairwise_preference/v1",
            "params": {"temperature": 0.1},
        },
    )
    judge = load_judge(
        judge_path,
        response_schema_resolver={"pairwise_preference/v1": object()},
        prompt_resolver=_prompt,
    )
    assert judge.model_identity == "moving"
    assert judge.can_auto_qualify is False
    with pytest.raises(RecordError, match="Unknown response schema"):
        load_judge(
            judge_path,
            response_schema_resolver={},
            prompt_resolver=_prompt,
        )


def _case_record(
    digest: str,
    *,
    page_id: str | None = "f001r",
    input_value: object | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": "vat_pal_lat_1199_f001r",
        "doc_id": "vat_pal_lat_1199",
        "page_id": page_id,
        "pages": [
            {
                "page_id": "f001r",
                "url": "https://example.test/f001r.jpg",
                "order": 1,
            }
        ],
        "inputs": {
            "page_image_clean": input_value
            if input_value is not None
            else {"path": "cases/input.bin", "sha256": digest}
        },
        "references": {
            "transcription": {"path": "gold/reference.txt", "sha256": digest}
        },
        "strata": ["latin", "marginalia"],
        "license": "Vatican Library terms",
        "adjudication": {
            "method": "double_transcription_with_resolution",
            "version": 1,
        },
    }


def _write_case_data(root: Path, *, content: bytes = b"evidence") -> tuple[Path, str]:
    (root / "cases").mkdir(parents=True, exist_ok=True)
    (root / "gold").mkdir(parents=True, exist_ok=True)
    (root / "cases" / "input.bin").write_bytes(content)
    (root / "gold" / "reference.txt").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    manifest = root / "cases" / "read.jsonl"
    manifest.write_text(canonical_json(_case_record(digest)) + "\n", encoding="utf-8")
    return manifest, digest


def test_case_manifest_validates_canonical_json_hashes_paths_and_page_identity(
    tmp_path: Path,
) -> None:
    manifest, digest = _write_case_data(tmp_path)
    cases = load_case_manifest(manifest, asset_root=tmp_path)
    assert cases[0].doc_id == "vat_pal_lat_1199"
    assert cases[0].page_id == "f001r"
    assert cases[0].inputs["page_image_clean"].sha256 == digest  # type: ignore[union-attr]

    noncanonical = json.dumps(_case_record(digest))
    manifest.write_text(noncanonical + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="not canonical JSON"):
        load_case_manifest(manifest, asset_root=tmp_path)

    traversal = _case_record(digest)
    traversal["references"] = {
        "transcription": {"path": "../secret.txt", "sha256": digest}
    }
    manifest.write_text(canonical_json(traversal) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="normalized relative path"):
        load_case_manifest(manifest, asset_root=tmp_path)

    drift = _case_record("0" * 64)
    manifest.write_text(canonical_json(drift) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="Hash mismatch"):
        load_case_manifest(manifest, asset_root=tmp_path)


def _suite_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "read/latin-diplomatic/v1",
        "station": "read",
        "mission": "faithful recovery of visible manuscript marks",
        "case_manifest": "read.jsonl",
        "primary_metrics": {
            "character_error_rate": {
                "direction": "minimize",
                "minimum_effect": 0.02,
                "confidence": 0.95,
            }
        },
        "hard_limits": {"invented_character_rate": {"maximum": 0.01}},
        "protected_slices": ["marginalia"],
        "slice_policy": {"minimum_cases": 1, "maximum_regression": 0.01},
        "operational_limits": {"mean_cost_usd_per_case": {"maximum": 0.08}},
        "judges": [
            {"metric": "blind_image_pairwise", "judge": "read-pairwise/judge-v1"}
        ],
        "downstream_probes": [{"id": "read-to-align/v1"}],
        "promotion": {
            "minimum_completed_cases": 1,
            "paired_bootstrap_samples": 100,
            "seed": 3477,
            "require_all_hard_limits": True,
            "require_all_downstream_probes": True,
        },
    }


def _fixed_judge(tmp_path: Path):
    path = tmp_path / "judge.yaml"
    _write_yaml(
        path,
        {
            "schema_version": 1,
            "id": "read-pairwise/judge-v1",
            "model": "gemini-2.5-pro-001",
            "prompt": "evaluation/read/pairwise",
            "response_schema": "pairwise_preference/v1",
            "params": {"temperature": 0.1},
        },
    )
    return load_judge(
        path,
        response_schema_resolver={"pairwise_preference/v1": object()},
        prompt_resolver=_prompt,
    )


def _load_test_suite(tmp_path: Path):
    suite_path = tmp_path / "suite.yaml"
    if not suite_path.exists():
        _write_yaml(suite_path, _suite_record())
    metrics = {
        name: SimpleNamespace(fingerprint=f"{name}/v1")
        for name in (
            "character_error_rate",
            "invented_character_rate",
            "mean_cost_usd_per_case",
            "blind_image_pairwise",
        )
    }
    return load_suite(
        suite_path,
        cases_root=tmp_path / "cases",
        asset_root=tmp_path,
        metric_resolver=metrics,
        probe_resolver={"read-to-align/v1": SimpleNamespace(fingerprint="probe/v1")},
        judge_resolver={"read-pairwise/judge-v1": _fixed_judge(tmp_path)},
    )


def test_suite_resolves_registries_and_hashes_case_metric_and_policy_drift(
    tmp_path: Path,
) -> None:
    manifest, digest = _write_case_data(tmp_path)
    suite = _load_test_suite(tmp_path)
    assert suite.cases[0].fingerprint
    assert suite.primary_metrics[0].direction == "minimize"

    original = suite.fingerprint
    assert suite.qualification_eligible is False
    assert suite.can_auto_qualify is False

    explicit_false = _suite_record()
    explicit_false["qualification_eligible"] = False
    _write_yaml(tmp_path / "suite.yaml", explicit_false)
    assert _load_test_suite(tmp_path).fingerprint == original

    eligible_record = _suite_record()
    eligible_record["qualification_eligible"] = True
    _write_yaml(tmp_path / "suite.yaml", eligible_record)
    eligible = _load_test_suite(tmp_path)
    assert eligible.qualification_eligible is True
    assert eligible.can_auto_qualify is True
    assert eligible.fingerprint != original

    record = _suite_record()
    record["promotion"] = {**record["promotion"], "seed": 999}  # type: ignore[arg-type]
    _write_yaml(tmp_path / "suite.yaml", record)
    assert _load_test_suite(tmp_path).fingerprint != original

    record = _suite_record()
    record["primary_metrics"] = {
        "character_error_rate": {
            "direction": "minimize",
            "minimum_effect": 0.03,
            "confidence": 0.95,
        }
    }
    _write_yaml(tmp_path / "suite.yaml", record)
    assert _load_test_suite(tmp_path).fingerprint != original

    new_content = b"corrected evidence"
    (tmp_path / "cases" / "input.bin").write_bytes(new_content)
    (tmp_path / "gold" / "reference.txt").write_bytes(new_content)
    changed_digest = hashlib.sha256(new_content).hexdigest()
    manifest.write_text(
        canonical_json(_case_record(changed_digest)) + "\n", encoding="utf-8"
    )
    _write_yaml(tmp_path / "suite.yaml", _suite_record())
    assert _load_test_suite(tmp_path).fingerprint != original
    assert digest != changed_digest


def test_suite_rejects_unknown_registry_names_and_candidate_socket_mismatch(
    tmp_path: Path,
) -> None:
    _write_case_data(tmp_path)
    suite_path = tmp_path / "suite.yaml"
    _write_yaml(suite_path, _suite_record())
    invalid_eligibility = _suite_record()
    invalid_eligibility["qualification_eligible"] = "yes"
    _write_yaml(suite_path, invalid_eligibility)
    with pytest.raises(RecordError, match="qualification_eligible must be a boolean"):
        _load_test_suite(tmp_path)

    _write_yaml(suite_path, _suite_record())
    with pytest.raises(RecordError, match="Unknown metric"):
        load_suite(
            suite_path,
            cases_root=tmp_path / "cases",
            asset_root=tmp_path,
            metric_resolver={},
            probe_resolver={},
            judge_resolver={},
        )

    suite = _load_test_suite(tmp_path)
    candidate_path = tmp_path / "candidate.yaml"
    _write_yaml(candidate_path, _candidate_record())
    candidate = load_candidate(
        candidate_path,
        registry={"read": {"direct/v1": _station()}},
        prompt_resolver=_prompt,
    )
    validate_candidate_suite(candidate, suite)

    wrong_station = replace(candidate, station="align")
    with pytest.raises(RecordError, match="does not match"):
        validate_candidate_suite(wrong_station, suite)
    manuscript = replace(candidate, grain="manuscript")
    with pytest.raises(RecordError, match="forbids page_id"):
        validate_candidate_suite(manuscript, suite)


def test_manuscript_candidate_requires_every_page_asset_and_null_page_id(
    tmp_path: Path,
) -> None:
    manifest, digest = _write_case_data(tmp_path)
    record = _case_record(
        digest,
        page_id=None,
        input_value={"f001r": {"path": "cases/input.bin", "sha256": digest}},
    )
    manifest.write_text(canonical_json(record) + "\n", encoding="utf-8")
    suite = _load_test_suite(tmp_path)
    candidate_path = tmp_path / "candidate.yaml"
    candidate_record = _candidate_record(
        id="read/manuscript-v1", variant="manuscript/v1"
    )
    _write_yaml(candidate_path, candidate_record)
    station = _station(variant="manuscript/v1", grain="manuscript")
    candidate = load_candidate(
        candidate_path,
        registry={"read": {"manuscript/v1": station}},
        prompt_resolver=_prompt,
    )
    validate_candidate_suite(candidate, suite)

    incomplete = _case_record(digest, page_id=None, input_value={})
    manifest.write_text(canonical_json(incomplete) + "\n", encoding="utf-8")
    with pytest.raises(RecordError, match="page mapping must not be empty"):
        _load_test_suite(tmp_path)


def _report(run_id: str = "eval-1", *, status: str = "completed") -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "decision": None,
        "started_at": "2026-07-21T10:00:00Z",
        "finished_at": "2026-07-21T10:05:00Z",
        "suite": {"id": "read/suite/v1", "fingerprint": "1" * 64},
        "baseline": {"id": "read/base", "fingerprint": "2" * 64},
        "challenger": {"id": "read/new", "fingerprint": "3" * 64},
        "judges": [],
        "cases": [],
        "aggregates": {},
        "downstream_probes": [],
        "qualification": {"decision": None, "reasons": ["insufficient evidence"]},
        "environment": {},
        "report_fingerprint": None,
    }
    report["report_fingerprint"] = report_fingerprint(report)
    return report


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def test_evaluation_store_preserves_production_tables_nulls_and_terminal_evidence(
    tmp_path: Path,
) -> None:
    db = tmp_path / "factory.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE items (doc_id TEXT PRIMARY KEY, status TEXT)")
    connection.execute("INSERT INTO items VALUES ('manuscript-1', 'active')")
    connection.commit()
    connection.close()

    report_path = tmp_path / "evaluations" / "runs" / "eval-1" / "report.json"
    report = _report()
    _write_report(report_path, report)
    with EvaluationStore(db) as store:
        running = store.begin_run(
            run_id="eval-1",
            suite_id="read/suite/v1",
            suite_fingerprint="1" * 64,
            baseline_fingerprint="2" * 64,
            challenger_fingerprint="3" * 64,
            started_at="2026-07-21T10:00:00Z",
        )
        assert running.decision is None
        assert running.report_path is None
        completed = store.index_report(report_path)
        assert completed.decision is None
        assert completed.finished_at == "2026-07-21T10:05:00Z"
        assert store.index_report(report_path) == completed

        changed = _report()
        changed["decision"] = "qualified"
        changed["report_fingerprint"] = report_fingerprint(changed)
        _write_report(report_path, changed)
        with pytest.raises(RecordError, match="immutable"):
            store.index_report(report_path)

    connection = sqlite3.connect(db)
    assert connection.execute("SELECT * FROM items").fetchall() == [
        ("manuscript-1", "active")
    ]
    production_schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'items'"
    ).fetchone()[0]
    assert "evaluation" not in production_schema
    connection.close()


def test_evaluation_store_rebuilds_only_run_index_from_canonical_reports(
    tmp_path: Path,
) -> None:
    db = tmp_path / "factory.db"
    reports = tmp_path / "evaluations" / "runs"
    _write_report(reports / "eval-1" / "report.json", _report("eval-1"))
    _write_report(reports / "eval-2" / "report.json", _report("eval-2"))
    promotion = EvaluationPromotionIndex(
        promotion_id="promotion-1",
        action="promote",
        recipe="default",
        station="read",
        previous_candidate_fingerprint="2" * 64,
        next_candidate_fingerprint="3" * 64,
        evaluation_run="eval-1",
        canary_run=None,
        approved_by="Dexin Huang <dh3172@columbia.edu>",
        created_at="2026-07-21T11:00:00Z",
    )
    with EvaluationStore(db) as store:
        store.record_promotion(promotion)
        assert store.rebuild_from_reports(reports) == 2
        assert [run.run_id for run in store.runs()] == ["eval-1", "eval-2"]
        assert store.promotions() == (promotion,)

        malformed = reports / "bad" / "report.json"
        malformed.parent.mkdir()
        malformed.write_text(json.dumps({"bad": 1}), encoding="utf-8")
        with pytest.raises(RecordError, match="invalid keys"):
            store.rebuild_from_reports(reports)
        assert [run.run_id for run in store.runs()] == ["eval-1", "eval-2"]
