from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

import palimpsest.factory.evaluation.promotion as promotion_domain
from palimpsest.factory import gateway
from palimpsest.factory.evaluation.candidate import ResolvedCandidate, canonical_json
from palimpsest.factory.evaluation.promotion import (
    CanaryOutcome,
    PromotionError,
    _apply_recipe_proposal,
    _append_promotion_record,
    commit_recipe_decision,
    create_promotion_record,
    create_rollback_proposal,
    create_rollback_record,
    load_canary_evidence,
    load_promotion_history,
    load_promotion_record,
    load_recipe_proposal,
    load_reproducibility_waiver,
    propose_recipe_change,
    record_canary_evidence,
    record_canary_cost_waiver,
    record_reproducibility_waiver,
    save_canary_evidence,
    save_promotion_record,
    save_recipe_proposal,
    save_reproducibility_waiver,
    to_evaluation_promotion_index,
)
from palimpsest.factory.evaluation.report import report_fingerprint


NOW = "2026-07-21T12:00:00Z"
APPROVER = "Dexin Huang <dh3172@columbia.edu>"


def _candidate(
    candidate_id: str,
    fingerprint_digit: str,
    *,
    model: str,
    model_identity: str = "fixed",
    tracked: bool = True,
) -> ResolvedCandidate:
    return ResolvedCandidate(
        schema_version=1,
        id=candidate_id,
        station="read",
        variant="default",
        grain="page",
        consumes=("page_image_clean", "page_regions"),
        optional_consumes=(),
        produces="page_transcription",
        model=model,
        model_identity=model_identity,  # type: ignore[arg-type]
        prompt_name="read/la/diplomatic",
        prompt_hash="a" * 64,
        params={
            "media_resolution": "low",
            "max_output_tokens": 32768,
            "thinking_level": "low",
            "secondary_model": "token-plan/qwen3.8-max",
            "secondary_thinking_level": None,
            "adjudicator_model": "anthropic/claude-fable-5",
            "adjudicator_thinking_level": "high",
        },
        options={},
        notes=None,
        implementation_fingerprint="b" * 64,
        fingerprint=fingerprint_digit * 64,
        tracked=tracked,
    )


def _report(
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    *,
    decision: str = "qualified",
    status: str = "completed",
    reasons: list[str] | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "run_id": "eval-read-1",
        "status": status,
        "decision": decision,
        "started_at": "2026-07-21T10:00:00Z",
        "finished_at": "2026-07-21T10:05:00Z",
        "suite": {"id": "read/latin/v1", "fingerprint": "c" * 64},
        "baseline": {"id": baseline.id, "fingerprint": baseline.fingerprint},
        "challenger": {"id": challenger.id, "fingerprint": challenger.fingerprint},
        "judges": [],
        "cases": [],
        "aggregates": {},
        "downstream_probes": [],
        "qualification": {"decision": decision, "reasons": reasons or []},
        "environment": {},
    }
    report["report_fingerprint"] = report_fingerprint(report)
    return report


def _recipe_root(
    tmp_path: Path,
    candidate: ResolvedCandidate,
    *,
    retain_model_placeholder: bool = False,
) -> Path:
    root = tmp_path / "recipes"
    root.mkdir()
    source = Path("palimpsest/factory/recipes/latin_manuscript.yaml").read_text(
        encoding="utf-8"
    )
    source = source.replace(
        "${PALIMPSEST_MODEL_READING_SECONDARY}",
        str(candidate.params["secondary_model"]),
    ).replace(
        "${PALIMPSEST_MODEL_ADJUDICATOR}",
        str(candidate.params["adjudicator_model"]),
    )
    if not retain_model_placeholder:
        source = source.replace("${PALIMPSEST_MODEL_READING}", candidate.model or "")
    (root / "latin_manuscript.yaml").write_text(source, encoding="utf-8")
    return root


