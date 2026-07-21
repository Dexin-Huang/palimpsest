"""Isolated paired execution for station evaluation cases."""

from __future__ import annotations

import hashlib
import json
import math
import re
import platform
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeAlias

from palimpsest.factory.core import registry
from palimpsest.factory.core.artifact import content_fingerprint
from palimpsest.factory.core.cell import CellSpec
from palimpsest.factory.core.contracts import contract, validate_payload
from palimpsest.factory.core.executors import make as make_executor
from palimpsest.factory.evaluation.candidate import ResolvedCandidate
from palimpsest.factory.evaluation.judging import JudgeExecutionResult, JudgeExecutor
from palimpsest.factory.evaluation.metrics import MetricDirection, MetricObservation
from palimpsest.factory.evaluation.response_schemas import ResponseSchema
from palimpsest.factory.evaluation.report import (
    CaseSideOutcome,
    PairedCaseOutcome,
    ReportIdentity,
    build_report,
    report_fingerprint,
    write_report,
)
from palimpsest.factory.evaluation.statistics import (
    ComparisonPolicy,
    HardLimit,
    compare_protected_slice,
    evaluate_hard_limit,
    paired_comparison,
    qualification_decision,
    summarize_reliability,
)
from palimpsest.factory.evaluation.store import EvaluationStore
from palimpsest.factory.evaluation.suite import (
    CaseAsset,
    EvaluationCase,
    EvaluationSuite,
    validate_candidate_suite,
)
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path, page_list_path

AssetResolver = Callable[[CaseAsset], Path]
ProbeCallable: TypeAlias = Callable[
    [Sequence[PairedCaseOutcome], Sequence[EvaluationCase]],
    Mapping[str, object],
]
JudgeEvidence: TypeAlias = Mapping[str, object]

_RUN_MANIFEST = "run.json"
_PAIR_RECORD = "pair.json"
_JUDGE_RECORDS = "judges"
_CHECKPOINT_SCHEMA_VERSION = 1
_RUN_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?\Z")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def _validated_run_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 255
        or _RUN_ID.fullmatch(value) is None
        or value.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES
    ):
        raise ValueError(
            "run_id must be one filesystem-safe identifier containing only "
            "letters, numbers, periods, underscores, and hyphens"
        )
    return value


@dataclass(frozen=True, slots=True)
class EvaluationWorkflowResult:
    """The published terminal artifact returned by :func:`run_evaluation`."""

    report_path: Path
    report: Mapping[str, object]


def filesystem_asset_resolver(tracked_root: Path, object_root: Path) -> AssetResolver:
    """Resolve tracked case files or previously fetched content-addressed objects."""

    tracked_root = tracked_root.resolve()
    object_root = object_root.resolve()

    def resolve(asset: CaseAsset) -> Path:
        if asset.path is not None:
            path = (tracked_root / asset.path).resolve()
            if not path.is_relative_to(tracked_root):
                raise ValueError(f"Case asset escapes tracked root: {asset.path}")
            return path
        return object_root / asset.sha256

    return resolve


