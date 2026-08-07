"""Pure promotion decisions and compare-and-swap recipe source changes.

This module consumes evaluation and canary evidence produced elsewhere.  It never
runs candidates, invokes a model gateway, refreshes production, or writes the
evaluation index.  The caller must explicitly perform and record those actions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping, Sequence

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from palimpsest.factory.core import recipe as production_recipe

from palimpsest.factory.evaluation import _record
from palimpsest.factory.evaluation.candidate import (
    RecordError,
    ResolvedCandidate,
    canonical_json,
    default_model_identity,
    content_fingerprint,
)
from palimpsest.factory.evaluation.report import report_fingerprint
from palimpsest.factory.evaluation.store import EvaluationPromotionIndex

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RECIPE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_UNKNOWN = {"missing", "not_available", "unavailable", "unknown"}
_PENDING_DIRECTORY = ".pending"


class PromotionError(RecordError):
    """Promotion evidence or a recipe compare-and-swap is invalid."""


_UNIQUE_JSON_OBJECT = _record.make_duplicate_key_json_hook(PromotionError)


@dataclass(frozen=True, slots=True)
class VerifiedReport:
    decision: str
    run_id: str
    report_fingerprint: str
    baseline_id: str
    baseline_fingerprint: str
    challenger_id: str
    challenger_fingerprint: str


@dataclass(frozen=True, slots=True)
class ReproducibilityWaiver:
    schema_version: int
    waiver_id: str
    candidate_fingerprint: str
    approved_by: str
    reason: str
    created_at: str
    waiver_fingerprint: str


@dataclass(frozen=True, slots=True)
class CanaryCostWaiver:
    schema_version: int
    waiver_id: str
    canary_fingerprint: str
    approved_by: str
    reason: str
    created_at: str
    waiver_fingerprint: str


@dataclass(frozen=True, slots=True)
class CanaryOutcome:
    name: str
    status: Literal["passed", "failed", "unknown"]
    required: bool = True

    def __post_init__(self) -> None:
        _nonempty(self.name, field="canary outcome name")
        if self.status not in {"passed", "failed", "unknown"}:
            raise PromotionError(f"Invalid canary outcome status: {self.status!r}")
        if type(self.required) is not bool:
            raise PromotionError("Canary outcome required must be a boolean")


@dataclass(frozen=True, slots=True)
class CanaryEvidence:
    schema_version: int
    work_order_id: str
    doc_id: str
    run_id: str
    recipe_hash: str
    refreshed_station: str
    status: Literal["passed", "failed", "unknown"]
    downstream_outcomes: tuple[CanaryOutcome, ...]
    known_cost_usd: float
    unknown_cost: bool
    book_valid: bool | None
    epub_valid: bool | None
    site_valid: bool | None
    human_review_required: bool
    human_review_passed: bool | None
    canary_fingerprint: str


@dataclass(frozen=True, slots=True)
class RecipeProposal:
    schema_version: int
    proposal_id: str
    action: Literal["promote", "rollback"]
    recipe: str
    station: str
    current_recipe_hash: str
    proposed_recipe_hash: str
    previous_candidate: str
    previous_candidate_fingerprint: str
    next_candidate: str
    next_candidate_fingerprint: str
    evaluation_run: str
    report_fingerprint: str
    source_promotion_id: str | None
    waiver_fingerprint: str | None
    proposed_source: str

    def __post_init__(self) -> None:
        _validate_recipe_name(self.recipe)
        _nonempty(self.station, field="station")
        for field, value in (
            ("proposal_id", self.proposal_id),
            ("current_recipe_hash", self.current_recipe_hash),
            ("proposed_recipe_hash", self.proposed_recipe_hash),
            ("previous_candidate_fingerprint", self.previous_candidate_fingerprint),
            ("next_candidate_fingerprint", self.next_candidate_fingerprint),
            ("report_fingerprint", self.report_fingerprint),
        ):
            _digest(value, field=field)
        if self.action not in {"promote", "rollback"}:
            raise PromotionError(f"Invalid proposal action: {self.action!r}")


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    schema_version: int
    promotion_id: str
    action: Literal["promote", "rollback"]
    recipe: str
    station: str
    previous_candidate: str
    previous_candidate_fingerprint: str
    next_candidate: str
    next_candidate_fingerprint: str
    evaluation_run: str
    report_fingerprint: str
    approved_by: str
    created_at: str
    canary: CanaryEvidence | None
    source_promotion_id: str | None
    waiver_fingerprint: str | None
    canary_cost_waiver: CanaryCostWaiver | None


def verify_qualified_report(report: Mapping[str, object]) -> VerifiedReport:
    """Verify and snapshot identities from a completed qualified report."""

    return _verify_report_decision(
        report,
        expected_decision="qualified",
        expected_reasons=(),
    )


def _verify_report_decision(
    report: Mapping[str, object],
    *,
    expected_decision: str,
    expected_reasons: tuple[str, ...],
) -> VerifiedReport:
    if not isinstance(report, Mapping):
        raise PromotionError("Evaluation report must be a mapping")
    if report.get("schema_version") != _SCHEMA_VERSION:
        raise PromotionError("Evaluation report schema_version must be integer 1")
    claimed = report.get("report_fingerprint")
    if not isinstance(claimed, str) or not _SHA256.fullmatch(claimed):
        raise PromotionError("Evaluation report fingerprint is invalid")
    if report_fingerprint(report) != claimed:
        raise PromotionError("Evaluation report fingerprint does not match its content")
    if report.get("status") != "completed":
        raise PromotionError("Only completed evaluation reports can be promoted")
    if report.get("decision") != expected_decision:
        raise PromotionError(
            f"Evaluation report decision must be {expected_decision!r}"
        )

    qualification = report.get("qualification")
    if not isinstance(qualification, Mapping):
        raise PromotionError("Evaluation report qualification is missing")
    if qualification.get("decision") != expected_decision:
        raise PromotionError("Evaluation report qualification decision does not match")
    reasons = qualification.get("reasons")
    if not isinstance(reasons, (list, tuple)) or tuple(reasons) != expected_reasons:
        raise PromotionError("Evaluation report has unexpected promotion blockers")

    for field in ("aggregates", "downstream_probes"):
        _reject_marked_required_unknown(report.get(field), field=field)

    baseline = _report_identity(report.get("baseline"), field="report.baseline")
    challenger = _report_identity(report.get("challenger"), field="report.challenger")
    return VerifiedReport(
        decision=expected_decision,
        run_id=_nonempty(report.get("run_id"), field="report.run_id"),
        report_fingerprint=claimed,
        baseline_id=baseline[0],
        baseline_fingerprint=baseline[1],
        challenger_id=challenger[0],
        challenger_fingerprint=challenger[1],
    )


def record_reproducibility_waiver(
    *, candidate_fingerprint: str, approved_by: str, reason: str, created_at: str
) -> ReproducibilityWaiver:
    """Create immutable, content-identified approval for one exact candidate."""

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "candidate_fingerprint": _digest(
            candidate_fingerprint, field="waiver candidate_fingerprint"
        ),
        "approved_by": _nonempty(approved_by, field="waiver approved_by"),
        "reason": _nonempty(reason, field="waiver reason"),
        "created_at": _timestamp(created_at, field="waiver created_at"),
    }
    fingerprint = content_fingerprint(payload)
    return ReproducibilityWaiver(
        **payload,
        waiver_id=fingerprint,
        waiver_fingerprint=fingerprint,
    )


def record_canary_cost_waiver(
    *, canary_fingerprint: str, approved_by: str, reason: str, created_at: str
) -> CanaryCostWaiver:
    """Approve unknown cost for one reviewed terminal canary."""

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "canary_fingerprint": _digest(
            canary_fingerprint, field="cost waiver canary_fingerprint"
        ),
        "approved_by": _nonempty(approved_by, field="cost waiver approved_by"),
        "reason": _nonempty(reason, field="cost waiver reason"),
        "created_at": _timestamp(created_at, field="cost waiver created_at"),
    }
    fingerprint = content_fingerprint(payload)
    return CanaryCostWaiver(
        **payload,
        waiver_id=fingerprint,
        waiver_fingerprint=fingerprint,
    )


def propose_recipe_change(
    *,
    report: Mapping[str, object],
    recipe_root: str | Path,
    recipe: str,
    station: str,
    current_candidate: ResolvedCandidate,
    next_candidate: ResolvedCandidate,
    waiver: ReproducibilityWaiver | None = None,
) -> RecipeProposal:
    """Create a qualified recipe proposal without applying or executing it."""

    _validate_candidate_pair(current_candidate, next_candidate, station=station)
    nonautomatic = [
        (side, candidate)
        for side, candidate in (
            ("baseline", current_candidate),
            ("challenger", next_candidate),
        )
        if not candidate.can_auto_qualify
    ]
    if report.get("decision") == "manual_review_required":
        if len(nonautomatic) != 1:
            raise PromotionError(
                "Manual review requires exactly one moving or untracked identity"
            )
        side, waived_candidate = nonautomatic[0]
        reason = (
            f"{side} identity requires reproducibility waiver: "
            f"{waived_candidate.fingerprint}"
        )
        verified = _verify_report_decision(
            report,
            expected_decision="manual_review_required",
            expected_reasons=(reason,),
        )
        if waiver is None:
            raise PromotionError(
                "Manual review requires an explicit reproducibility waiver"
            )
        _verify_waiver(waiver, waived_candidate.fingerprint)
        waiver_fingerprint = waiver.waiver_fingerprint
        allow_nonautomatic_current = side == "baseline"
    else:
        verified = verify_qualified_report(report)
        if not current_candidate.can_auto_qualify:
            raise PromotionError(
                "A moving or untracked baseline requires manual review"
            )
        waiver_fingerprint = _qualification_waiver(next_candidate, waiver)
        allow_nonautomatic_current = False
    if (
        verified.baseline_id != current_candidate.id
        or verified.baseline_fingerprint != current_candidate.fingerprint
    ):
        raise PromotionError("Report baseline does not match the current candidate")
    if (
        verified.challenger_id != next_candidate.id
        or verified.challenger_fingerprint != next_candidate.fingerprint
    ):
        raise PromotionError("Report challenger does not match the proposed candidate")

    return _build_recipe_proposal(
        action="promote",
        recipe_root=recipe_root,
        recipe=recipe,
        station=station,
        current_candidate=current_candidate,
        next_candidate=next_candidate,
        evaluation_run=verified.run_id,
        report_fingerprint=verified.report_fingerprint,
        source_promotion_id=None,
        waiver_fingerprint=waiver_fingerprint,
        allow_nonautomatic_current=allow_nonautomatic_current,
    )


def _apply_recipe_proposal(proposal: RecipeProposal, *, recipe_root: str | Path) -> str:
    """Atomically apply a proposal only if the named recipe source is unchanged."""

    _verify_proposal(proposal)
    source = _recipe_source(recipe_root, proposal.recipe)
    current = source.read_bytes()
    current_hash = _source_hash(current)
    if current_hash != proposal.current_recipe_hash:
        raise PromotionError(
            "Recipe changed after proposal generation; compare-and-swap refused"
        )

    proposed = proposal.proposed_source.encode("utf-8")
    if _source_hash(proposed) != proposal.proposed_recipe_hash:
        raise PromotionError("Proposal source does not match its proposed recipe hash")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{source.name}.",
            suffix=".tmp",
            dir=source.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(proposed)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise PromotionError(f"Recipe source is not writable: {source}") from error
    return proposal.proposed_recipe_hash


def record_canary_evidence(
    *,
    work_order_id: str,
    doc_id: str,
    run_id: str,
    recipe_hash: str,
    refreshed_station: str,
    status: Literal["passed", "failed", "unknown"],
    downstream_outcomes: Sequence[CanaryOutcome],
    known_cost_usd: float,
    unknown_cost: bool,
    book_valid: bool | None,
    epub_valid: bool | None,
    site_valid: bool | None,
    human_review_required: bool = False,
    human_review_passed: bool | None = None,
) -> CanaryEvidence:
    """Record externally-produced canary evidence; no canary is run here."""

    if status not in {"passed", "failed", "unknown"}:
        raise PromotionError(f"Invalid canary status: {status!r}")
    if isinstance(known_cost_usd, bool) or not isinstance(known_cost_usd, (int, float)):
        raise PromotionError(
            "Canary known_cost_usd must be a finite non-negative number"
        )
    known_cost = float(known_cost_usd)
    if not math.isfinite(known_cost) or known_cost < 0:
        raise PromotionError(
            "Canary known_cost_usd must be a finite non-negative number"
        )
    outcomes = tuple(downstream_outcomes)
    if not all(isinstance(outcome, CanaryOutcome) for outcome in outcomes):
        raise PromotionError("Canary downstream outcomes must be CanaryOutcome records")
    if type(unknown_cost) is not bool:
        raise PromotionError("Canary unknown_cost must be a boolean")
    if type(human_review_required) is not bool:
        raise PromotionError("Canary human_review_required must be a boolean")
    for name, value in (
        ("book_valid", book_valid),
        ("epub_valid", epub_valid),
        ("site_valid", site_valid),
        ("human_review_passed", human_review_passed),
    ):
        if value is not None and type(value) is not bool:
            raise PromotionError(f"Canary {name} must be a boolean or null")

    fields = {
        "schema_version": _SCHEMA_VERSION,
        "work_order_id": _nonempty(work_order_id, field="canary work_order_id"),
        "doc_id": _nonempty(doc_id, field="canary doc_id"),
        "run_id": _nonempty(run_id, field="canary run_id"),
        "recipe_hash": _digest(recipe_hash, field="canary recipe_hash"),
        "refreshed_station": _nonempty(
            refreshed_station, field="canary refreshed_station"
        ),
        "status": status,
        "downstream_outcomes": outcomes,
        "known_cost_usd": known_cost,
        "unknown_cost": unknown_cost,
        "book_valid": book_valid,
        "epub_valid": epub_valid,
        "site_valid": site_valid,
        "human_review_required": human_review_required,
        "human_review_passed": human_review_passed,
    }
    fingerprint = content_fingerprint(_canary_payload(fields))
    return CanaryEvidence(**fields, canary_fingerprint=fingerprint)


def create_promotion_record(
    proposal: RecipeProposal,
    *,
    canary: CanaryEvidence | None,
    approved_by: str,
    created_at: str,
    cost_waiver: CanaryCostWaiver | None = None,
) -> PromotionRecord:
    """Create a final promote decision from reviewed matching canary evidence."""

    _verify_proposal(proposal)
    if proposal.action != "promote":
        raise PromotionError("A promotion record requires a promote proposal")
    if canary is None:
        raise PromotionError("A passing canary is required for promotion")
    approver = _nonempty(approved_by, field="approved_by")
    _verify_passing_canary(
        canary,
        proposal,
        cost_waiver=cost_waiver,
        approved_by=approver,
    )
    return _decision_record(
        action="promote",
        proposal=proposal,
        approved_by=approver,
        created_at=created_at,
        canary=canary,
        source_promotion_id=None,
        canary_cost_waiver=cost_waiver,
    )


def create_rollback_proposal(
    promotion: PromotionRecord,
    *,
    recipe_root: str | Path,
    current_candidate: ResolvedCandidate,
    previous_candidate: ResolvedCandidate,
) -> RecipeProposal:
    """Propose restoring the exact prior candidate named by a promotion record."""

    _verify_decision_record(promotion)
    if promotion.action != "promote":
        raise PromotionError("Rollback must name an exact promote record")
    if (
        current_candidate.id != promotion.next_candidate
        or current_candidate.fingerprint != promotion.next_candidate_fingerprint
        or previous_candidate.id != promotion.previous_candidate
        or previous_candidate.fingerprint != promotion.previous_candidate_fingerprint
    ):
        raise PromotionError("Rollback candidates do not exactly match the promotion")
    return _build_recipe_proposal(
        action="rollback",
        recipe_root=recipe_root,
        recipe=promotion.recipe,
        station=promotion.station,
        current_candidate=current_candidate,
        next_candidate=previous_candidate,
        evaluation_run=promotion.evaluation_run,
        report_fingerprint=promotion.report_fingerprint,
        source_promotion_id=promotion.promotion_id,
        waiver_fingerprint=None,
        allow_nonautomatic_current=True,
    )


def create_rollback_record(
    proposal: RecipeProposal,
    *,
    promotion: PromotionRecord,
    approved_by: str,
    created_at: str,
    canary: CanaryEvidence | None = None,
) -> PromotionRecord:
    """Append an inverse decision; the original promotion remains unchanged."""

    _verify_proposal(proposal)
    _verify_decision_record(promotion)
    if proposal.action != "rollback" or promotion.action != "promote":
        raise PromotionError(
            "Rollback record requires a rollback proposal and promotion"
        )
    if proposal.source_promotion_id != promotion.promotion_id:
        raise PromotionError("Rollback proposal names a different promotion")
    if (
        proposal.previous_candidate != promotion.next_candidate
        or proposal.previous_candidate_fingerprint
        != promotion.next_candidate_fingerprint
        or proposal.next_candidate != promotion.previous_candidate
        or proposal.next_candidate_fingerprint
        != promotion.previous_candidate_fingerprint
    ):
        raise PromotionError("Rollback does not restore the exact prior fingerprint")
    if canary is not None:
        _verify_canary_identity(canary, proposal)
    return _decision_record(
        action="rollback",
        proposal=proposal,
        approved_by=approved_by,
        created_at=created_at,
        canary=canary,
        source_promotion_id=promotion.promotion_id,
        canary_cost_waiver=None,
    )


def save_recipe_proposal(path: str | Path, proposal: RecipeProposal) -> Path:
    """Publish one canonical proposal artifact without overwriting any file."""

    _verify_proposal(proposal)
    return _atomic_create_json(
        path, {"proposal_id": proposal.proposal_id, **_proposal_payload(proposal)}
    )


def load_recipe_proposal(path: str | Path) -> RecipeProposal:
    """Load a strict canonical proposal and verify its content identity."""

    raw = _load_canonical_record(
        path,
        field="recipe proposal",
        keys=set(RecipeProposal.__slots__),
    )
    proposal = RecipeProposal(**raw)  # type: ignore[arg-type]
    _verify_proposal(proposal)
    return proposal


def save_canary_evidence(path: str | Path, canary: CanaryEvidence) -> Path:
    """Publish one canonical canary evidence artifact without overwriting."""

    _verify_canary_record(canary)
    return _atomic_create_json(path, _serialized_canary(canary))


def load_canary_evidence(path: str | Path) -> CanaryEvidence:
    """Load strict canonical canary evidence and verify its fingerprint."""

    raw = _load_canonical_record(
        path,
        field="canary evidence",
        keys=set(CanaryEvidence.__slots__),
    )
    return _canary_from_mapping(raw, field="canary evidence")


def save_promotion_record(path: str | Path, record: PromotionRecord) -> Path:
    """Publish one immutable promote or rollback record without overwriting."""

    _verify_decision_record(record)
    return _atomic_create_json(path, _serialized_decision(record))


def load_promotion_record(path: str | Path) -> PromotionRecord:
    """Load a strict canonical decision record and verify its content identity."""

    raw = _load_canonical_record(
        path,
        field="promotion record",
        keys=set(PromotionRecord.__slots__),
    )
    return _decision_from_mapping(raw, field="promotion record")


def save_reproducibility_waiver(
    path: str | Path, waiver: ReproducibilityWaiver
) -> Path:
    """Publish one exact-candidate waiver without overwriting any file."""

    _verify_waiver(waiver, waiver.candidate_fingerprint)
    return _atomic_create_json(
        path,
        {name: getattr(waiver, name) for name in ReproducibilityWaiver.__slots__},
    )


def load_reproducibility_waiver(path: str | Path) -> ReproducibilityWaiver:
    """Load a strict canonical waiver and verify its content identity."""

    raw = _load_canonical_record(
        path,
        field="reproducibility waiver",
        keys=set(ReproducibilityWaiver.__slots__),
    )
    waiver = ReproducibilityWaiver(**raw)  # type: ignore[arg-type]
    _verify_waiver(waiver, waiver.candidate_fingerprint)
    return waiver


def _append_promotion_record(history_root: str | Path, record: PromotionRecord) -> Path:
    """Atomically append a decision as ``<promotion_id>.json``."""

    _verify_decision_record(record)
    root = Path(history_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise PromotionError(f"Cannot create promotion history root: {root}") from error
    if not resolved_root.is_dir():
        raise PromotionError(f"Promotion history root is not a directory: {root}")
    existing = load_promotion_history(resolved_root)
    if any(item.promotion_id == record.promotion_id for item in existing):
        raise PromotionError(
            f"Immutable artifact already exists: {record.promotion_id}.json"
        )
    _validated_promotion_history((*existing, record))
    return save_promotion_record(
        resolved_root / f"{record.promotion_id}.json",
        record,
    )


def commit_recipe_decision(
    proposal: RecipeProposal,
    record: PromotionRecord,
    *,
    recipe_root: str | Path,
    history_root: str | Path,
) -> Path:
    """Recoverably commit recipe CAS and its immutable decision as one protocol.

    A durable pending intent is published before the recipe can change. A
    retry recognizes both the pre-CAS and post-CAS states and publishes the
    same content-identified final record exactly once.
    """

    _verify_recipe_decision_match(proposal, record)
    root = Path(history_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise PromotionError(f"Cannot create promotion history root: {root}") from error
    if not resolved_root.is_dir():
        raise PromotionError(f"Promotion history root is not a directory: {root}")
    pending_root = resolved_root / _PENDING_DIRECTORY
    try:
        pending_root.mkdir(exist_ok=True)
        pending_root = pending_root.resolve(strict=True)
    except OSError as error:
        raise PromotionError(
            "Cannot create pending promotion intent directory"
        ) from error
    if not pending_root.is_dir() or pending_root.parent != resolved_root:
        raise PromotionError("Pending promotion intents must remain under history_root")

    pending_path = pending_root / f"{record.promotion_id}.json"
    final_path = resolved_root / f"{record.promotion_id}.json"
    intent = _pending_intent_payload(proposal, record)

    if final_path.exists():
        existing = load_promotion_record(final_path)
        if existing != record:
            raise PromotionError(
                "Final promotion identity exists with different content"
            )
        _require_recipe_hash(
            proposal, recipe_root=recipe_root, expected=proposal.proposed_recipe_hash
        )
        if pending_path.exists():
            _verify_pending_intent(pending_path, proposal, record)
            _clear_pending_intent(pending_path)
        return final_path

    if pending_path.exists():
        _verify_pending_intent(pending_path, proposal, record)
    else:
        _atomic_create_json(pending_path, intent)
    _fsync_path(pending_path)

    current_hash = _recipe_hash(recipe_root, proposal.recipe)
    if current_hash == proposal.current_recipe_hash:
        _apply_recipe_proposal(proposal, recipe_root=recipe_root)
    elif current_hash != proposal.proposed_recipe_hash:
        raise PromotionError(
            "Recipe matches neither the pending decision's current nor proposed hash"
        )

    final_path = _append_promotion_record(resolved_root, record)
    _fsync_path(final_path)
    _clear_pending_intent(pending_path)
    return final_path


def load_promotion_history(
    history_root: str | Path,
) -> tuple[PromotionRecord, ...]:
    """Load and cross-check all immutable records in an append-only history."""

    try:
        root = Path(history_root).resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PromotionError(
            f"Promotion history root does not exist: {history_root}"
        ) from error
    if not root.is_dir():
        raise PromotionError(f"Promotion history root is not a directory: {root}")
    records: list[PromotionRecord] = []
    for path in root.iterdir():
        if path.name == _PENDING_DIRECTORY:
            if not path.is_dir() or path.resolve(strict=True).parent != root:
                raise PromotionError("Pending promotion intent directory is invalid")
            continue
        if not path.is_file() or path.suffix != ".json":
            raise PromotionError(f"Unexpected promotion history entry: {path.name}")
        record = load_promotion_record(path)
        if path.name != f"{record.promotion_id}.json":
            raise PromotionError(
                f"Promotion history filename does not match record identity: {path.name}"
            )
        records.append(record)
    return _validated_promotion_history(records)


def _validated_promotion_history(
    records: Sequence[PromotionRecord],
) -> tuple[PromotionRecord, ...]:
    ordered = tuple(
        sorted(records, key=lambda item: (item.created_at, item.promotion_id))
    )
    by_id = {record.promotion_id: record for record in ordered}
    if len(by_id) != len(ordered):
        raise PromotionError("Promotion history contains duplicate promotion IDs")
    for record in ordered:
        if record.action != "rollback":
            continue
        source = by_id.get(record.source_promotion_id)
        if source is None or source.action != "promote":
            raise PromotionError("Rollback history references a missing promote record")
        if (
            record.recipe != source.recipe
            or record.station != source.station
            or record.evaluation_run != source.evaluation_run
            or record.report_fingerprint != source.report_fingerprint
            or record.previous_candidate != source.next_candidate
            or record.previous_candidate_fingerprint
            != source.next_candidate_fingerprint
            or record.next_candidate != source.previous_candidate
            or record.next_candidate_fingerprint
            != source.previous_candidate_fingerprint
            or record.created_at < source.created_at
        ):
            raise PromotionError(
                "Rollback history does not restore its exact prior fingerprint"
            )
    return ordered


def to_evaluation_promotion_index(
    record: PromotionRecord,
) -> EvaluationPromotionIndex:
    """Convert a verified canonical decision for ``EvaluationStore.record_promotion``."""

    _verify_decision_record(record)
    return EvaluationPromotionIndex(
        promotion_id=record.promotion_id,
        action=record.action,
        recipe=record.recipe,
        station=record.station,
        previous_candidate_fingerprint=record.previous_candidate_fingerprint,
        next_candidate_fingerprint=record.next_candidate_fingerprint,
        evaluation_run=record.evaluation_run,
        canary_run=None if record.canary is None else record.canary.run_id,
        approved_by=record.approved_by,
        created_at=record.created_at,
    )


def _build_recipe_proposal(
    *,
    action: Literal["promote", "rollback"],
    recipe_root: str | Path,
    recipe: str,
    station: str,
    current_candidate: ResolvedCandidate,
    next_candidate: ResolvedCandidate,
    evaluation_run: str,
    report_fingerprint: str,
    source_promotion_id: str | None,
    waiver_fingerprint: str | None,
    allow_nonautomatic_current: bool,
) -> RecipeProposal:
    _validate_candidate_pair(current_candidate, next_candidate, station=station)
    source = _recipe_source(recipe_root, recipe)
    current_bytes = source.read_bytes()
    try:
        current_source = current_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromotionError(f"Recipe source is not valid UTF-8: {source}") from error
    _validate_resolved_current_slot(
        source=source,
        recipe=recipe,
        station=station,
        candidate=current_candidate,
        allow_nonautomatic=allow_nonautomatic_current,
    )
    start, end, indentation = _station_source_span(current_source, station)
    newline = "\r\n" if "\r\n" in current_source else "\n"
    rendered = yaml.safe_dump(
        [_candidate_slot(next_candidate)],
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).replace("\n", newline)
    replacement = "".join(
        indentation + line for line in rendered.splitlines(keepends=True)
    )
    proposed_source = current_source[:start] + replacement + current_source[end:]
    current_hash = _source_hash(current_bytes)
    proposed_hash = _source_hash(proposed_source.encode("utf-8"))
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "action": action,
        "recipe": recipe,
        "station": station,
        "current_recipe_hash": current_hash,
        "proposed_recipe_hash": proposed_hash,
        "previous_candidate": current_candidate.id,
        "previous_candidate_fingerprint": current_candidate.fingerprint,
        "next_candidate": next_candidate.id,
        "next_candidate_fingerprint": next_candidate.fingerprint,
        "evaluation_run": evaluation_run,
        "report_fingerprint": report_fingerprint,
        "source_promotion_id": source_promotion_id,
        "waiver_fingerprint": waiver_fingerprint,
        "proposed_source": proposed_source,
    }
    proposal_id = content_fingerprint(payload)
    return RecipeProposal(**payload, proposal_id=proposal_id)


def _decision_record(
    *,
    action: Literal["promote", "rollback"],
    proposal: RecipeProposal,
    approved_by: str,
    created_at: str,
    canary: CanaryEvidence | None,
    source_promotion_id: str | None,
    canary_cost_waiver: CanaryCostWaiver | None,
) -> PromotionRecord:
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "action": action,
        "recipe": proposal.recipe,
        "station": proposal.station,
        "previous_candidate": proposal.previous_candidate,
        "previous_candidate_fingerprint": proposal.previous_candidate_fingerprint,
        "next_candidate": proposal.next_candidate,
        "next_candidate_fingerprint": proposal.next_candidate_fingerprint,
        "evaluation_run": proposal.evaluation_run,
        "report_fingerprint": proposal.report_fingerprint,
        "approved_by": _nonempty(approved_by, field="approved_by"),
        "created_at": _timestamp(created_at, field="created_at"),
        "canary": canary,
        "source_promotion_id": source_promotion_id,
        "waiver_fingerprint": proposal.waiver_fingerprint,
        "canary_cost_waiver": canary_cost_waiver,
    }
    promotion_id = content_fingerprint(_decision_payload(payload))
    return PromotionRecord(**payload, promotion_id=promotion_id)


def _validate_resolved_current_slot(
    *,
    source: Path,
    recipe: str,
    station: str,
    candidate: ResolvedCandidate,
    allow_nonautomatic: bool,
) -> None:
    try:
        resolved_recipe = production_recipe.load(recipe, recipes_dir=source.parent)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise PromotionError(
            f"Current recipe cannot be fully resolved and validated: {error}"
        ) from error
    matches = [step for step in resolved_recipe.steps if step.station.name == station]
    if len(matches) != 1:
        raise PromotionError(
            f"Resolved recipe must contain station {station!r} exactly once"
        )
    step = matches[0]
    resolved_identity = (
        None if step.model is None else default_model_identity(step.model)
    )
    if (
        step.station.variant != candidate.variant
        or step.model != candidate.model
        or step.prompt_name != candidate.prompt_name
        or _plain_json(step.params) != _plain_json(candidate.params)
        or _plain_json(step.options) != _plain_json(candidate.options)
        or (
            resolved_identity is not None
            and resolved_identity != candidate.model_identity
        )
    ):
        raise PromotionError(
            "Resolved recipe station slot does not exactly match the current candidate"
        )
    if not candidate.can_auto_qualify and not allow_nonautomatic:
        raise PromotionError(
            "Current moving or untracked recipe identity requires manual review"
        )


def _station_source_span(source: str, station: str) -> tuple[int, int, str]:
    try:
        document = yaml.compose(source)
    except yaml.YAMLError as error:
        raise PromotionError("Recipe source is not valid YAML") from error
    if not isinstance(document, MappingNode):
        raise PromotionError("Recipe source must be a YAML mapping")
    line_node: SequenceNode | None = None
    for key_node, value_node in document.value:
        if (
            isinstance(key_node, ScalarNode)
            and key_node.value == "line"
            and isinstance(value_node, SequenceNode)
        ):
            line_node = value_node
            break
    if line_node is None:
        raise PromotionError("Recipe line must be an ordered list")

    matches: list[int] = []
    for index, item in enumerate(line_node.value):
        if not isinstance(item, MappingNode):
            continue
        for key_node, value_node in item.value:
            if (
                isinstance(key_node, ScalarNode)
                and key_node.value == "station"
                and isinstance(value_node, ScalarNode)
                and value_node.value == station
            ):
                matches.append(index)
                break
    if len(matches) != 1:
        raise PromotionError(f"Recipe must contain station {station!r} exactly once")

    lines = source.splitlines(keepends=True)
    offsets: list[int] = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    index = matches[0]
    station_line = line_node.value[index].start_mark.line
    start = offsets[station_line]
    end = (
        offsets[line_node.value[index + 1].start_mark.line]
        if index + 1 < len(line_node.value)
        else len(source)
    )
    line = lines[station_line]
    dash = line.find("-")
    if dash < 0 or line[:dash].strip():
        raise PromotionError("Recipe station block has unsupported indentation")
    return start, end, line[:dash]


def _candidate_slot(candidate: ResolvedCandidate) -> dict[str, object]:
    slot: dict[str, object] = {"station": candidate.station}
    if candidate.variant != "default":
        slot["variant"] = candidate.variant
    if candidate.model is not None:
        slot["model"] = candidate.model
    if candidate.prompt_name is not None:
        slot["prompt"] = candidate.prompt_name
    if candidate.params:
        slot["params"] = _plain_json(candidate.params)
    for key, value in sorted(candidate.options.items()):
        if key in slot:
            raise PromotionError(f"Candidate option conflicts with recipe key: {key}")
        slot[key] = _plain_json(value)
    return slot


def _qualification_waiver(
    candidate: ResolvedCandidate, waiver: ReproducibilityWaiver | None
) -> str | None:
    if candidate.can_auto_qualify:
        if waiver is None:
            return None
        _verify_waiver(waiver, candidate.fingerprint)
        return waiver.waiver_fingerprint
    if waiver is None:
        raise PromotionError(
            "Moving or untracked candidates require an explicit reproducibility waiver"
        )
    _verify_waiver(waiver, candidate.fingerprint)
    return waiver.waiver_fingerprint


def _verify_waiver(waiver: ReproducibilityWaiver, fingerprint: str) -> None:
    if not isinstance(waiver, ReproducibilityWaiver):
        raise PromotionError("Reproducibility waiver has the wrong record type")
    _schema(waiver.schema_version, field="waiver.schema_version")
    _digest(waiver.candidate_fingerprint, field="waiver.candidate_fingerprint")
    _nonempty(waiver.approved_by, field="waiver.approved_by")
    _nonempty(waiver.reason, field="waiver.reason")
    _timestamp(waiver.created_at, field="waiver.created_at")
    _digest(waiver.waiver_id, field="waiver.waiver_id")
    _digest(waiver.waiver_fingerprint, field="waiver.waiver_fingerprint")
    payload = {
        "schema_version": waiver.schema_version,
        "candidate_fingerprint": waiver.candidate_fingerprint,
        "approved_by": waiver.approved_by,
        "reason": waiver.reason,
        "created_at": waiver.created_at,
    }
    expected = content_fingerprint(payload)
    if (
        waiver.schema_version != _SCHEMA_VERSION
        or waiver.candidate_fingerprint != fingerprint
        or waiver.waiver_id != expected
        or waiver.waiver_fingerprint != expected
    ):
        raise PromotionError(
            "Reproducibility waiver is invalid or for another candidate"
        )


def _verify_passing_canary(
    canary: CanaryEvidence,
    proposal: RecipeProposal,
    *,
    cost_waiver: CanaryCostWaiver | None,
    approved_by: str,
) -> None:
    _verify_canary_identity(canary, proposal)
    blocking = [
        outcome.name
        for outcome in canary.downstream_outcomes
        if outcome.required and outcome.status != "passed"
    ]
    if blocking:
        raise PromotionError(f"Required canary outcomes did not pass: {blocking}")
    for name, value in (
        ("book", canary.book_valid),
        ("EPUB", canary.epub_valid),
        ("site", canary.site_valid),
    ):
        if value is not True:
            state = "unknown" if value is None else "failed"
            raise PromotionError(f"Canary {name} validation {state}")
    if canary.human_review_required and canary.human_review_passed is not True:
        raise PromotionError("Required canary human review did not pass")
    if canary.status == "failed":
        raise PromotionError("Only a passing canary allows promotion")
    if canary.unknown_cost:
        if canary.status != "unknown":
            raise PromotionError("Unknown canary cost must produce unknown status")
        if cost_waiver is None:
            raise PromotionError("Canary cost evidence is unknown")
        _verify_cost_waiver(
            cost_waiver,
            canary_fingerprint=canary.canary_fingerprint,
            approved_by=approved_by,
        )
    else:
        if cost_waiver is not None:
            raise PromotionError("Canary cost waiver requires unknown cost evidence")
        if canary.status != "passed":
            raise PromotionError("Only a passing canary allows promotion")


def _verify_cost_waiver(
    waiver: CanaryCostWaiver,
    *,
    canary_fingerprint: str,
    approved_by: str,
) -> None:
    if not isinstance(waiver, CanaryCostWaiver):
        raise PromotionError("Canary cost waiver has the wrong record type")
    _schema(waiver.schema_version, field="cost waiver.schema_version")
    _digest(waiver.canary_fingerprint, field="cost waiver.canary_fingerprint")
    _nonempty(waiver.approved_by, field="cost waiver.approved_by")
    _nonempty(waiver.reason, field="cost waiver.reason")
    _timestamp(waiver.created_at, field="cost waiver.created_at")
    _digest(waiver.waiver_id, field="cost waiver.waiver_id")
    _digest(waiver.waiver_fingerprint, field="cost waiver.waiver_fingerprint")
    payload = {
        "schema_version": waiver.schema_version,
        "canary_fingerprint": waiver.canary_fingerprint,
        "approved_by": waiver.approved_by,
        "reason": waiver.reason,
        "created_at": waiver.created_at,
    }
    expected = content_fingerprint(payload)
    if (
        waiver.schema_version != _SCHEMA_VERSION
        or waiver.canary_fingerprint != canary_fingerprint
        or waiver.approved_by != approved_by
        or waiver.waiver_id != expected
        or waiver.waiver_fingerprint != expected
    ):
        raise PromotionError(
            "Canary cost waiver is invalid, has another approver, or names another canary"
        )


def _verify_canary_identity(canary: CanaryEvidence, proposal: RecipeProposal) -> None:
    _verify_canary_record(canary)
    if canary.recipe_hash != proposal.proposed_recipe_hash:
        raise PromotionError("Canary used a different resolved recipe hash")
    if canary.refreshed_station != proposal.station:
        raise PromotionError("Canary refreshed a different station")


def _canary_payload(
    evidence: CanaryEvidence | Mapping[str, object],
) -> dict[str, object]:
    def value(name: str) -> object:
        return (
            evidence[name] if isinstance(evidence, Mapping) else getattr(evidence, name)
        )

    outcomes = value("downstream_outcomes")
    return {
        "schema_version": value("schema_version"),
        "work_order_id": value("work_order_id"),
        "doc_id": value("doc_id"),
        "run_id": value("run_id"),
        "recipe_hash": value("recipe_hash"),
        "refreshed_station": value("refreshed_station"),
        "status": value("status"),
        "downstream_outcomes": [
            {
                "name": outcome.name,
                "status": outcome.status,
                "required": outcome.required,
            }
            for outcome in outcomes  # type: ignore[union-attr]
        ],
        "known_cost_usd": value("known_cost_usd"),
        "unknown_cost": value("unknown_cost"),
        "book_valid": value("book_valid"),
        "epub_valid": value("epub_valid"),
        "site_valid": value("site_valid"),
        "human_review_required": value("human_review_required"),
        "human_review_passed": value("human_review_passed"),
    }


def _decision_payload(payload: Mapping[str, object]) -> dict[str, object]:
    result = dict(payload)
    canary = result.get("canary")
    if isinstance(canary, CanaryEvidence):
        result["canary"] = {
            **_canary_payload(canary),
            "canary_fingerprint": canary.canary_fingerprint,
        }
    cost_waiver = result.get("canary_cost_waiver")
    if isinstance(cost_waiver, CanaryCostWaiver):
        result["canary_cost_waiver"] = {
            name: getattr(cost_waiver, name)
            for name in CanaryCostWaiver.__slots__
        }
    return result


def _serialized_canary(canary: CanaryEvidence) -> dict[str, object]:
    return {
        **_canary_payload(canary),
        "canary_fingerprint": canary.canary_fingerprint,
    }


def _serialized_decision(record: PromotionRecord) -> dict[str, object]:
    payload = {
        name: getattr(record, name)
        for name in PromotionRecord.__slots__
        if name != "promotion_id"
    }
    return {
        "promotion_id": record.promotion_id,
        **_decision_payload(payload),
    }


def _pending_intent_payload(
    proposal: RecipeProposal, record: PromotionRecord
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "proposal": {
            "proposal_id": proposal.proposal_id,
            **_proposal_payload(proposal),
        },
        "record": _serialized_decision(record),
    }


def _verify_pending_intent(
    path: Path, proposal: RecipeProposal, record: PromotionRecord
) -> None:
    raw = _load_canonical_record(
        path,
        field="pending promotion intent",
        keys={"schema_version", "proposal", "record"},
    )
    _schema(raw.get("schema_version"), field="pending intent.schema_version")
    raw_proposal = raw.get("proposal")
    raw_record = raw.get("record")
    if not isinstance(raw_proposal, Mapping) or not isinstance(raw_record, Mapping):
        raise PromotionError("Pending promotion intent records must be objects")
    _strict_keys(
        raw_proposal,
        set(RecipeProposal.__slots__),
        field="pending intent proposal",
    )
    pending_proposal = RecipeProposal(**dict(raw_proposal))  # type: ignore[arg-type]
    _verify_proposal(pending_proposal)
    pending_record = _decision_from_mapping(raw_record, field="pending intent record")
    if pending_proposal != proposal or pending_record != record:
        raise PromotionError("Pending promotion intent differs from requested decision")


def _verify_recipe_decision_match(
    proposal: RecipeProposal, record: PromotionRecord
) -> None:
    _verify_proposal(proposal)
    _verify_decision_record(record)
    if (
        record.action != proposal.action
        or record.recipe != proposal.recipe
        or record.station != proposal.station
        or record.previous_candidate != proposal.previous_candidate
        or record.previous_candidate_fingerprint
        != proposal.previous_candidate_fingerprint
        or record.next_candidate != proposal.next_candidate
        or record.next_candidate_fingerprint != proposal.next_candidate_fingerprint
        or record.evaluation_run != proposal.evaluation_run
        or record.report_fingerprint != proposal.report_fingerprint
        or record.source_promotion_id != proposal.source_promotion_id
        or record.waiver_fingerprint != proposal.waiver_fingerprint
    ):
        raise PromotionError("Promotion record does not match its recipe proposal")
    if record.action == "promote":
        assert record.canary is not None
        _verify_passing_canary(
            record.canary,
            proposal,
            cost_waiver=record.canary_cost_waiver,
            approved_by=record.approved_by,
        )
    elif record.canary is not None:
        _verify_canary_identity(record.canary, proposal)


def _recipe_hash(recipe_root: str | Path, recipe: str) -> str:
    return _source_hash(_recipe_source(recipe_root, recipe).read_bytes())


def _require_recipe_hash(
    proposal: RecipeProposal, *, recipe_root: str | Path, expected: str
) -> None:
    if _recipe_hash(recipe_root, proposal.recipe) != expected:
        raise PromotionError("Recipe source is not in the committed decision state")


def _fsync_path(path: Path) -> None:
    try:
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    except OSError as error:
        raise PromotionError(
            f"Cannot durably sync promotion artifact: {path}"
        ) from error


def _clear_pending_intent(path: Path) -> None:
    try:
        path.unlink()
    except OSError as error:
        raise PromotionError(
            f"Cannot clear completed promotion intent: {path}"
        ) from error


def _canary_from_mapping(raw: Mapping[str, object], *, field: str) -> CanaryEvidence:
    _strict_keys(raw, set(CanaryEvidence.__slots__), field=field)
    _schema(raw.get("schema_version"), field=f"{field}.schema_version")
    raw_outcomes = raw.get("downstream_outcomes")
    if not isinstance(raw_outcomes, list):
        raise PromotionError(f"{field}.downstream_outcomes must be a list")
    outcomes: list[CanaryOutcome] = []
    for index, raw_outcome in enumerate(raw_outcomes):
        outcome_field = f"{field}.downstream_outcomes[{index}]"
        if not isinstance(raw_outcome, Mapping):
            raise PromotionError(f"{outcome_field} must be an object")
        _strict_keys(
            raw_outcome,
            {"name", "status", "required"},
            field=outcome_field,
        )
        required = raw_outcome.get("required")
        if type(required) is not bool:
            raise PromotionError(f"{outcome_field}.required must be a boolean")
        outcomes.append(
            CanaryOutcome(
                name=_nonempty(raw_outcome.get("name"), field=f"{outcome_field}.name"),
                status=raw_outcome.get("status"),  # type: ignore[arg-type]
                required=required,
            )
        )
    for name in ("unknown_cost", "human_review_required"):
        if type(raw.get(name)) is not bool:
            raise PromotionError(f"{field}.{name} must be a boolean")
    for name in (
        "book_valid",
        "epub_valid",
        "site_valid",
        "human_review_passed",
    ):
        if raw.get(name) is not None and type(raw.get(name)) is not bool:
            raise PromotionError(f"{field}.{name} must be a boolean or null")
    canary = CanaryEvidence(
        schema_version=raw["schema_version"],  # type: ignore[arg-type]
        work_order_id=raw["work_order_id"],  # type: ignore[arg-type]
        doc_id=raw["doc_id"],  # type: ignore[arg-type]
        run_id=raw["run_id"],  # type: ignore[arg-type]
        recipe_hash=raw["recipe_hash"],  # type: ignore[arg-type]
        refreshed_station=raw["refreshed_station"],  # type: ignore[arg-type]
        status=raw["status"],  # type: ignore[arg-type]
        downstream_outcomes=tuple(outcomes),
        known_cost_usd=raw["known_cost_usd"],  # type: ignore[arg-type]
        unknown_cost=raw["unknown_cost"],  # type: ignore[arg-type]
        book_valid=raw["book_valid"],  # type: ignore[arg-type]
        epub_valid=raw["epub_valid"],  # type: ignore[arg-type]
        site_valid=raw["site_valid"],  # type: ignore[arg-type]
        human_review_required=raw["human_review_required"],  # type: ignore[arg-type]
        human_review_passed=raw["human_review_passed"],  # type: ignore[arg-type]
        canary_fingerprint=raw["canary_fingerprint"],  # type: ignore[arg-type]
    )
    _verify_canary_record(canary)
    return canary


def _decision_from_mapping(raw: Mapping[str, object], *, field: str) -> PromotionRecord:
    _strict_keys(raw, set(PromotionRecord.__slots__), field=field)
    _schema(raw.get("schema_version"), field=f"{field}.schema_version")
    raw_canary = raw.get("canary")
    if raw_canary is None:
        canary = None
    elif isinstance(raw_canary, Mapping):
        canary = _canary_from_mapping(raw_canary, field=f"{field}.canary")
    else:
        raise PromotionError(f"{field}.canary must be an object or null")
    raw_cost_waiver = raw.get("canary_cost_waiver")
    if raw_cost_waiver is None:
        cost_waiver = None
    elif isinstance(raw_cost_waiver, Mapping):
        _strict_keys(
            raw_cost_waiver,
            set(CanaryCostWaiver.__slots__),
            field=f"{field}.canary_cost_waiver",
        )
        cost_waiver = CanaryCostWaiver(**dict(raw_cost_waiver))  # type: ignore[arg-type]
    else:
        raise PromotionError(
            f"{field}.canary_cost_waiver must be an object or null"
        )
    values = dict(raw)
    values["canary"] = canary
    values["canary_cost_waiver"] = cost_waiver
    record = PromotionRecord(**values)  # type: ignore[arg-type]
    _verify_decision_record(record)
    return record


def _load_canonical_record(
    path: str | Path, *, field: str, keys: set[str]
) -> dict[str, object]:
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PromotionError(f"Cannot read {field} {source}: {error}") from error

    def reject_constant(value: str) -> object:
        raise PromotionError(f"{field} contains non-finite JSON number: {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_UNIQUE_JSON_OBJECT,
            parse_constant=reject_constant,
        )
    except PromotionError:
        raise
    except json.JSONDecodeError as error:
        raise PromotionError(f"{field} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise PromotionError(f"{field} must be a JSON object")
    _strict_keys(value, keys, field=field)
    if raw != canonical_json(value):
        raise PromotionError(f"{field} is not canonical JSON")
    return value


def _atomic_create_json(path: str | Path, payload: Mapping[str, object]) -> Path:
    requested = Path(path)
    try:
        parent = requested.parent.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PromotionError(
            f"Artifact parent directory does not exist: {requested.parent}"
        ) from error
    if not parent.is_dir() or not requested.name:
        raise PromotionError(f"Artifact parent is not a directory: {parent}")
    destination = parent / requested.name
    content = canonical_json(payload).encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    except FileExistsError as error:
        raise PromotionError(
            f"Immutable artifact already exists: {destination}"
        ) from error
    except OSError as error:
        raise PromotionError(
            f"Cannot publish immutable artifact: {destination}"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def _strict_keys(value: Mapping[str, object], keys: set[str], *, field: str) -> None:
    _record.strict_keys(value, keys, field=field, error_cls=PromotionError)


def _schema(value: object, *, field: str) -> int:
    return _record.schema_version(value, field=field, error_cls=PromotionError)


def _verify_proposal(proposal: RecipeProposal) -> None:
    if not isinstance(proposal, RecipeProposal):
        raise PromotionError("Recipe proposal has the wrong record type")
    _schema(proposal.schema_version, field="proposal.schema_version")
    expected = content_fingerprint(_proposal_payload(proposal))
    if proposal.proposal_id != expected:
        raise PromotionError("Recipe proposal fingerprint does not match its content")
    _nonempty(proposal.proposed_source, field="proposed_source")
    if (
        _source_hash(proposal.proposed_source.encode("utf-8"))
        != proposal.proposed_recipe_hash
    ):
        raise PromotionError("Proposal source does not match its proposed recipe hash")
    _nonempty(proposal.previous_candidate, field="previous_candidate")
    _nonempty(proposal.next_candidate, field="next_candidate")
    _nonempty(proposal.evaluation_run, field="evaluation_run")
    if proposal.source_promotion_id is not None:
        _digest(proposal.source_promotion_id, field="source_promotion_id")
    if proposal.waiver_fingerprint is not None:
        _digest(proposal.waiver_fingerprint, field="waiver_fingerprint")
    if proposal.action == "promote" and proposal.source_promotion_id is not None:
        raise PromotionError("A promote proposal cannot name a source promotion")
    if proposal.action == "rollback" and proposal.source_promotion_id is None:
        raise PromotionError("A rollback proposal must name its source promotion")


def _proposal_payload(proposal: RecipeProposal) -> dict[str, object]:
    return {
        "schema_version": proposal.schema_version,
        "action": proposal.action,
        "recipe": proposal.recipe,
        "station": proposal.station,
        "current_recipe_hash": proposal.current_recipe_hash,
        "proposed_recipe_hash": proposal.proposed_recipe_hash,
        "previous_candidate": proposal.previous_candidate,
        "previous_candidate_fingerprint": proposal.previous_candidate_fingerprint,
        "next_candidate": proposal.next_candidate,
        "next_candidate_fingerprint": proposal.next_candidate_fingerprint,
        "evaluation_run": proposal.evaluation_run,
        "report_fingerprint": proposal.report_fingerprint,
        "source_promotion_id": proposal.source_promotion_id,
        "waiver_fingerprint": proposal.waiver_fingerprint,
        "proposed_source": proposal.proposed_source,
    }


def _verify_decision_record(record: PromotionRecord) -> None:
    if not isinstance(record, PromotionRecord):
        raise PromotionError("Promotion record has the wrong record type")
    _schema(record.schema_version, field="promotion.schema_version")
    _digest(record.promotion_id, field="promotion promotion_id")
    _validate_recipe_name(record.recipe)
    _nonempty(record.station, field="promotion station")
    _nonempty(record.previous_candidate, field="promotion previous_candidate")
    _nonempty(record.next_candidate, field="promotion next_candidate")
    _digest(
        record.previous_candidate_fingerprint,
        field="promotion previous_candidate_fingerprint",
    )
    _digest(
        record.next_candidate_fingerprint,
        field="promotion next_candidate_fingerprint",
    )
    _nonempty(record.evaluation_run, field="promotion evaluation_run")
    _digest(record.report_fingerprint, field="promotion report_fingerprint")
    _nonempty(record.approved_by, field="promotion approved_by")
    _timestamp(record.created_at, field="promotion created_at")
    if record.canary is not None:
        _verify_canary_record(record.canary)
    if record.source_promotion_id is not None:
        _digest(record.source_promotion_id, field="promotion source_promotion_id")
    if record.waiver_fingerprint is not None:
        _digest(record.waiver_fingerprint, field="promotion waiver_fingerprint")
    if record.canary_cost_waiver is not None:
        if record.canary is None:
            raise PromotionError("Canary cost waiver requires canary evidence")
        _verify_cost_waiver(
            record.canary_cost_waiver,
            canary_fingerprint=record.canary.canary_fingerprint,
            approved_by=record.approved_by,
        )
    if record.action == "promote":
        if record.canary is None or record.source_promotion_id is not None:
            raise PromotionError(
                "Promote records require canary evidence and no source promotion"
            )
    elif record.action == "rollback":
        if record.source_promotion_id is None:
            raise PromotionError("Rollback records must name their source promotion")
        if record.canary_cost_waiver is not None:
            raise PromotionError("Rollback records cannot carry canary cost waivers")
    else:
        raise PromotionError(f"Invalid promotion action: {record.action!r}")
    payload = {
        name: getattr(record, name)
        for name in PromotionRecord.__slots__
        if name != "promotion_id"
    }
    if record.promotion_id != content_fingerprint(_decision_payload(payload)):
        raise PromotionError("Promotion record fingerprint does not match its content")


def _verify_canary_record(canary: CanaryEvidence) -> None:
    if not isinstance(canary, CanaryEvidence):
        raise PromotionError("Canary evidence has the wrong record type")
    _schema(canary.schema_version, field="canary.schema_version")
    _nonempty(canary.work_order_id, field="canary work_order_id")
    _nonempty(canary.doc_id, field="canary doc_id")
    _nonempty(canary.run_id, field="canary run_id")
    _digest(canary.recipe_hash, field="canary recipe_hash")
    _nonempty(canary.refreshed_station, field="canary refreshed_station")
    if type(canary.unknown_cost) is not bool:
        raise PromotionError("Canary unknown_cost must be a boolean")
    if type(canary.human_review_required) is not bool:
        raise PromotionError("Canary human_review_required must be a boolean")
    for name, value in (
        ("book_valid", canary.book_valid),
        ("epub_valid", canary.epub_valid),
        ("site_valid", canary.site_valid),
        ("human_review_passed", canary.human_review_passed),
    ):
        if value is not None and type(value) is not bool:
            raise PromotionError(f"Canary {name} must be a boolean or null")
    if canary.status not in {"passed", "failed", "unknown"}:
        raise PromotionError(f"Invalid canary status: {canary.status!r}")
    if (
        isinstance(canary.known_cost_usd, bool)
        or not isinstance(canary.known_cost_usd, (int, float))
        or not math.isfinite(canary.known_cost_usd)
        or canary.known_cost_usd < 0
    ):
        raise PromotionError(
            "Canary known_cost_usd must be a finite non-negative number"
        )
    if not all(
        isinstance(outcome, CanaryOutcome) for outcome in canary.downstream_outcomes
    ):
        raise PromotionError("Canary downstream outcomes must be CanaryOutcome records")
    expected = content_fingerprint(_canary_payload(canary))
    if canary.canary_fingerprint != expected:
        raise PromotionError("Canary fingerprint does not match its content")


def _recipe_source(recipe_root: str | Path, recipe: str) -> Path:
    _validate_recipe_name(recipe)
    root = Path(recipe_root).resolve(strict=True)
    if not root.is_dir():
        raise PromotionError(f"Recipe root is not a directory: {root}")
    unresolved = root / f"{recipe}.yaml"
    try:
        source = unresolved.resolve(strict=True)
    except FileNotFoundError as error:
        raise PromotionError(f"Recipe source does not exist: {recipe}.yaml") from error
    if source.parent != root or not source.is_file():
        raise PromotionError("Recipe source must be a direct file under recipe_root")
    return source


def _validate_recipe_name(recipe: str) -> None:
    if not isinstance(recipe, str) or not _RECIPE_NAME.fullmatch(recipe):
        raise PromotionError(f"Invalid recipe name: {recipe!r}")


def _validate_candidate_pair(
    current: ResolvedCandidate, next_candidate: ResolvedCandidate, *, station: str
) -> None:
    _nonempty(station, field="station")
    for label, candidate in (("current", current), ("next", next_candidate)):
        if candidate.station != station:
            raise PromotionError(
                f"{label.capitalize()} candidate belongs to another station"
            )
        _nonempty(candidate.id, field=f"{label} candidate id")
        _digest(candidate.fingerprint, field=f"{label} candidate fingerprint")
    if current.fingerprint == next_candidate.fingerprint:
        raise PromotionError(
            "Current and proposed candidate fingerprints are identical"
        )


def _report_identity(value: object, *, field: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise PromotionError(f"{field} must be an identity mapping")
    return (
        _nonempty(value.get("id"), field=f"{field}.id"),
        _digest(value.get("fingerprint"), field=f"{field}.fingerprint"),
    )


def _reject_marked_required_unknown(value: object, *, field: str) -> None:
    if isinstance(value, Mapping):
        if value.get("required") is True:
            evidence = value.get("value", value.get("status"))
            if _contains_unknown(evidence):
                raise PromotionError(f"Required promotion evidence is unknown: {field}")
        for key, nested in value.items():
            _reject_marked_required_unknown(nested, field=f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_marked_required_unknown(nested, field=f"{field}[{index}]")


def _contains_unknown(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _UNKNOWN
    if isinstance(value, Mapping):
        return any(_contains_unknown(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unknown(item) for item in value)
    return False


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def _source_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PromotionError(f"{field} must be a lowercase SHA-256 fingerprint")
    return value


def _nonempty(value: object, *, field: str) -> str:
    return _record.string(value, field=field, error_cls=PromotionError)


def _timestamp(value: object, *, field: str) -> str:
    text = _nonempty(value, field=field)
    if not text.endswith("Z"):
        raise PromotionError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise PromotionError(f"{field} must be an ISO-8601 timestamp") from error
    return text