def _proposal(tmp_path: Path):
    baseline = _candidate("read/baseline", "1", model="qwen3.8-max-001")
    challenger = _candidate("read/challenger", "2", model="qwen3.8-max-002")
    root = _recipe_root(tmp_path, baseline)
    proposal = propose_recipe_change(
        report=_report(baseline, challenger),
        recipe_root=root,
        recipe="latin_manuscript",
        station="read",
        current_candidate=baseline,
        next_candidate=challenger,
    )
    return root, baseline, challenger, proposal


def _canary(proposal, *, status: str = "passed", outcome: str = "passed"):
    return record_canary_evidence(
        work_order_id="order-1",
        doc_id="canary-doc",
        run_id="canary-run-1",
        recipe_hash=proposal.proposed_recipe_hash,
        refreshed_station="read",
        status=status,  # type: ignore[arg-type]
        downstream_outcomes=(
            CanaryOutcome("all downstream cells", outcome),  # type: ignore[arg-type]
        ),
        known_cost_usd=1.25,
        unknown_cost=False,
        book_valid=True,
        epub_valid=True,
        site_valid=True,
        human_review_required=True,
        human_review_passed=True,
    )


@pytest.mark.parametrize(
    ("status", "decision", "message"),
    [
        ("interrupted", "qualified", "completed"),
        ("completed", "rejected", "qualified"),
    ],
)
def test_unqualified_or_incomplete_report_cannot_propose(
    tmp_path: Path, status: str, decision: str, message: str
) -> None:
    baseline = _candidate("read/baseline", "1", model="qwen3.8-max-001")
    challenger = _candidate("read/challenger", "2", model="qwen3.8-max-002")
    root = _recipe_root(tmp_path, baseline)

    with pytest.raises(PromotionError, match=message):
        propose_recipe_change(
            report=_report(baseline, challenger, status=status, decision=decision),
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=baseline,
            next_candidate=challenger,
        )


def test_tampered_report_cannot_propose(tmp_path: Path) -> None:
    baseline = _candidate("read/baseline", "1", model="qwen3.8-max-001")
    challenger = _candidate("read/challenger", "2", model="qwen3.8-max-002")
    root = _recipe_root(tmp_path, baseline)
    report = _report(baseline, challenger)
    report["decision"] = "rejected"

    with pytest.raises(PromotionError, match="fingerprint"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=baseline,
            next_candidate=challenger,
        )


def test_stale_recipe_compare_and_swap_never_changes_source(tmp_path: Path) -> None:
    root, _, _, proposal = _proposal(tmp_path)
    source = root / "latin_manuscript.yaml"
    source.write_text(
        source.read_text(encoding="utf-8") + "# operator edit\n", encoding="utf-8"
    )
    operator_version = source.read_bytes()

    with pytest.raises(PromotionError, match="compare-and-swap"):
        _apply_recipe_proposal(proposal, recipe_root=root)

    assert source.read_bytes() == operator_version


@pytest.mark.parametrize("recipe", ["../latin_manuscript", "nested/recipe", "C:escape"])
def test_recipe_path_traversal_is_rejected(tmp_path: Path, recipe: str) -> None:
    baseline = _candidate("read/baseline", "1", model="qwen3.8-max-001")
    challenger = _candidate("read/challenger", "2", model="qwen3.8-max-002")
    root = _recipe_root(tmp_path, baseline)

    with pytest.raises(PromotionError, match="Invalid recipe name"):
        propose_recipe_change(
            report=_report(baseline, challenger),
            recipe_root=root,
            recipe=recipe,
            station="read",
            current_candidate=baseline,
            next_candidate=challenger,
        )


def test_failed_missing_or_incomplete_canary_cannot_promote(tmp_path: Path) -> None:
    _, _, _, proposal = _proposal(tmp_path)

    with pytest.raises(PromotionError, match="passing canary"):
        create_promotion_record(
            proposal, canary=None, approved_by=APPROVER, created_at=NOW
        )
    with pytest.raises(PromotionError, match="passing canary"):
        create_promotion_record(
            proposal,
            canary=_canary(proposal, status="failed"),
            approved_by=APPROVER,
            created_at=NOW,
        )
    with pytest.raises(PromotionError, match="outcomes"):
        create_promotion_record(
            proposal,
            canary=_canary(proposal, outcome="unknown"),
            approved_by=APPROVER,
            created_at=NOW,
        )