class EvaluationRunner:
    """Run baseline and challenger cells without production ledger or workspace."""

    def __init__(
        self,
        *,
        run_root: Path,
        asset_resolver: AssetResolver,
        executor: str = "subprocess",
        workers: int = 1,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self._run_root = run_root
        self._resolve_asset = asset_resolver
        self._executor_name = executor
        self._workers = workers

    def verify(
        self,
        *,
        suite: EvaluationSuite,
        baseline: ResolvedCandidate,
        challenger: ResolvedCandidate,
        cases: Sequence[EvaluationCase] | None = None,
    ) -> tuple[EvaluationCase, ...]:
        """Verify identities, frozen membership, implementations, and assets without work."""

        validate_candidate_suite(baseline, suite)
        validate_candidate_suite(challenger, suite)
        selected = tuple(suite.cases if cases is None else cases)
        _validate_pair(baseline, challenger, selected)
        frozen = {case.case_id: case.fingerprint for case in suite.cases}
        for case in selected:
            if frozen.get(case.case_id) != case.fingerprint:
                raise ValueError(
                    f"Case {case.case_id!r} is not the frozen case from suite {suite.id!r}"
                )
            for value in case.inputs.values():
                assets = value.values() if isinstance(value, Mapping) else (value,)
                for asset in assets:
                    self._verify_asset(asset)
            for asset in case.references.values():
                self._verify_asset(asset)
        for binding in (*suite.primary_metrics, *suite.hard_limits):
            if not callable(getattr(binding.definition, "observe", None)):
                raise TypeError(
                    f"Metric definition {binding.name!r} must provide observe(output, gold)"
                )
        for probe in suite.downstream_probes:
            if not callable(probe.definition):
                raise TypeError(
                    f"Downstream probe {probe.id!r} must be callable as "
                    "probe(paired_cases, evaluation_cases)"
                )
        return selected

    def _verify_asset(self, asset: CaseAsset) -> Path:
        source = self._resolve_asset(asset)
        if not source.is_file():
            raise FileNotFoundError(f"Evaluation asset is unavailable: {source}")
        with source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != asset.sha256:
            raise ValueError(
                f"Evaluation asset hash mismatch for {source}: "
                f"expected {asset.sha256}, got {actual}"
            )
        return source

    def run(
        self,
        *,
        run_id: str,
        cases: Sequence[EvaluationCase],
        baseline: ResolvedCandidate,
        challenger: ResolvedCandidate,
        allow_existing: bool = False,
        on_case: Callable[[PairedCaseOutcome], None] | None = None,
    ) -> tuple[PairedCaseOutcome, ...]:
        run_id = _validated_run_id(run_id)
        _validate_pair(baseline, challenger, cases)

        run_dir = self._run_root / run_id
        if run_dir.exists() and not allow_existing:
            raise FileExistsError(f"Evaluation run already exists: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=allow_existing)

        indexed = tuple(enumerate(cases))
        attempts = {
            case.case_id: _next_attempt_number(
                run_dir / "cases" / _safe_case_directory(case.case_id)
            )
            for case in cases
        }

        def finish(index: int, outcome: PairedCaseOutcome):
            if on_case is not None:
                on_case(outcome)
            return index, outcome

        if self._workers == 1:
            completed = [
                finish(
                    index,
                    self._run_case(
                        run_id,
                        run_dir,
                        case,
                        baseline,
                        challenger,
                        attempts[case.case_id],
                    ),
                )
                for index, case in indexed
            ]
        else:
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                futures = {
                    pool.submit(
                        self._run_case,
                        run_id,
                        run_dir,
                        case,
                        baseline,
                        challenger,
                        attempts[case.case_id],
                    ): index
                    for index, case in indexed
                }
                completed = [
                    finish(futures[future], future.result())
                    for future in as_completed(futures)
                ]
        return tuple(result for _, result in sorted(completed))

    def _run_case(
        self,
        run_id: str,
        run_dir: Path,
        case: EvaluationCase,
        baseline: ResolvedCandidate,
        challenger: ResolvedCandidate,
        attempt: int,
    ) -> PairedCaseOutcome:
        case_dir = (
            run_dir
            / "cases"
            / _safe_case_directory(case.case_id)
            / "attempts"
            / f"attempt-{attempt:04d}"
        )
        order = _side_order(run_id, case.case_id)
        candidates = {
            "baseline": baseline,
            "challenger": challenger,
        }
        outcomes: dict[str, CaseSideOutcome] = {}
        for side in order:
            outcomes[side] = self._run_side(
                case,
                side,
                candidates[side],
                case_dir / side,
            )
        return PairedCaseOutcome(
            case_id=case.case_id,
            baseline=outcomes["baseline"],
            challenger=outcomes["challenger"],
        )

    def _run_side(
        self,
        case: EvaluationCase,
        side: str,
        candidate: ResolvedCandidate,
        library_root: Path,
    ) -> CaseSideOutcome:
        station = registry.get(candidate.station, candidate.variant)
        _validate_case_inputs(case, station)
        self._materialize_case(case, station, library_root)
        spec = CellSpec(
            doc_id=case.doc_id,
            station=candidate.station,
            variant=candidate.variant,
            page_id=case.page_id,
            library_root=str(library_root),
            config_fingerprint=candidate.fingerprint,
            input_fingerprint=_case_input_fingerprint(case),
            model=candidate.model,
            prompt_name=candidate.prompt_name,
            prompt_sha256=candidate.prompt_hash,
            params=dict(candidate.params),
            options=dict(candidate.options),
        )

        started = time.perf_counter()
        execution_outcome = None
        try:
            execution_outcome = make_executor(self._executor_name).execute(spec)
            output_path = Path(execution_outcome.output_path).resolve()
            if not output_path.is_relative_to(library_root.resolve()):
                raise ValueError(
                    "Executor output escaped its isolated evaluation workspace"
                )
            output_contract = contract(candidate.produces)
            if output_contract.format == "json":
                output = read_json(output_path)
                if not isinstance(output, Mapping):
                    raise ValueError("Candidate JSON output must be an object")
                validate_payload(
                    candidate.produces,
                    output,
                    expected_doc_id=case.doc_id,
                )
            output_fingerprint = content_fingerprint(output_path)
            return CaseSideOutcome(
                candidate_id=candidate.id,
                candidate_fingerprint=candidate.fingerprint,
                succeeded=True,
                output_path=str(output_path),
                output_fingerprint=output_fingerprint,
                latency_seconds=time.perf_counter() - started,
                tokens_in=execution_outcome.tokens_in,
                tokens_out=execution_outcome.tokens_out,
                cost_usd=execution_outcome.cost_usd,
            )
        except Exception as error:
            return CaseSideOutcome(
                candidate_id=candidate.id,
                candidate_fingerprint=candidate.fingerprint,
                succeeded=False,
                output_path=None,
                output_fingerprint=None,
                latency_seconds=time.perf_counter() - started,
                tokens_in=getattr(
                    execution_outcome, "tokens_in", getattr(error, "tokens_in", None)
                ),
                tokens_out=getattr(
                    execution_outcome, "tokens_out", getattr(error, "tokens_out", None)
                ),
                cost_usd=getattr(
                    execution_outcome, "cost_usd", getattr(error, "cost_usd", None)
                ),
                error_kind=getattr(error, "kind", type(error).__name__.lower()),
                error_message=str(error),
            )

    def _materialize_case(self, case, station, library_root: Path) -> None:
        library_root.mkdir(parents=True)
        atomic_write_json(
            page_list_path(case.doc_id, library_root),
            {
                "doc_id": case.doc_id,
                "pages": [dict(page) for page in case.pages],
            },
        )

        for kind, source in case.inputs.items():
            kind_contract = contract(kind)
            if isinstance(source, Mapping):
                for page_id, asset in source.items():
                    if kind_contract.grain != "page":
                        raise ValueError(
                            f"Manuscript input {kind!r} cannot have page assets"
                        )
                    self._copy_asset(
                        asset,
                        artifact_path(case.doc_id, kind, page_id, library_root),
                    )
                continue

            page_id = case.page_id if kind_contract.grain == "page" else None
            self._copy_asset(
                source,
                artifact_path(case.doc_id, kind, page_id, library_root),
            )

    def _copy_asset(self, asset: CaseAsset, destination: Path) -> None:
        source = self._verify_asset(asset)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _validate_case_inputs(case: EvaluationCase, station) -> None:
    supplied = set(case.inputs)
    required = set(station.consumes) - {"page_list"}
    allowed = required | (set(station.optional_consumes) - {"page_list"})
    missing = sorted(required - supplied)
    extra = sorted(supplied - allowed)
    if missing:
        raise ValueError(f"Case {case.case_id!r} is missing inputs: {missing}")
    if extra:
        raise ValueError(f"Case {case.case_id!r} has undeclared inputs: {extra}")
    if station.grain == "page":
        if case.page_id is None:
            raise ValueError(f"Page station {station.name!r} requires case.page_id")
        if case.page_id not in {page["page_id"] for page in case.pages}:
            raise ValueError(f"Case page {case.page_id!r} is absent from pages")
    elif case.page_id is not None:
        raise ValueError(
            f"Manuscript station {station.name!r} requires case.page_id=null"
        )


def _case_input_fingerprint(case: EvaluationCase) -> str:
    def asset_identity(value):
        if isinstance(value, Mapping):
            return {page_id: asset.sha256 for page_id, asset in sorted(value.items())}
        return value.sha256

    payload = {
        "doc_id": case.doc_id,
        "page_id": case.page_id,
        "pages": [dict(page) for page in case.pages],
        "inputs": {
            kind: asset_identity(value) for kind, value in sorted(case.inputs.items())
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _side_order(run_id: str, case_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{run_id}\0{case_id}".encode("utf-8")).digest()
    return (
        ("baseline", "challenger") if digest[0] % 2 == 0 else ("challenger", "baseline")
    )


def _safe_case_directory(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
    return f"case-{digest}"


def _validate_pair(
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    cases: Sequence[EvaluationCase],
) -> None:
    if baseline.station != challenger.station:
        raise ValueError("Baseline and challenger must target the same station")
    if baseline.fingerprint == challenger.fingerprint:
        raise ValueError("Baseline and challenger fingerprints must differ")
    socket_fields = ("grain", "consumes", "optional_consumes", "produces")
    incompatible = [
        field
        for field in socket_fields
        if getattr(baseline, field) != getattr(challenger, field)
    ]
    if incompatible:
        raise ValueError(
            "Baseline and challenger station sockets differ: " + ", ".join(incompatible)
        )
    if not cases:
        raise ValueError("Evaluation requires at least one case")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Evaluation case IDs must be unique")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_json_value(item) for item in sorted(value)]
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _validated_cost_ceiling(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("max_cost_usd must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("max_cost_usd must be a finite non-negative number")
    return result


def _checkpoint_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("fingerprint", None)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _immutable_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"Evaluation evidence already exists: {path}")
    serialized = _json_value(payload)
    if not isinstance(serialized, Mapping):
        raise TypeError("Evaluation evidence must be a mapping")
    document = dict(serialized)
    document["fingerprint"] = _checkpoint_fingerprint(document)
    atomic_write_json(path, document)


def _read_checkpoint(path: Path, *, label: str) -> Mapping[str, object]:
    value = _load_json_object(path, label=label)
    actual = value.get("fingerprint")
    if not isinstance(actual, str) or actual != _checkpoint_fingerprint(value):
        raise ValueError(f"{label} fingerprint does not match its content")
    return value


def _run_manifest(
    *,
    run_id: str,
    suite: EvaluationSuite,
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    cases: Sequence[EvaluationCase],
    executor: str,
    max_cost_usd: float | None,
) -> Mapping[str, object]:
    return {
        "schema_version": _CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "suite": {"id": suite.id, "fingerprint": suite.fingerprint},
        "baseline": {"id": baseline.id, "fingerprint": baseline.fingerprint},
        "challenger": {"id": challenger.id, "fingerprint": challenger.fingerprint},
        "judges": [
            {
                "metric": binding.metric,
                "id": binding.judge.id,
                "fingerprint": binding.judge.fingerprint,
            }
            for binding in suite.judges
        ],
        "cases": [
            {
                "case_id": case.case_id,
                "case_fingerprint": case.fingerprint,
                "input_fingerprint": _case_input_fingerprint(case),
            }
            for case in cases
        ],
        "executor": executor,
        "max_cost_usd": max_cost_usd,
    }


def _next_attempt_number(case_dir: Path) -> int:
    attempts_dir = case_dir / "attempts"
    if not attempts_dir.exists():
        return 1
    if not attempts_dir.is_dir():
        raise ValueError(f"Ambiguous case attempt state: {attempts_dir}")
    numbers = []
    for path in attempts_dir.iterdir():
        if not path.is_dir() or not path.name.startswith("attempt-"):
            raise ValueError(f"Ambiguous case attempt state: {path}")
        suffix = path.name.removeprefix("attempt-")
        if len(suffix) != 4 or not suffix.isdigit() or int(suffix) < 1:
            raise ValueError(f"Ambiguous case attempt state: {path}")
        numbers.append(int(suffix))
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"Ambiguous duplicate case attempts: {attempts_dir}")
    return max(numbers, default=0) + 1


def _case_record_identity(
    manifest: Mapping[str, object], case: EvaluationCase
) -> Mapping[str, object]:
    return {
        "run_fingerprint": manifest["fingerprint"],
        "case_id": case.case_id,
        "case_fingerprint": case.fingerprint,
        "input_fingerprint": _case_input_fingerprint(case),
    }


def _pair_record_path(run_dir: Path, case: EvaluationCase) -> Path:
    return run_dir / "cases" / _safe_case_directory(case.case_id) / _PAIR_RECORD


def _judge_record_name(binding) -> str:
    identity = f"{binding.metric}\0{binding.judge.fingerprint}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest() + ".json"


def _judge_record_path(run_dir: Path, case: EvaluationCase, binding) -> Path:
    return (
        run_dir
        / "cases"
        / _safe_case_directory(case.case_id)
        / _JUDGE_RECORDS
        / _judge_record_name(binding)
    )


def _write_pair_record(
    run_dir: Path,
    manifest: Mapping[str, object],
    case: EvaluationCase,
    outcome: PairedCaseOutcome,
) -> None:
    _immutable_json(
        _pair_record_path(run_dir, case),
        {
            "schema_version": _CHECKPOINT_SCHEMA_VERSION,
            **_case_record_identity(manifest, case),
            "outcome": asdict(outcome),
        },
    )


def _write_judge_record(
    run_dir: Path,
    manifest: Mapping[str, object],
    case: EvaluationCase,
    binding,
    evidence: JudgeEvidence,
) -> None:
    _immutable_json(
        _judge_record_path(run_dir, case, binding),
        {
            "schema_version": _CHECKPOINT_SCHEMA_VERSION,
            **_case_record_identity(manifest, case),
            "metric": binding.metric,
            "judge_fingerprint": binding.judge.fingerprint,
            "evidence": evidence,
        },
    )


def _side_from_record(
    value: object,
    *,
    label: str,
    candidate: ResolvedCandidate,
    case: EvaluationCase,
    run_dir: Path,
) -> CaseSideOutcome:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    expected = set(CaseSideOutcome.__dataclass_fields__)
    if set(value) != expected:
        raise ValueError(f"{label} has invalid fields")
    try:
        outcome = CaseSideOutcome(**dict(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is invalid: {error}") from error
    if (
        outcome.candidate_id != candidate.id
        or outcome.candidate_fingerprint != candidate.fingerprint
    ):
        raise ValueError(f"{label} candidate identity drift")
    if outcome.succeeded:
        assert outcome.output_path is not None
        assert outcome.output_fingerprint is not None
        output_path = Path(outcome.output_path).resolve()
        if not output_path.is_relative_to(run_dir.resolve()):
            raise ValueError(f"{label} output escaped the evaluation run")
        if not output_path.is_file():
            raise ValueError(f"{label} output is missing")
        if content_fingerprint(output_path) != outcome.output_fingerprint:
            raise ValueError(f"{label} output fingerprint drift")
        if contract(candidate.produces).format == "json":
            output = read_json(output_path)
            if not isinstance(output, Mapping):
                raise ValueError(f"{label} JSON output must be an object")
            validate_payload(candidate.produces, output, expected_doc_id=case.doc_id)
    return outcome


def _read_pair_record(
    run_dir: Path,
    manifest: Mapping[str, object],
    case: EvaluationCase,
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
) -> PairedCaseOutcome | None:
    path = _pair_record_path(run_dir, case)
    if not path.exists():
        return None
    record = _read_checkpoint(path, label=f"pair record for {case.case_id}")
    expected_keys = {
        "schema_version",
        "run_fingerprint",
        "case_id",
        "case_fingerprint",
        "input_fingerprint",
        "outcome",
        "fingerprint",
    }
    if set(record) != expected_keys or record.get("schema_version") != 1:
        raise ValueError(f"Pair record for {case.case_id} has invalid fields")
    for name, expected in _case_record_identity(manifest, case).items():
        if record.get(name) != expected:
            raise ValueError(f"Pair record identity drift for {case.case_id}: {name}")
    value = record["outcome"]
    if not isinstance(value, Mapping) or set(value) != {
        "case_id",
        "baseline",
        "challenger",
    }:
        raise ValueError(f"Pair record outcome for {case.case_id} is invalid")
    if value["case_id"] != case.case_id:
        raise ValueError(f"Pair record case identity drift for {case.case_id}")
    return PairedCaseOutcome(
        case_id=case.case_id,
        baseline=_side_from_record(
            value["baseline"],
            label=f"{case.case_id} baseline",
            candidate=baseline,
            case=case,
            run_dir=run_dir,
        ),
        challenger=_side_from_record(
            value["challenger"],
            label=f"{case.case_id} challenger",
            candidate=challenger,
            case=case,
            run_dir=run_dir,
        ),
    )


def _read_judge_record(
    run_dir: Path,
    manifest: Mapping[str, object],
    case: EvaluationCase,
    binding,
) -> JudgeEvidence | None:
    path = _judge_record_path(run_dir, case, binding)
    if not path.exists():
        return None
    record = _read_checkpoint(path, label=f"judge record for {case.case_id}")
    expected_keys = {
        "schema_version",
        "run_fingerprint",
        "case_id",
        "case_fingerprint",
        "input_fingerprint",
        "metric",
        "judge_fingerprint",
        "evidence",
        "fingerprint",
    }
    if set(record) != expected_keys or record.get("schema_version") != 1:
        raise ValueError(f"Judge record for {case.case_id} has invalid fields")
    for name, expected in _case_record_identity(manifest, case).items():
        if record.get(name) != expected:
            raise ValueError(f"Judge record identity drift for {case.case_id}: {name}")
    if (
        record.get("metric") != binding.metric
        or record.get("judge_fingerprint") != binding.judge.fingerprint
        or not isinstance(record.get("evidence"), Mapping)
    ):
        raise ValueError(f"Judge record identity drift for {case.case_id}")
    evidence = record["evidence"]
    if (
        evidence.get("case_id") != case.case_id
        or evidence.get("metric") != binding.metric
        or evidence.get("judge_fingerprint") != binding.judge.fingerprint
    ):
        raise ValueError(f"Judge evidence identity drift for {case.case_id}")
    return MappingProxyType(dict(evidence))


def _load_run_manifest(
    run_dir: Path,
    *,
    expected: Mapping[str, object],
    requested_cost_ceiling: float | None,
) -> tuple[Mapping[str, object], float | None]:
    manifest = _read_checkpoint(
        run_dir / _RUN_MANIFEST, label="evaluation run manifest"
    )
    prior_ceiling = _validated_cost_ceiling(manifest.get("max_cost_usd"))
    if requested_cost_ceiling is not None and requested_cost_ceiling != prior_ceiling:
        raise ValueError("Resumed evaluation max-cost identity drift")
    effective_ceiling = (
        prior_ceiling if requested_cost_ceiling is None else requested_cost_ceiling
    )
    expected_with_ceiling = dict(expected)
    expected_with_ceiling["max_cost_usd"] = effective_ceiling
    expected_with_ceiling["fingerprint"] = _checkpoint_fingerprint(
        expected_with_ceiling
    )
    if manifest != expected_with_ceiling:
        raise ValueError("Resumed evaluation run identity drift")
    return manifest, effective_ceiling


def _validate_resume_layout(
    run_dir: Path,
    cases: Sequence[EvaluationCase],
    suite: EvaluationSuite,
) -> None:
    allowed_root = {_RUN_MANIFEST, "cases"}
    for path in run_dir.iterdir():
        if path.name not in allowed_root:
            raise ValueError(f"Ambiguous partial evaluation state: {path}")
    cases_dir = run_dir / "cases"
    if not cases_dir.exists():
        return
    if not cases_dir.is_dir():
        raise ValueError(f"Ambiguous partial evaluation state: {cases_dir}")
    expected_dirs = {_safe_case_directory(case.case_id): case for case in cases}
    for path in cases_dir.iterdir():
        case = expected_dirs.get(path.name)
        if case is None or not path.is_dir():
            raise ValueError(f"Ambiguous partial evaluation state: {path}")
        allowed_case = {"attempts", _PAIR_RECORD, _JUDGE_RECORDS}
        for item in path.iterdir():
            if item.name not in allowed_case:
                raise ValueError(f"Ambiguous partial evaluation state: {item}")
        _next_attempt_number(path)
        pair_path = path / _PAIR_RECORD
        judges_dir = path / _JUDGE_RECORDS
        if judges_dir.exists():
            if not judges_dir.is_dir() or not pair_path.is_file():
                raise ValueError(f"Ambiguous partial evaluation state: {judges_dir}")
            expected_judges = {_judge_record_name(binding) for binding in suite.judges}
            for item in judges_dir.iterdir():
                if item.name not in expected_judges or not item.is_file():
                    raise ValueError(f"Ambiguous partial evaluation state: {item}")
        if pair_path.exists() and not pair_path.is_file():
            raise ValueError(f"Ambiguous partial evaluation state: {pair_path}")


def _known_cost(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number or null")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite non-negative number or null")
    return result


def _cost_accounting(
    outcomes: Sequence[PairedCaseOutcome],
    judge_evidence: Sequence[JudgeEvidence],
    *,
    maximum: float | None,
    dispatch_stopped: bool,
) -> Mapping[str, object]:
    candidate_costs = [
        _known_cost(
            side.cost_usd,
            label=f"{outcome.case_id} {name} cost_usd",
        )
        for outcome in outcomes
        for name, side in (
            ("baseline", outcome.baseline),
            ("challenger", outcome.challenger),
        )
    ]
    judge_costs = []
    for item in judge_evidence:
        usage = item.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("Judge evidence usage must be a mapping")
        judge_costs.append(
            _known_cost(
                usage.get("cost_usd"),
                label=f"{item.get('case_id')} judge cost_usd",
            )
        )
    candidate_known = sum(cost for cost in candidate_costs if cost is not None)
    judge_known = sum(cost for cost in judge_costs if cost is not None)
    candidate_unknown = any(cost is None for cost in candidate_costs)
    judge_unknown = any(cost is None for cost in judge_costs)
    total_known = candidate_known + judge_known
    return {
        "maximum_cost_usd": maximum,
        "candidate_known_cost_usd": candidate_known,
        "candidate_unknown_cost": candidate_unknown,
        "judge_known_cost_usd": judge_known,
        "judge_unknown_cost": judge_unknown,
        "total_known_cost_usd": total_known,
        "unknown_cost": candidate_unknown or judge_unknown,
        "limit_reached": maximum is not None and total_known >= maximum,
        "limit_exceeded": maximum is not None and total_known > maximum,
        "dispatch_stopped": dispatch_stopped,
    }


def _budget_blocks_dispatch(accounting: Mapping[str, object]) -> bool:
    return bool(
        accounting["maximum_cost_usd"] is not None
        and (accounting["unknown_cost"] or accounting["limit_reached"])
    )


def _load_json_object(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_gold(
    runner: EvaluationRunner,
    case: EvaluationCase,
) -> Mapping[str, object]:
    references: dict[str, Mapping[str, object]] = {}
    for name, asset in sorted(case.references.items()):
        path = runner._verify_asset(asset)
        references[name] = _load_json_object(
            path,
            label=f"Scorer reference {name!r} for case {case.case_id!r}",
        )
    if len(references) == 1:
        return next(iter(references.values()))
    return references


def _load_successful_output(
    outcome: CaseSideOutcome,
    *,
    output_kind: str,
    case_id: str,
    side: str,
) -> Mapping[str, object] | None:
    if not outcome.succeeded or contract(output_kind).format != "json":
        return None
    assert outcome.output_path is not None
    return _load_json_object(
        Path(outcome.output_path),
        label=f"{side} output for case {case_id!r}",
    )


def _metric_definitions(suite: EvaluationSuite) -> Mapping[str, object]:
    definitions: dict[str, object] = {}
    for binding in (*suite.primary_metrics, *suite.hard_limits):
        existing = definitions.setdefault(binding.name, binding.definition)
        if existing is not binding.definition:
            raise ValueError(
                f"Suite resolves conflicting definitions for metric {binding.name!r}"
            )
    return MappingProxyType(definitions)


def _score_cases(
    runner: EvaluationRunner,
    suite: EvaluationSuite,
    cases: Sequence[EvaluationCase],
    outcomes: Sequence[PairedCaseOutcome],
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
) -> Mapping[str, tuple[MetricObservation, ...]]:
    by_case = {case.case_id: case for case in cases}
    definitions = _metric_definitions(suite)
    observations = {name: [] for name in definitions}
    for outcome in outcomes:
        case = by_case[outcome.case_id]
        baseline_output = _load_successful_output(
            outcome.baseline,
            output_kind=baseline.produces,
            case_id=case.case_id,
            side="baseline",
        )
        challenger_output = _load_successful_output(
            outcome.challenger,
            output_kind=challenger.produces,
            case_id=case.case_id,
            side="challenger",
        )
        gold = _load_gold(runner, case)
        for name, definition in definitions.items():
            baseline_value = (
                definition.observe(baseline_output, gold)
                if baseline_output is not None
                else None
            )
            challenger_value = (
                definition.observe(challenger_output, gold)
                if challenger_output is not None
                else None
            )
            observations[name].append(
                MetricObservation(
                    case_id=case.case_id,
                    metric=name,
                    baseline=baseline_value,
                    candidate=challenger_value,
                    slices=frozenset(case.strata),
                    baseline_succeeded=outcome.baseline.succeeded,
                    candidate_succeeded=outcome.challenger.succeeded,
                )
            )
    return MappingProxyType(
        {name: tuple(values) for name, values in observations.items()}
    )


def _complete_mean(
    observations: Sequence[MetricObservation],
    *,
    side: str,
) -> float | None:
    values = [
        observation.baseline if side == "baseline" else observation.candidate
        for observation in observations
    ]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None) / len(values)


def _complete_numeric(
    values: Sequence[float | None],
    operation: Callable[[Sequence[float]], float],
) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return operation(tuple(value for value in values if value is not None))


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _operational_values(
    outcomes: Sequence[PairedCaseOutcome],
) -> Mapping[str, Mapping[str, float | None] | object]:
    baseline = tuple(outcome.baseline for outcome in outcomes)
    challenger = tuple(outcome.challenger for outcome in outcomes)

    def side_values(side: Sequence[CaseSideOutcome]) -> dict[str, float | None]:
        costs = tuple(item.cost_usd for item in side)
        latencies = tuple(item.latency_seconds for item in side)
        return {
            "mean_cost_usd_per_case": _complete_numeric(
                costs, lambda known: sum(known) / len(known)
            ),
            "total_cost_usd": _complete_numeric(costs, sum),
            "known_cost_usd": sum(value for value in costs if value is not None),
            "p95_latency_seconds": _complete_numeric(latencies, _p95),
            "mean_latency_seconds": _complete_numeric(
                latencies, lambda known: sum(known) / len(known)
            ),
            "success_rate": (
                sum(item.succeeded for item in side) / len(side) if side else None
            ),
        }

    baseline_values = side_values(baseline)
    challenger_values = side_values(challenger)
    return {
        "baseline": baseline_values,
        "challenger": challenger_values,
        "unknown_cost": {
            "baseline": any(item.cost_usd is None for item in baseline),
            "challenger": any(item.cost_usd is None for item in challenger),
        },
    }


def _run_probes(
    suite: EvaluationSuite,
    outcomes: Sequence[PairedCaseOutcome],
    cases: Sequence[EvaluationCase],
) -> tuple[Mapping[str, object], ...]:
    results: list[Mapping[str, object]] = []
    for probe in suite.downstream_probes:
        try:
            raw = probe.definition(tuple(outcomes), tuple(cases))
            if not isinstance(raw, Mapping):
                raise TypeError("probe result must be a mapping")
            status = raw.get("status")
            if status not in {"passed", "failed", "unknown"}:
                raise ValueError(
                    "probe result status must be 'passed', 'failed', or 'unknown'"
                )
            result = {"id": probe.id, **dict(raw)}
        except Exception as error:
            result = {
                "id": probe.id,
                "status": "unknown",
                "reason": f"{type(error).__name__}: {error}",
            }
        results.append(result)
    return tuple(results)


def _judge_side_assignments(
    suite: EvaluationSuite,
    cases: Sequence[EvaluationCase],
    judge_id: str,
) -> Mapping[str, tuple[bool, int]]:
    ranked = sorted(
        cases,
        key=lambda case: hashlib.sha256(
            f"{suite.promotion.seed}\0{judge_id}\0{case.case_id}".encode("utf-8")
        ).digest(),
    )
    assignments: dict[str, tuple[bool, int]] = {}
    for index, case in enumerate(ranked):
        seed = int.from_bytes(
            hashlib.sha256(
                f"{suite.promotion.seed}\0{judge_id}\0{case.case_id}\0order".encode(
                    "utf-8"
                )
            ).digest()[:8],
            "big",
        )
        assignments[case.case_id] = (index % 2 == 0, seed)
    return MappingProxyType(assignments)


def _transcription_text(output: Mapping[str, object]) -> str | None:
    text = output.get("text")
    if isinstance(text, str):
        return text
    transcription = output.get("transcription")
    if isinstance(transcription, str):
        return transcription
    if isinstance(transcription, Mapping):
        nested = transcription.get("text")
        if isinstance(nested, str):
            return nested
    return None


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Declared page image has an unsupported or malformed image format")


def _judge_usage(
    result: JudgeExecutionResult | None = None, error: Exception | None = None
) -> Mapping[str, int | float | None]:
    if result is not None:
        return {
            "prompt_tokens": result.prompt_tokens,
            "output_tokens": result.output_tokens,
            "thought_tokens": result.thought_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": result.cost_usd,
        }
    return {
        "prompt_tokens": getattr(
            error, "prompt_tokens", getattr(error, "tokens_in", None)
        ),
        "output_tokens": getattr(
            error, "output_tokens", getattr(error, "tokens_out", None)
        ),
        "thought_tokens": getattr(error, "thought_tokens", None),
        "total_tokens": getattr(error, "total_tokens", None),
        "cost_usd": getattr(error, "cost_usd", None),
    }


def _unknown_judge_evidence(
    *,
    case_id: str,
    binding,
    baseline_is_a: bool,
    seed: int,
    reason: str,
    error: Exception | None = None,
    result: JudgeExecutionResult | None = None,
) -> JudgeEvidence:
    return {
        "case_id": case_id,
        "metric": binding.metric,
        "judge_id": binding.judge.id,
        "judge_fingerprint": binding.judge.fingerprint,
        "model": binding.judge.model,
        "status": "unknown",
        "order": {
            "A": "baseline" if baseline_is_a else "challenger",
            "B": "challenger" if baseline_is_a else "baseline",
            "seed": seed,
        },
        "winner": None,
        "response": None,
        "confidence": None,
        "reason": None,
        "failure_flags": None,
        "usage": _judge_usage(result=result, error=error),
        "error_kind": None if error is None else type(error).__name__,
        "error_message": reason,
    }


def _run_judges(
    runner: EvaluationRunner,
    suite: EvaluationSuite,
    cases: Sequence[EvaluationCase],
    outcomes: Sequence[PairedCaseOutcome],
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    executor: JudgeExecutor | None,
    bindings: Sequence[object] | None = None,
) -> tuple[JudgeEvidence, ...]:
    by_case = {case.case_id: case for case in cases}
    evidence: list[JudgeEvidence] = []
    for binding in suite.judges if bindings is None else bindings:
        assignments = _judge_side_assignments(suite, cases, binding.judge.id)
        for outcome in outcomes:
            case = by_case[outcome.case_id]
            baseline_is_a, seed = assignments[case.case_id]
            if not binding.judge.can_auto_qualify:
                evidence.append(
                    _unknown_judge_evidence(
                        case_id=case.case_id,
                        binding=binding,
                        baseline_is_a=baseline_is_a,
                        seed=seed,
                        reason="judge identity cannot auto-qualify",
                    )
                )
                continue
            if executor is None:
                evidence.append(
                    _unknown_judge_evidence(
                        case_id=case.case_id,
                        binding=binding,
                        baseline_is_a=baseline_is_a,
                        seed=seed,
                        reason="judge executor unavailable",
                    )
                )
                continue
            result = None
            try:
                baseline_output = _load_successful_output(
                    outcome.baseline,
                    output_kind=baseline.produces,
                    case_id=case.case_id,
                    side="baseline",
                )
                challenger_output = _load_successful_output(
                    outcome.challenger,
                    output_kind=challenger.produces,
                    case_id=case.case_id,
                    side="challenger",
                )
                if baseline_output is None or challenger_output is None:
                    raise ValueError("both candidate outputs are required for judging")
                baseline_text = _transcription_text(baseline_output)
                challenger_text = _transcription_text(challenger_output)
                if baseline_text is None or challenger_text is None:
                    raise ValueError("candidate output has no transcription text")
                image_asset = case.inputs.get("page_image_clean")
                if isinstance(image_asset, Mapping):
                    image_asset = image_asset.get(case.page_id)
                if not isinstance(image_asset, CaseAsset):
                    raise ValueError("case has no declared page_image_clean input")
                image_path = runner._verify_asset(image_asset)
                image_bytes = image_path.read_bytes()
                if baseline_is_a:
                    text_a, text_b = baseline_text, challenger_text
                else:
                    text_a, text_b = challenger_text, baseline_text
                result = executor.execute(
                    judge=binding.judge,
                    source_image=image_bytes,
                    source_mime=_image_mime(image_bytes),
                    text_a=text_a,
                    text_b=text_b,
                )
                if not isinstance(result, JudgeExecutionResult):
                    raise TypeError("judge executor returned a malformed result")
                if result.model != binding.judge.model:
                    raise ValueError(
                        "judge executor returned evidence from the wrong model"
                    )
                schema = binding.judge.response_schema_definition
                if not isinstance(schema, ResponseSchema):
                    raise TypeError(
                        "judge executor used an unregistered response schema"
                    )
                response = schema.validate(
                    {
                        "winner": result.response.winner,
                        "confidence": result.response.confidence,
                        "reason": result.response.reason,
                        "failure_flags": list(result.response.failure_flags),
                    }
                )
                winner = response.winner
                mapped_winner = (
                    "tie"
                    if winner == "tie"
                    else (
                        "baseline" if (winner == "A") == baseline_is_a else "challenger"
                    )
                )
                evidence.append(
                    {
                        "case_id": case.case_id,
                        "metric": binding.metric,
                        "judge_id": binding.judge.id,
                        "judge_fingerprint": binding.judge.fingerprint,
                        "model": result.model,
                        "status": "completed",
                        "order": {
                            "A": "baseline" if baseline_is_a else "challenger",
                            "B": "challenger" if baseline_is_a else "baseline",
                            "seed": seed,
                        },
                        "winner": mapped_winner,
                        "response": {
                            "winner": winner,
                            "confidence": response.confidence,
                            "reason": response.reason,
                            "failure_flags": list(response.failure_flags),
                        },
                        "confidence": response.confidence,
                        "reason": response.reason,
                        "failure_flags": list(response.failure_flags),
                        "usage": _judge_usage(result=result),
                        "error_kind": None,
                        "error_message": None,
                    }
                )
            except Exception as error:
                evidence.append(
                    _unknown_judge_evidence(
                        case_id=case.case_id,
                        binding=binding,
                        baseline_is_a=baseline_is_a,
                        seed=seed,
                        reason=str(error),
                        error=error,
                        result=(
                            result if isinstance(result, JudgeExecutionResult) else None
                        ),
                    )
                )
    return tuple(evidence)


def _judge_aggregates(
    suite: EvaluationSuite, evidence: Sequence[JudgeEvidence]
) -> tuple[Mapping[str, object], ...]:
    aggregates = []
    for binding in suite.judges:
        selected = [
            item
            for item in evidence
            if item["metric"] == binding.metric
            and item["judge_fingerprint"] == binding.judge.fingerprint
        ]
        completed = [item for item in selected if item["status"] == "completed"]
        costs = [item["usage"]["cost_usd"] for item in selected]
        aggregates.append(
            {
                "metric": binding.metric,
                "judge_id": binding.judge.id,
                "judge_fingerprint": binding.judge.fingerprint,
                "wins": sum(item["winner"] == "challenger" for item in completed),
                "losses": sum(item["winner"] == "baseline" for item in completed),
                "ties": sum(item["winner"] == "tie" for item in completed),
                "unknowns": sum(item["status"] != "completed" for item in selected),
                "mean_confidence": (
                    sum(float(item["confidence"]) for item in completed)
                    / len(completed)
                    if completed
                    else None
                ),
                "total_cost_usd": (
                    sum(float(cost) for cost in costs)
                    if costs and all(cost is not None for cost in costs)
                    else None
                ),
                "positional_a_wins": sum(
                    item["response"]["winner"] == "A" for item in completed
                ),
                "positional_b_wins": sum(
                    item["response"]["winner"] == "B" for item in completed
                ),
                "observations": selected,
            }
        )
    return tuple(aggregates)


def _execution_observations(
    outcomes: Sequence[PairedCaseOutcome],
) -> tuple[MetricObservation, ...]:
    return tuple(
        MetricObservation(
            case_id=outcome.case_id,
            metric="execution_success",
            baseline=None,
            candidate=None,
            baseline_succeeded=outcome.baseline.succeeded,
            candidate_succeeded=outcome.challenger.succeeded,
        )
        for outcome in outcomes
    )


def _scorecard(
    suite: EvaluationSuite,
    outcomes: Sequence[PairedCaseOutcome],
    observations: Mapping[str, tuple[MetricObservation, ...]],
    probes: Sequence[Mapping[str, object]],
    judge_evidence: Sequence[JudgeEvidence],
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    *,
    expected_case_count: int | None = None,
    cost_accounting: Mapping[str, object] | None = None,
) -> tuple[Mapping[str, object], str, tuple[str, ...]]:
    if expected_case_count is None:
        expected_case_count = len(outcomes)
    if cost_accounting is None:
        cost_accounting = _cost_accounting(
            outcomes,
            judge_evidence,
            maximum=None,
            dispatch_stopped=False,
        )
    primary = []
    protected = []
    for binding in suite.primary_metrics:
        metric_observations = observations[binding.name]
        direction = MetricDirection(binding.direction)
        primary.append(
            paired_comparison(
                binding.name,
                metric_observations,
                direction=direction,
                policy=ComparisonPolicy(
                    minimum_effect=binding.minimum_effect,
                    confidence=binding.confidence,
                    bootstrap_samples=suite.promotion.paired_bootstrap_samples,
                    seed=suite.promotion.seed,
                    minimum_pairs=suite.promotion.minimum_completed_cases,
                ),
            )
        )
        for slice_name in suite.protected_slices:
            protected.append(
                compare_protected_slice(
                    slice_name,
                    binding.name,
                    metric_observations,
                    direction=direction,
                    minimum_cases=suite.slice_policy.minimum_cases,
                    maximum_regression=suite.slice_policy.maximum_regression,
                    confidence=binding.confidence,
                    bootstrap_samples=suite.promotion.paired_bootstrap_samples,
                    seed=suite.promotion.seed,
                )
            )

    hard_limits = []
    for binding in suite.hard_limits:
        hard_limits.append(
            evaluate_hard_limit(
                HardLimit(
                    binding.name,
                    minimum=binding.minimum,
                    maximum=binding.maximum,
                ),
                _complete_mean(observations[binding.name], side="candidate"),
            )
        )

    execution = _execution_observations(outcomes)
    reliability = {
        "baseline": summarize_reliability(execution, side="baseline"),
        "challenger": summarize_reliability(execution, side="candidate"),
    }
    operations = _operational_values(outcomes)
    challenger_operations = operations["challenger"]
    assert isinstance(challenger_operations, Mapping)
    operational_limits = [
        evaluate_hard_limit(
            HardLimit(
                binding.name,
                minimum=binding.minimum,
                maximum=binding.maximum,
            ),
            challenger_operations.get(binding.name),
        )
        for binding in suite.operational_limits
    ]

    required_hard_limits = (
        hard_limits if suite.promotion.require_all_hard_limits else ()
    )
    base = qualification_decision(
        primary_metrics=primary,
        hard_limits=required_hard_limits,
        protected_slices=protected,
    )
    reasons = list(base.reasons)
    completed = sum(
        outcome.baseline.succeeded and outcome.challenger.succeeded
        for outcome in outcomes
    )
    if completed < suite.promotion.minimum_completed_cases:
        reasons.append(
            "minimum completed cases not met: "
            f"{completed} < {suite.promotion.minimum_completed_cases}"
        )
    missing_cases = expected_case_count - len(outcomes)
    if missing_cases:
        reasons.append(
            f"missing paired case observations: {missing_cases} of {expected_case_count}"
        )
    if cost_accounting["unknown_cost"] and not any(
        item.get("status") != "completed" for item in judge_evidence
    ):
        reasons.append("cost ceiling observation unknown")
    if cost_accounting["limit_exceeded"]:
        reasons.append("maximum evaluation cost exceeded")
    elif cost_accounting["dispatch_stopped"]:
        reasons.append("maximum evaluation cost reached before next dispatch")
    for result in operational_limits:
        if result.decision.value != "pass":
            reasons.append(
                f"operational limit {result.decision.value}: {result.metric}"
            )
    if suite.promotion.require_all_downstream_probes:
        reasons.extend(
            f"downstream probe {probe.get('status', 'unknown')}: {probe['id']}"
            for probe in probes
            if probe.get("status") != "passed"
        )
    unknown_judges = [
        item for item in judge_evidence if item.get("status") != "completed"
    ]
    reasons.extend(
        f"required judge unknown: {binding.judge.id}"
        for binding in suite.judges
        if any(
            item.get("judge_fingerprint") == binding.judge.fingerprint
            for item in unknown_judges
        )
    )
    identity_waivers = tuple(
        (side, candidate.fingerprint)
        for side, candidate in (
            ("baseline", baseline),
            ("challenger", challenger),
        )
        if not candidate.can_auto_qualify
    )
    reasons.extend(
        f"{side} identity requires reproducibility waiver: {fingerprint}"
        for side, fingerprint in identity_waivers
    )
    if not suite.qualification_eligible:
        reasons.append("suite is not qualification eligible")
    elif not suite.can_auto_qualify:
        reasons.append("judge identity cannot auto-qualify")

    hard_limit_failed = any(
        result.decision.value == "fail" for result in required_hard_limits
    )
    cost_limit_failed = bool(cost_accounting["limit_exceeded"])
    unknown_evidence = (
        base.status.value == "unknown"
        or any(result.decision.value == "unknown" for result in operational_limits)
        or (
            suite.promotion.require_all_downstream_probes
            and any(probe.get("status") == "unknown" for probe in probes)
        )
        or bool(unknown_judges)
        or bool(cost_accounting["unknown_cost"])
    )
    insufficient_evidence = (
        base.status.value == "insufficient"
        or completed < suite.promotion.minimum_completed_cases
        or missing_cases > 0
    )
    rejected_evidence = (
        not base.qualified
        or any(result.decision.value == "fail" for result in operational_limits)
        or (
            suite.promotion.require_all_downstream_probes
            and any(probe.get("status") == "failed" for probe in probes)
        )
        or not suite.qualification_eligible
        or not suite.can_auto_qualify
    )
    if hard_limit_failed or cost_limit_failed:
        decision = "rejected"
    elif unknown_evidence:
        decision = "unknown"
    elif insufficient_evidence:
        decision = "insufficient"
    elif rejected_evidence:
        decision = "rejected"
    elif len(identity_waivers) == 1:
        decision = "manual_review_required"
    elif identity_waivers:
        decision = "rejected"
    else:
        decision = "qualified"
    aggregates = {
        "completed_cases": completed,
        "requested_cases": expected_case_count,
        "observed_cases": len(outcomes),
        "cost_ceiling": cost_accounting,
        "metrics": {
            name: {
                "observations": values,
                "comparison": next(
                    (item for item in primary if item.metric == name),
                    None,
                ),
            }
            for name, values in observations.items()
        },
        "judges": _judge_aggregates(suite, judge_evidence),
        "hard_limits": hard_limits,
        "protected_slices": protected,
        "reliability": reliability,
        "operations": {
            **operations,
            "limits": operational_limits,
        },
    }
    serialized = _json_value(aggregates)
    assert isinstance(serialized, Mapping)
    return serialized, decision, tuple(dict.fromkeys(reasons))


def _report_environment(
    *,
    executor: str,
    workers: int,
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    supplied: Mapping[str, object] | None,
) -> Mapping[str, object]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executor": executor,
        "workers": workers,
        "candidate_identities": {
            "baseline": {
                "model": baseline.model,
                "model_identity": baseline.model_identity,
                "implementation_fingerprint": baseline.implementation_fingerprint,
                "prompt_name": baseline.prompt_name,
                "prompt_hash": baseline.prompt_hash,
                "tracked": baseline.tracked,
            },
            "challenger": {
                "model": challenger.model,
                "model_identity": challenger.model_identity,
                "implementation_fingerprint": challenger.implementation_fingerprint,
                "prompt_name": challenger.prompt_name,
                "prompt_hash": challenger.prompt_hash,
                "tracked": challenger.tracked,
            },
        },
        "operator": dict(supplied or {}),
    }


def _build_terminal_report(
    *,
    run_id: str,
    status: str,
    decision: str,
    started_at: str,
    finished_at: str,
    suite: EvaluationSuite,
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    cases: Sequence[EvaluationCase],
    outcomes: Sequence[PairedCaseOutcome],
    aggregates: Mapping[str, object],
    probes: Sequence[Mapping[str, object]],
    reasons: Sequence[str],
    environment: Mapping[str, object],
) -> dict[str, Any]:
    report = build_report(
        run_id=run_id,
        status=status,
        decision=decision,
        started_at=started_at,
        finished_at=finished_at,
        suite=ReportIdentity(suite.id, suite.fingerprint),
        baseline=ReportIdentity(baseline.id, baseline.fingerprint),
        challenger=ReportIdentity(challenger.id, challenger.fingerprint),
        judges=tuple(
            ReportIdentity(binding.judge.id, binding.judge.fingerprint)
            for binding in suite.judges
        ),
        cases=outcomes,
        aggregates=aggregates,
        downstream_probes=probes,
        qualification={"decision": decision, "reasons": list(reasons)},
        environment=environment,
    )
    by_case = {case.case_id: case for case in cases}
    metric_observations = aggregates.get("metrics", {})
    for case_report in report["cases"]:
        case_id = case_report["case_id"]
        case = by_case[case_id]
        case_report["side_order"] = list(_side_order(run_id, case_id))
        case_report["strata"] = list(case.strata)
        case_report["observations"] = {
            metric: next(
                (
                    observation
                    for observation in details["observations"]
                    if observation["case_id"] == case_id
                ),
                None,
            )
            for metric, details in metric_observations.items()
        }
        case_report["judges"] = [
            observation
            for judge in aggregates.get("judges", ())
            for observation in judge["observations"]
            if observation["case_id"] == case_id
        ]
    report["report_fingerprint"] = report_fingerprint(report)
    return report


def run_evaluation(
    *,
    run_id: str,
    suite: EvaluationSuite,
    baseline: ResolvedCandidate,
    challenger: ResolvedCandidate,
    store: EvaluationStore,
    run_root: Path,
    asset_resolver: AssetResolver,
    executor: str = "subprocess",
    workers: int = 1,
    cases: Sequence[EvaluationCase] | None = None,
    environment: Mapping[str, object] | None = None,
    judge_executor: JudgeExecutor | None = None,
    max_cost_usd: float | None = None,
    resume: str | None = None,
) -> EvaluationWorkflowResult:
    """Verify, execute, score, publish, and index one immutable paired run.

    Downstream probe definitions have one concrete callable shape:
    ``probe(paired_cases, evaluation_cases) -> {"status": ...}``.
    Candidate execution finishes for both sides before references are loaded or
    either output is exposed to scorers and probes.
    """

    run_id = _validated_run_id(run_id)
    requested_ceiling = _validated_cost_ceiling(max_cost_usd)
    if resume is not None and resume != run_id:
        raise ValueError("resume must identify the same run as run_id")
    runner = EvaluationRunner(
        run_root=run_root,
        asset_resolver=asset_resolver,
        executor=executor,
        workers=workers,
    )
    selected = runner.verify(
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        cases=cases,
    )
    run_dir = run_root / run_id
    expected_manifest = _run_manifest(
        run_id=run_id,
        suite=suite,
        baseline=baseline,
        challenger=challenger,
        cases=selected,
        executor=executor,
        max_cost_usd=requested_ceiling,
    )
    outcomes_by_case: dict[str, PairedCaseOutcome] = {}
    judge_by_identity: dict[tuple[str, str, str], JudgeEvidence] = {}

    if resume is not None:
        indexed = store.run(resume)
        if indexed is None:
            raise ValueError(f"Evaluation run does not exist: {resume!r}")
        if indexed.status != "running":
            raise ValueError(
                f"Terminal evaluation run cannot be resumed: {resume!r} "
                f"({indexed.status})"
            )
        if (
            indexed.suite_id != suite.id
            or indexed.suite_fingerprint != suite.fingerprint
            or indexed.baseline_fingerprint != baseline.fingerprint
            or indexed.challenger_fingerprint != challenger.fingerprint
        ):
            raise ValueError("Resumed evaluation store identity drift")
        if not run_dir.is_dir():
            raise ValueError(
                "Resumed evaluation run has no immutable evidence directory"
            )
        manifest, effective_ceiling = _load_run_manifest(
            run_dir,
            expected=expected_manifest,
            requested_cost_ceiling=requested_ceiling,
        )
        _validate_resume_layout(run_dir, selected, suite)
        started_at = indexed.started_at
        for case in selected:
            outcome = _read_pair_record(
                run_dir,
                manifest,
                case,
                baseline,
                challenger,
            )
            if outcome is None:
                continue
            outcomes_by_case[case.case_id] = outcome
            for binding in suite.judges:
                evidence = _read_judge_record(run_dir, manifest, case, binding)
                if evidence is not None:
                    judge_by_identity[
                        (case.case_id, binding.metric, binding.judge.fingerprint)
                    ] = evidence
    else:
        effective_ceiling = requested_ceiling
        started_at = _utc_now()
        store.begin_run(
            run_id=run_id,
            suite_id=suite.id,
            suite_fingerprint=suite.fingerprint,
            baseline_fingerprint=baseline.fingerprint,
            challenger_fingerprint=challenger.fingerprint,
            started_at=started_at,
        )
        manifest = {}

    report_path = run_dir / "report.json"
    report_environment = _report_environment(
        executor=executor,
        workers=workers,
        baseline=baseline,
        challenger=challenger,
        supplied=environment,
    )

    def ordered_outcomes() -> tuple[PairedCaseOutcome, ...]:
        return tuple(
            outcomes_by_case[case.case_id]
            for case in selected
            if case.case_id in outcomes_by_case
        )

    def ordered_judges() -> tuple[JudgeEvidence, ...]:
        return tuple(
            judge_by_identity[(case.case_id, binding.metric, binding.judge.fingerprint)]
            for binding in suite.judges
            for case in selected
            if (
                case.case_id,
                binding.metric,
                binding.judge.fingerprint,
            )
            in judge_by_identity
        )

    def current_costs(*, stopped: bool = False) -> Mapping[str, object]:
        return _cost_accounting(
            ordered_outcomes(),
            ordered_judges(),
            maximum=effective_ceiling,
            dispatch_stopped=stopped,
        )

    def run_missing_judges(
        case: EvaluationCase,
        outcome: PairedCaseOutcome,
        *,
        enforce_ceiling: bool,
    ) -> bool:
        for binding in suite.judges:
            key = (case.case_id, binding.metric, binding.judge.fingerprint)
            if key in judge_by_identity:
                continue
            if enforce_ceiling and _budget_blocks_dispatch(current_costs()):
                return True
            evidence = _run_judges(
                runner,
                suite,
                selected,
                (outcome,),
                baseline,
                challenger,
                judge_executor,
                bindings=(binding,),
            )
            if len(evidence) != 1:
                raise RuntimeError(
                    "Judge execution did not produce exactly one observation"
                )
            _write_judge_record(
                run_dir,
                manifest,
                case,
                binding,
                evidence[0],
            )
            judge_by_identity[key] = evidence[0]
        return False

    def record_pair(case: EvaluationCase, outcome: PairedCaseOutcome) -> None:
        outcomes_by_case[case.case_id] = outcome
        _write_pair_record(run_dir, manifest, case, outcome)

    dispatch_stopped = False
    try:
        if resume is None:
            run_dir.mkdir(parents=True)
            _immutable_json(run_dir / _RUN_MANIFEST, expected_manifest)
            manifest = _read_checkpoint(
                run_dir / _RUN_MANIFEST, label="evaluation run manifest"
            )

        if effective_ceiling is None:
            for case in selected:
                outcome = outcomes_by_case.get(case.case_id)
                if outcome is not None:
                    run_missing_judges(case, outcome, enforce_ceiling=False)
            remaining = tuple(
                case for case in selected if case.case_id not in outcomes_by_case
            )

            def complete_case(outcome: PairedCaseOutcome) -> None:
                case = next(
                    case for case in selected if case.case_id == outcome.case_id
                )
                record_pair(case, outcome)
                run_missing_judges(case, outcome, enforce_ceiling=False)

            if remaining:
                runner.run(
                    run_id=run_id,
                    cases=remaining,
                    baseline=baseline,
                    challenger=challenger,
                    allow_existing=True,
                    on_case=complete_case,
                )
        else:
            blocked = False
            for case in selected:
                outcome = outcomes_by_case.get(case.case_id)
                if outcome is not None:
                    if not blocked:
                        blocked = run_missing_judges(
                            case,
                            outcome,
                            enforce_ceiling=True,
                        )
                    continue
                if blocked or _budget_blocks_dispatch(current_costs()):
                    blocked = True
                    continue
                outcome = runner.run(
                    run_id=run_id,
                    cases=(case,),
                    baseline=baseline,
                    challenger=challenger,
                    allow_existing=True,
                )[0]
                record_pair(case, outcome)
                blocked = run_missing_judges(
                    case,
                    outcome,
                    enforce_ceiling=True,
                )
            dispatch_stopped = blocked and (
                len(outcomes_by_case) < len(selected)
                or any(
                    (
                        case.case_id,
                        binding.metric,
                        binding.judge.fingerprint,
                    )
                    not in judge_by_identity
                    for case in selected
                    if case.case_id in outcomes_by_case
                    for binding in suite.judges
                )
            )

        outcomes = ordered_outcomes()
        judge_evidence = list(ordered_judges())
        for case in selected:
            outcome = outcomes_by_case.get(case.case_id)
            if outcome is None:
                continue
            for binding in suite.judges:
                key = (case.case_id, binding.metric, binding.judge.fingerprint)
                if key in judge_by_identity:
                    continue
                baseline_is_a, seed = _judge_side_assignments(
                    suite, selected, binding.judge.id
                )[case.case_id]
                judge_evidence.append(
                    _unknown_judge_evidence(
                        case_id=case.case_id,
                        binding=binding,
                        baseline_is_a=baseline_is_a,
                        seed=seed,
                        reason="judge was not dispatched because the cost ceiling was reached",
                    )
                )
        judge_evidence_tuple = tuple(judge_evidence)
        cost_accounting = _cost_accounting(
            outcomes,
            judge_evidence_tuple,
            maximum=effective_ceiling,
            dispatch_stopped=dispatch_stopped,
        )
        observations = _score_cases(
            runner,
            suite,
            selected,
            outcomes,
            baseline,
            challenger,
        )
        probes = _run_probes(suite, outcomes, selected)
        aggregates, decision, reasons = _scorecard(
            suite,
            outcomes,
            observations,
            probes,
            judge_evidence_tuple,
            baseline,
            challenger,
            expected_case_count=len(selected),
            cost_accounting=cost_accounting,
        )
        report = _build_terminal_report(
            run_id=run_id,
            status="completed",
            decision=decision,
            started_at=started_at,
            finished_at=_utc_now(),
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            cases=selected,
            outcomes=outcomes,
            aggregates=aggregates,
            probes=probes,
            reasons=reasons,
            environment=report_environment,
        )
        write_report(report_path, report)
        store.index_report(report_path)
        return EvaluationWorkflowResult(report_path, MappingProxyType(report))
    except Exception as error:
        outcomes = ordered_outcomes()
        judge_evidence = ordered_judges()
        failure_reason = f"{type(error).__name__}: {error}"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            failed_aggregates: dict[str, object] = {
                "failure": {
                    "kind": type(error).__name__,
                    "message": str(error),
                },
                "judges": _judge_aggregates(suite, judge_evidence),
            }
            try:
                failed_aggregates["cost_ceiling"] = _cost_accounting(
                    outcomes,
                    judge_evidence,
                    maximum=effective_ceiling,
                    dispatch_stopped=dispatch_stopped,
                )
            except ValueError:
                failed_aggregates["cost_ceiling"] = {
                    "maximum_cost_usd": effective_ceiling,
                    "unknown_cost": True,
                    "dispatch_stopped": dispatch_stopped,
                }
            failed_report = _build_terminal_report(
                run_id=run_id,
                status="failed",
                decision="error",
                started_at=started_at,
                finished_at=_utc_now(),
                suite=suite,
                baseline=baseline,
                challenger=challenger,
                cases=selected,
                outcomes=outcomes,
                aggregates=failed_aggregates,
                probes=(),
                reasons=(failure_reason,),
                environment=report_environment,
            )
            write_report(report_path, failed_report)
            store.index_report(report_path)
        except Exception as publication_error:
            if hasattr(error, "add_note"):
                error.add_note(
                    "Failed to publish terminal evaluation report: "
                    f"{type(publication_error).__name__}: {publication_error}"
                )
        raise