def test_unknown_canary_cost_requires_exact_reviewed_waiver(tmp_path: Path) -> None:
    _, _, _, proposal = _proposal(tmp_path)
    canary = record_canary_evidence(
        work_order_id="order-1",
        doc_id="canary-doc",
        run_id="canary-run-unknown-cost",
        recipe_hash=proposal.proposed_recipe_hash,
        refreshed_station="read",
        status="unknown",
        downstream_outcomes=(CanaryOutcome("all downstream cells", "passed"),),
        known_cost_usd=0.05,
        unknown_cost=True,
        book_valid=True,
        epub_valid=True,
        site_valid=True,
    )

    with pytest.raises(PromotionError, match="cost evidence is unknown"):
        create_promotion_record(
            proposal,
            canary=canary,
            approved_by=APPROVER,
            created_at=NOW,
        )

    wrong_canary = record_canary_cost_waiver(
        canary_fingerprint="9" * 64,
        approved_by=APPROVER,
        reason="negligible subscription-backed agent cost",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="another canary"):
        create_promotion_record(
            proposal,
            canary=canary,
            approved_by=APPROVER,
            created_at=NOW,
            cost_waiver=wrong_canary,
        )

    waiver = record_canary_cost_waiver(
        canary_fingerprint=canary.canary_fingerprint,
        approved_by=APPROVER,
        reason="negligible subscription-backed agent cost",
        created_at=NOW,
    )
    record = create_promotion_record(
        proposal,
        canary=canary,
        approved_by=APPROVER,
        created_at=NOW,
        cost_waiver=waiver,
    )
    path = tmp_path / "promotion.json"
    save_promotion_record(path, record)

    assert record.canary_cost_waiver == waiver
    assert load_promotion_record(path) == record


def test_canary_cost_waiver_cannot_mask_other_evidence(tmp_path: Path) -> None:
    _, _, _, proposal = _proposal(tmp_path)
    known_canary = _canary(proposal)
    waiver = record_canary_cost_waiver(
        canary_fingerprint=known_canary.canary_fingerprint,
        approved_by=APPROVER,
        reason="not applicable to known cost",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="requires unknown cost"):
        create_promotion_record(
            proposal,
            canary=known_canary,
            approved_by=APPROVER,
            created_at=NOW,
            cost_waiver=waiver,
        )

    failed_canary = record_canary_evidence(
        work_order_id="order-1",
        doc_id="canary-doc",
        run_id="canary-run-failed",
        recipe_hash=proposal.proposed_recipe_hash,
        refreshed_station="read",
        status="failed",
        downstream_outcomes=(CanaryOutcome("all downstream cells", "passed"),),
        known_cost_usd=0.05,
        unknown_cost=True,
        book_valid=True,
        epub_valid=True,
        site_valid=True,
    )
    failed_waiver = record_canary_cost_waiver(
        canary_fingerprint=failed_canary.canary_fingerprint,
        approved_by=APPROVER,
        reason="cost only",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="passing canary"):
        create_promotion_record(
            proposal,
            canary=failed_canary,
            approved_by=APPROVER,
            created_at=NOW,
            cost_waiver=failed_waiver,
        )


def test_moving_candidate_requires_exact_recorded_waiver(tmp_path: Path) -> None:
    baseline = _candidate("read/baseline", "1", model="qwen3.8-max-001")
    moving = _candidate(
        "read/moving", "2", model="qwen3.8-max-latest", model_identity="moving"
    )
    root = _recipe_root(tmp_path, baseline)
    report = _report(baseline, moving)

    with pytest.raises(PromotionError, match="reproducibility waiver"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=baseline,
            next_candidate=moving,
        )

    wrong = record_reproducibility_waiver(
        candidate_fingerprint="9" * 64,
        approved_by=APPROVER,
        reason="provider-controlled emergency alias",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="another candidate"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=baseline,
            next_candidate=moving,
            waiver=wrong,
        )

    waiver = record_reproducibility_waiver(
        candidate_fingerprint=moving.fingerprint,
        approved_by=APPROVER,
        reason="provider-controlled emergency alias",
        created_at=NOW,
    )
    proposal = propose_recipe_change(
        report=report,
        recipe_root=root,
        recipe="latin_manuscript",
        station="read",
        current_candidate=baseline,
        next_candidate=moving,
        waiver=waiver,
    )
    assert proposal.waiver_fingerprint == waiver.waiver_fingerprint


def test_manual_review_cuts_over_env_backed_moving_baseline_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moving = _candidate(
        "read/moving-baseline",
        "1",
        model="qwen3.8-max-latest",
        model_identity="moving",
    )
    fixed = _candidate("read/fixed", "2", model="qwen3.8-max-002")
    root = _recipe_root(tmp_path, moving, retain_model_placeholder=True)
    monkeypatch.setenv("PALIMPSEST_MODEL_READING", moving.model)
    reason = f"baseline identity requires reproducibility waiver: {moving.fingerprint}"
    report = _report(
        moving,
        fixed,
        decision="manual_review_required",
        reasons=[reason],
    )

    with pytest.raises(PromotionError, match="explicit reproducibility waiver"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=moving,
            next_candidate=fixed,
        )
    wrong = record_reproducibility_waiver(
        candidate_fingerprint="9" * 64,
        approved_by=APPROVER,
        reason="wrong identity",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="another candidate"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=moving,
            next_candidate=fixed,
            waiver=wrong,
        )
    waiver = record_reproducibility_waiver(
        candidate_fingerprint=moving.fingerprint,
        approved_by=APPROVER,
        reason="first fixed cutover from provider-managed alias",
        created_at=NOW,
    )
    original = (root / "latin_manuscript.yaml").read_bytes().decode("utf-8")
    proposal = propose_recipe_change(
        report=report,
        recipe_root=root,
        recipe="latin_manuscript",
        station="read",
        current_candidate=moving,
        next_candidate=fixed,
        waiver=waiver,
    )

    start = original.index("  - station: read")
    end = original.index("  - station: survey")
    proposed_end = proposal.proposed_source.index("  - station: survey")
    assert proposal.proposed_source[:start] == original[:start]
    assert proposal.proposed_source[proposed_end:] == original[end:]
    changed = proposal.proposed_source[start:proposed_end]
    assert fixed.model in changed
    assert "${PALIMPSEST_MODEL_READING}" not in changed
    assert proposal.waiver_fingerprint == waiver.waiver_fingerprint


def test_manual_review_rejects_two_nonautomatic_identities_and_other_blockers(
    tmp_path: Path,
) -> None:
    moving_baseline = _candidate(
        "read/moving-baseline",
        "1",
        model="qwen3.8-max-latest",
        model_identity="moving",
    )
    moving_challenger = _candidate(
        "read/moving-challenger",
        "2",
        model="qwen3.9-max-latest",
        model_identity="moving",
    )
    root = _recipe_root(tmp_path, moving_baseline)
    reasons = [
        "baseline identity requires reproducibility waiver: "
        f"{moving_baseline.fingerprint}",
        "challenger identity requires reproducibility waiver: "
        f"{moving_challenger.fingerprint}",
    ]
    report = _report(
        moving_baseline,
        moving_challenger,
        decision="manual_review_required",
        reasons=reasons,
    )
    waiver = record_reproducibility_waiver(
        candidate_fingerprint=moving_baseline.fingerprint,
        approved_by=APPROVER,
        reason="cannot waive two identities",
        created_at=NOW,
    )
    with pytest.raises(PromotionError, match="exactly one"):
        propose_recipe_change(
            report=report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=moving_baseline,
            next_candidate=moving_challenger,
            waiver=waiver,
        )

    fixed = _candidate("read/fixed", "2", model="qwen3.8-max-002")
    blocker_report = _report(
        moving_baseline,
        fixed,
        decision="manual_review_required",
        reasons=[
            reasons[0],
            "primary metric effect is insufficient",
        ],
    )
    with pytest.raises(PromotionError, match="unexpected promotion blockers"):
        propose_recipe_change(
            report=blocker_report,
            recipe_root=root,
            recipe="latin_manuscript",
            station="read",
            current_candidate=moving_baseline,
            next_candidate=fixed,
            waiver=waiver,
        )


def test_promotion_and_exact_rollback_are_immutable(
    tmp_path: Path,
) -> None:
    root, baseline, challenger, proposal = _proposal(tmp_path)
    assert (
        _apply_recipe_proposal(proposal, recipe_root=root)
        == proposal.proposed_recipe_hash
    )
    promotion = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )

    rollback_proposal = create_rollback_proposal(
        promotion,
        recipe_root=root,
        current_candidate=challenger,
        previous_candidate=baseline,
    )
    assert rollback_proposal.previous_candidate_fingerprint == challenger.fingerprint
    assert rollback_proposal.next_candidate_fingerprint == baseline.fingerprint
    _apply_recipe_proposal(rollback_proposal, recipe_root=root)
    rollback = create_rollback_record(
        rollback_proposal,
        promotion=promotion,
        approved_by=APPROVER,
        created_at="2026-07-21T13:00:00Z",
    )
    assert rollback.source_promotion_id == promotion.promotion_id
    assert (
        rollback.previous_candidate_fingerprint == promotion.next_candidate_fingerprint
    )
    assert (
        rollback.next_candidate_fingerprint == promotion.previous_candidate_fingerprint
    )
    restored = yaml.safe_load(
        (root / "latin_manuscript.yaml").read_text(encoding="utf-8")
    )
    assert (
        next(slot for slot in restored["line"] if slot["station"] == "read")["model"]
        == baseline.model
    )
    with pytest.raises(FrozenInstanceError):
        rollback.next_candidate_fingerprint = "f" * 64  # type: ignore[misc]


def test_promotion_domain_never_triggers_paid_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid_calls: list[str] = []

    def paid_trap(*args, **kwargs):
        paid_calls.append("called")
        raise AssertionError("paid execution was triggered")

    monkeypatch.setattr(gateway, "generate", paid_trap)
    monkeypatch.setattr(gateway, "generate_json", paid_trap)
    monkeypatch.setattr(subprocess, "run", paid_trap)

    root, baseline, challenger, proposal = _proposal(tmp_path)
    _apply_recipe_proposal(proposal, recipe_root=root)
    promotion = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    rollback_proposal = create_rollback_proposal(
        promotion,
        recipe_root=root,
        current_candidate=challenger,
        previous_candidate=baseline,
    )
    _apply_recipe_proposal(rollback_proposal, recipe_root=root)
    create_rollback_record(
        rollback_proposal,
        promotion=promotion,
        approved_by=APPROVER,
        created_at="2026-07-21T13:00:00Z",
    )

    assert paid_calls == []


def test_promotion_artifacts_round_trip_canonically_without_overwrite(
    tmp_path: Path,
) -> None:
    root, _, challenger, proposal = _proposal(tmp_path)
    _apply_recipe_proposal(proposal, recipe_root=root)
    canary = _canary(proposal)
    promotion = create_promotion_record(
        proposal,
        canary=canary,
        approved_by=APPROVER,
        created_at=NOW,
    )
    waiver = record_reproducibility_waiver(
        candidate_fingerprint=challenger.fingerprint,
        approved_by=APPROVER,
        reason="record serialization proof",
        created_at=NOW,
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    proposal_path = save_recipe_proposal(artifacts / "proposal.json", proposal)
    canary_path = save_canary_evidence(artifacts / "canary.json", canary)
    promotion_path = save_promotion_record(artifacts / "promotion.json", promotion)
    waiver_path = save_reproducibility_waiver(artifacts / "waiver.json", waiver)

    assert load_recipe_proposal(proposal_path) == proposal
    assert load_canary_evidence(canary_path) == canary
    assert load_promotion_record(promotion_path) == promotion
    assert load_reproducibility_waiver(waiver_path) == waiver
    assert proposal_path.read_text(encoding="utf-8") == canonical_json(
        {
            "proposal_id": proposal.proposal_id,
            **{
                name: getattr(proposal, name)
                for name in proposal.__slots__
                if name != "proposal_id"
            },
        }
    )
    original = promotion_path.read_bytes()
    with pytest.raises(PromotionError, match="already exists"):
        save_promotion_record(promotion_path, promotion)
    assert promotion_path.read_bytes() == original


def test_artifact_loading_rejects_duplicate_unknown_and_tampered_content(
    tmp_path: Path,
) -> None:
    _, _, challenger, proposal = _proposal(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    proposal_path = save_recipe_proposal(artifacts / "proposal.json", proposal)
    duplicate = artifacts / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(PromotionError, match="Duplicate"):
        load_recipe_proposal(duplicate)

    proposal_data = json.loads(proposal_path.read_text(encoding="utf-8"))
    noncanonical = artifacts / "noncanonical.json"
    noncanonical.write_text(json.dumps(proposal_data, indent=2), encoding="utf-8")
    with pytest.raises(PromotionError, match="canonical"):
        load_recipe_proposal(noncanonical)

    proposal_data["unexpected"] = True
    unknown = artifacts / "unknown.json"
    unknown.write_text(canonical_json(proposal_data), encoding="utf-8")
    with pytest.raises(PromotionError, match="unknown"):
        load_recipe_proposal(unknown)

    waiver = record_reproducibility_waiver(
        candidate_fingerprint=challenger.fingerprint,
        approved_by=APPROVER,
        reason="original reason",
        created_at=NOW,
    )
    waiver_path = save_reproducibility_waiver(artifacts / "waiver.json", waiver)
    waiver_data = json.loads(waiver_path.read_text(encoding="utf-8"))
    waiver_data["reason"] = "tampered reason"
    tampered = artifacts / "tampered.json"
    tampered.write_text(canonical_json(waiver_data), encoding="utf-8")
    with pytest.raises(PromotionError, match="invalid"):
        load_reproducibility_waiver(tampered)


def test_append_only_history_persists_exact_records_and_maps_to_store(
    tmp_path: Path,
) -> None:
    root, baseline, challenger, proposal = _proposal(tmp_path)
    _apply_recipe_proposal(proposal, recipe_root=root)
    promotion = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    rollback_proposal = create_rollback_proposal(
        promotion,
        recipe_root=root,
        current_candidate=challenger,
        previous_candidate=baseline,
    )
    _apply_recipe_proposal(rollback_proposal, recipe_root=root)
    rollback = create_rollback_record(
        rollback_proposal,
        promotion=promotion,
        approved_by=APPROVER,
        created_at="2026-07-21T13:00:00Z",
    )
    history_root = tmp_path / "promotion-history"
    orphan_root = tmp_path / "orphan-history"
    with pytest.raises(PromotionError, match="missing promote"):
        _append_promotion_record(orphan_root, rollback)
    assert load_promotion_history(orphan_root) == ()

    promotion_path = _append_promotion_record(history_root, promotion)
    rollback_path = _append_promotion_record(history_root, rollback)

    assert promotion_path.name == f"{promotion.promotion_id}.json"
    assert rollback_path.name == f"{rollback.promotion_id}.json"
    assert load_promotion_history(history_root) == (promotion, rollback)
    with pytest.raises(PromotionError, match="already exists"):
        _append_promotion_record(history_root, promotion)
    indexed = to_evaluation_promotion_index(promotion)
    assert indexed.promotion_id == promotion.promotion_id
    assert indexed.previous_candidate_fingerprint == baseline.fingerprint
    assert indexed.next_candidate_fingerprint == challenger.fingerprint
    assert indexed.canary_run == promotion.canary.run_id


def test_commit_recovers_from_failure_before_recipe_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _, proposal = _proposal(tmp_path)
    record = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    history_root = tmp_path / "history"
    source = root / "latin_manuscript.yaml"
    original = source.read_bytes()
    real_apply = promotion_domain._apply_recipe_proposal

    def fail_before_apply(*args, **kwargs):
        raise RuntimeError("injected before CAS")

    monkeypatch.setattr(promotion_domain, "_apply_recipe_proposal", fail_before_apply)
    with pytest.raises(RuntimeError, match="before CAS"):
        commit_recipe_decision(
            proposal,
            record,
            recipe_root=root,
            history_root=history_root,
        )

    assert source.read_bytes() == original
    assert load_promotion_history(history_root) == ()
    assert (history_root / ".pending" / f"{record.promotion_id}.json").is_file()

    monkeypatch.setattr(promotion_domain, "_apply_recipe_proposal", real_apply)
    final = commit_recipe_decision(
        proposal,
        record,
        recipe_root=root,
        history_root=history_root,
    )
    assert load_promotion_record(final) == record
    assert source.read_bytes().decode("utf-8") == proposal.proposed_source


def test_commit_recovers_from_failure_after_recipe_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _, proposal = _proposal(tmp_path)
    record = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    history_root = tmp_path / "history"
    real_append = promotion_domain._append_promotion_record

    def fail_before_final(*args, **kwargs):
        raise RuntimeError("injected after CAS")

    monkeypatch.setattr(promotion_domain, "_append_promotion_record", fail_before_final)
    with pytest.raises(RuntimeError, match="after CAS"):
        commit_recipe_decision(
            proposal,
            record,
            recipe_root=root,
            history_root=history_root,
        )

    assert (root / "latin_manuscript.yaml").read_bytes().decode(
        "utf-8"
    ) == proposal.proposed_source
    assert load_promotion_history(history_root) == ()

    monkeypatch.setattr(promotion_domain, "_append_promotion_record", real_append)
    final = commit_recipe_decision(
        proposal,
        record,
        recipe_root=root,
        history_root=history_root,
    )
    assert load_promotion_record(final) == record


def test_commit_recovers_after_final_publication_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _, proposal = _proposal(tmp_path)
    record = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    history_root = tmp_path / "history"
    real_clear = promotion_domain._clear_pending_intent

    def fail_after_final(*args, **kwargs):
        raise RuntimeError("injected after final")

    monkeypatch.setattr(promotion_domain, "_clear_pending_intent", fail_after_final)
    with pytest.raises(RuntimeError, match="after final"):
        commit_recipe_decision(
            proposal,
            record,
            recipe_root=root,
            history_root=history_root,
        )

    assert load_promotion_history(history_root) == (record,)
    pending = history_root / ".pending" / f"{record.promotion_id}.json"
    assert pending.is_file()

    monkeypatch.setattr(promotion_domain, "_clear_pending_intent", real_clear)
    recovered = commit_recipe_decision(
        proposal,
        record,
        recipe_root=root,
        history_root=history_root,
    )
    assert not pending.exists()
    assert (
        commit_recipe_decision(
            proposal,
            record,
            recipe_root=root,
            history_root=history_root,
        )
        == recovered
    )
    assert load_promotion_history(history_root) == (record,)


def test_commit_supports_exact_rollback_decisions(tmp_path: Path) -> None:
    root, baseline, challenger, proposal = _proposal(tmp_path)
    promotion = create_promotion_record(
        proposal,
        canary=_canary(proposal),
        approved_by=APPROVER,
        created_at=NOW,
    )
    history_root = tmp_path / "history"
    commit_recipe_decision(
        proposal,
        promotion,
        recipe_root=root,
        history_root=history_root,
    )
    rollback_proposal = create_rollback_proposal(
        promotion,
        recipe_root=root,
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
        recipe_root=root,
        history_root=history_root,
    )

    assert load_promotion_history(history_root) == (promotion, rollback)
    restored = yaml.safe_load(
        (root / "latin_manuscript.yaml").read_text(encoding="utf-8")
    )
    assert (
        next(slot for slot in restored["line"] if slot["station"] == "read")["model"]
        == baseline.model
    )
