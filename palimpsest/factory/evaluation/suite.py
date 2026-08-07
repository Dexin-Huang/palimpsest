"""Strict evaluation-suite and content-addressed case-manifest records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

from palimpsest.factory.core.contracts import contract, validate_payload
from palimpsest.factory.evaluation import _record
from palimpsest.factory.evaluation.candidate import (
    RecordError,
    ResolvedCandidate,
    _load_yaml,
    _record_id,
    _schema_version,
    _strict_mapping,
    _string,
    canonical_json,
    content_fingerprint,
    immutable_json,
)
from palimpsest.factory.evaluation.judge import ResolvedJudge, _resolve_registered

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _number(value: object, *, field: str, minimum: float | None = None) -> float:
    return _record.number(value, field=field, error_cls=RecordError, minimum=minimum)


def _positive_integer(value: object, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise RecordError(f"{field} must be a positive integer")
    return value


def _unique_strings(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RecordError(f"{field} must be a list")
    result = tuple(_record_id(item, field=f"{field}[]") for item in value)
    if len(result) != len(set(result)):
        raise RecordError(f"{field} contains duplicates")
    return result


def _duplicate_rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecordError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _local_path(value: object, *, root: Path, field: str) -> tuple[str, Path]:
    relative = _string(value, field=field)
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise RecordError(f"{field} must be a normalized relative path")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RecordError(f"{field} escapes the evaluation data root")
    return candidate.as_posix(), resolved


@dataclass(frozen=True, slots=True)
class CaseAsset:
    sha256: str
    path: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    schema_version: int
    case_id: str
    doc_id: str
    page_id: str | None
    pages: tuple[Mapping[str, object], ...]
    inputs: Mapping[str, CaseAsset | Mapping[str, CaseAsset]]
    references: Mapping[str, CaseAsset]
    strata: tuple[str, ...]
    license: str
    adjudication: Mapping[str, object]
    fingerprint: str


def _load_asset(
    value: object,
    *,
    field: str,
    asset_root: Path,
    verify_local: bool,
) -> CaseAsset:
    record = _strict_mapping(
        value,
        field=field,
        required={"sha256"},
        optional={"path", "source"},
    )
    has_path = "path" in record
    has_source = "source" in record
    if has_path == has_source:
        raise RecordError(f"{field} must contain exactly one of path or source")
    digest = _string(record["sha256"], field=f"{field}.sha256")
    if not _SHA256.fullmatch(digest):
        raise RecordError(f"{field}.sha256 must be a lowercase SHA-256 digest")
    if has_path:
        relative, resolved = _local_path(
            record["path"], root=asset_root, field=f"{field}.path"
        )
        if verify_local:
            try:
                actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except OSError as error:
                raise RecordError(
                    f"Cannot verify {field}.path {relative!r}: {error}"
                ) from error
            if actual != digest:
                raise RecordError(
                    f"Hash mismatch for {field}.path {relative!r}: expected {digest}, got {actual}"
                )
        return CaseAsset(sha256=digest, path=relative)
    source = _string(record["source"], field=f"{field}.source")
    parsed = urlsplit(source.split(":", 1)[1] if source.startswith("iiif:") else source)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RecordError(f"{field}.source must be an http(s) or iiif:http(s) URL")
    return CaseAsset(sha256=digest, source=source)


def _load_asset_map(
    value: object,
    *,
    field: str,
    asset_root: Path,
    verify_local: bool,
    allow_empty: bool,
) -> Mapping[str, CaseAsset]:
    if not isinstance(value, dict) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        raise RecordError(f"{field} must be a{qualifier} mapping")
    assets: dict[str, CaseAsset] = {}
    for name, asset in value.items():
        safe_name = _record_id(name, field=f"{field} key")
        assets[safe_name] = _load_asset(
            asset,
            field=f"{field}.{safe_name}",
            asset_root=asset_root,
            verify_local=verify_local,
        )

    return MappingProxyType(assets)


def _load_input_map(
    value: object,
    *,
    asset_root: Path,
    verify_local: bool,
) -> Mapping[str, CaseAsset | Mapping[str, CaseAsset]]:
    if not isinstance(value, dict):
        raise RecordError("case.inputs must be a mapping")
    inputs: dict[str, CaseAsset | Mapping[str, CaseAsset]] = {}
    for name, raw in value.items():
        safe_name = _record_id(name, field="case.inputs key")
        if not isinstance(raw, dict):
            raise RecordError(
                f"case.inputs.{safe_name} must be an asset or page mapping"
            )
        if "sha256" in raw:
            inputs[safe_name] = _load_asset(
                raw,
                field=f"case.inputs.{safe_name}",
                asset_root=asset_root,
                verify_local=verify_local,
            )
            continue
        by_page: dict[str, CaseAsset] = {}
        if not raw:
            raise RecordError(f"case.inputs.{safe_name} page mapping must not be empty")
        for page_id, asset in raw.items():
            safe_page_id = _record_id(page_id, field=f"case.inputs.{safe_name} page_id")
            by_page[safe_page_id] = _load_asset(
                asset,
                field=f"case.inputs.{safe_name}.{safe_page_id}",
                asset_root=asset_root,
                verify_local=verify_local,
            )
        inputs[safe_name] = MappingProxyType(by_page)
    return MappingProxyType(inputs)


def _asset_identity(asset: CaseAsset | Mapping[str, CaseAsset]) -> object:
    if isinstance(asset, Mapping):
        return {
            page_id: _asset_identity(page_asset)
            for page_id, page_asset in asset.items()
        }
    return {"path": asset.path, "source": asset.source, "sha256": asset.sha256}


def _load_case(
    value: object,
    *,
    asset_root: Path,
    verify_local: bool,
) -> EvaluationCase:
    record = _strict_mapping(
        value,
        field="case",
        required={
            "schema_version",
            "case_id",
            "doc_id",
            "page_id",
            "pages",
            "inputs",
            "references",
            "strata",
            "license",
            "adjudication",
        },
    )
    schema_version = _schema_version(record["schema_version"])
    case_id = _record_id(record["case_id"], field="case.case_id")
    doc_id = _record_id(record["doc_id"], field="case.doc_id")
    page_value = record["page_id"]
    page_id = (
        None if page_value is None else _record_id(page_value, field="case.page_id")
    )
    if not isinstance(record["pages"], list):
        raise RecordError("case.pages must be a list")
    try:
        validate_payload(
            "page_list",
            {"doc_id": doc_id, "pages": record["pages"]},
            expected_doc_id=doc_id,
        )
    except ValueError as error:
        raise RecordError(f"Invalid case.pages: {error}") from error
    orders = [page["order"] for page in record["pages"]]
    if len(orders) != len(set(orders)) or orders != sorted(orders):
        raise RecordError("case.pages order values must be unique and ascending")
    page_ids = {page["page_id"] for page in record["pages"]}
    if page_id is not None and page_id not in page_ids:
        raise RecordError("case.page_id must identify a member of case.pages")
    pages_value = immutable_json(record["pages"], field="case.pages")
    assert isinstance(pages_value, tuple) and all(
        isinstance(page, Mapping) for page in pages_value
    )
    pages = pages_value
    inputs = _load_input_map(
        record["inputs"],
        asset_root=asset_root,
        verify_local=verify_local,
    )
    references = _load_asset_map(
        record["references"],
        field="case.references",
        asset_root=asset_root,
        verify_local=verify_local,
        allow_empty=False,
    )
    strata = _unique_strings(record["strata"], field="case.strata")
    license_name = _string(record["license"], field="case.license")
    adjudication_record = _strict_mapping(
        record["adjudication"],
        field="case.adjudication",
        required={"method", "version"},
    )
    _record_id(adjudication_record["method"], field="case.adjudication.method")
    _positive_integer(adjudication_record["version"], field="case.adjudication.version")
    adjudication = immutable_json(adjudication_record, field="case.adjudication")
    assert isinstance(adjudication, Mapping)
    identity = {
        "schema_version": schema_version,
        "case_id": case_id,
        "doc_id": doc_id,
        "page_id": page_id,
        "pages": pages,
        "inputs": {name: _asset_identity(asset) for name, asset in inputs.items()},
        "references": {
            name: _asset_identity(asset) for name, asset in references.items()
        },
        "strata": strata,
        "license": license_name,
        "adjudication": adjudication,
    }
    return EvaluationCase(
        schema_version=schema_version,
        case_id=case_id,
        doc_id=doc_id,
        page_id=page_id,
        pages=pages,
        inputs=inputs,
        references=references,
        strata=strata,
        license=license_name,
        adjudication=adjudication,
        fingerprint=content_fingerprint(identity),
    )


def load_case_manifest(
    path: str | Path,
    *,
    asset_root: str | Path | None = None,
    verify_local: bool = True,
) -> tuple[EvaluationCase, ...]:
    """Load canonical JSONL and verify every local content-addressed object."""
    manifest = Path(path)
    if manifest.suffix.lower() != ".jsonl":
        raise RecordError(f"Expected a JSONL case manifest: {manifest}")
    root = Path(asset_root) if asset_root is not None else manifest.parent
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RecordError(f"Cannot load {manifest}: {error}") from error
    if not lines:
        raise RecordError("Case manifest must not be empty")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line:
            raise RecordError(f"Blank line in case manifest at line {line_number}")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_duplicate_rejecting_object,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    RecordError(f"Invalid JSON number {token}")
                ),
            )
        except (json.JSONDecodeError, RecordError) as error:
            raise RecordError(
                f"Invalid case manifest line {line_number}: {error}"
            ) from error
        if line != canonical_json(value):
            raise RecordError(f"Case manifest line {line_number} is not canonical JSON")
        case = _load_case(value, asset_root=root, verify_local=verify_local)
        if case.case_id in seen:
            raise RecordError(f"Duplicate case_id: {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


@dataclass(frozen=True, slots=True)
class PrimaryMetric:
    name: str
    direction: Literal["minimize", "maximize"]
    minimum_effect: float
    confidence: float
    definition: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class MetricLimit:
    name: str
    minimum: float | None
    maximum: float | None
    definition: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SlicePolicy:
    minimum_cases: int
    maximum_regression: float


@dataclass(frozen=True, slots=True)
class JudgeMetric:
    metric: str
    judge: ResolvedJudge
    definition: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class DownstreamProbe:
    id: str
    definition: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_completed_cases: int
    paired_bootstrap_samples: int
    seed: int
    require_all_hard_limits: bool
    require_all_downstream_probes: bool


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    schema_version: int
    id: str
    station: str
    mission: str
    case_manifest: str
    cases: tuple[EvaluationCase, ...]
    primary_metrics: tuple[PrimaryMetric, ...]
    hard_limits: tuple[MetricLimit, ...]
    protected_slices: tuple[str, ...]
    slice_policy: SlicePolicy
    operational_limits: tuple[MetricLimit, ...]
    judges: tuple[JudgeMetric, ...]
    downstream_probes: tuple[DownstreamProbe, ...]
    promotion: PromotionPolicy
    fingerprint: str
    qualification_eligible: bool = False

    @property
    def can_auto_qualify(self) -> bool:
        return self.qualification_eligible and all(
            binding.judge.can_auto_qualify for binding in self.judges
        )


def _definition_identity(name: str, definition: object) -> str:
    fingerprint = getattr(definition, "fingerprint", None)
    return fingerprint if isinstance(fingerprint, str) and fingerprint else name


def _load_metric_limit(
    name: str,
    value: object,
    *,
    field_name: str,
    metric_resolver: object,
) -> MetricLimit:
    safe_name = _record_id(name, field=f"{field_name} key")
    record = _strict_mapping(
        value,
        field=f"{field_name}.{safe_name}",
        required=set(),
        optional={"minimum", "maximum"},
    )
    if set(record) not in ({"minimum"}, {"maximum"}):
        raise RecordError(f"{field_name}.{safe_name} must declare exactly one bound")
    definition = _resolve_registered(safe_name, metric_resolver, kind="metric")
    minimum = (
        _number(record["minimum"], field=f"{field_name}.{safe_name}.minimum")
        if "minimum" in record
        else None
    )
    maximum = (
        _number(record["maximum"], field=f"{field_name}.{safe_name}.maximum")
        if "maximum" in record
        else None
    )
    return MetricLimit(safe_name, minimum, maximum, definition)


def _infer_evaluation_root(suite_path: Path) -> Path:
    for parent in suite_path.parents:
        if parent.name == "suites":
            return parent.parent
    return suite_path.parent


def load_suite(
    path: str | Path,
    *,
    metric_resolver: object,
    probe_resolver: object,
    judge_resolver: object,
    cases_root: str | Path | None = None,
    asset_root: str | Path | None = None,
    verify_local: bool = True,
) -> EvaluationSuite:
    """Resolve a suite and all of its cases before any benchmark execution."""
    suite_path = Path(path)
    record = _strict_mapping(
        _load_yaml(suite_path),
        field="suite",
        required={
            "schema_version",
            "id",
            "station",
            "mission",
            "case_manifest",
            "primary_metrics",
            "hard_limits",
            "protected_slices",
            "slice_policy",
            "operational_limits",
            "judges",
            "downstream_probes",
            "promotion",
        },
        optional={"qualification_eligible"},
    )
    schema_version = _schema_version(record["schema_version"])
    suite_id = _record_id(record["id"], field="suite.id")
    station = _record_id(record["station"], field="suite.station")
    if not suite_id.startswith(f"{station}/"):
        raise RecordError("suite.id must be namespaced by suite.station")
    mission = _string(record["mission"], field="suite.mission")
    qualification_eligible = record.get("qualification_eligible", False)
    if type(qualification_eligible) is not bool:
        raise RecordError("suite.qualification_eligible must be a boolean")
    evaluation_root = _infer_evaluation_root(suite_path)
    manifest_base = (
        Path(cases_root) if cases_root is not None else evaluation_root / "cases"
    )
    case_manifest, manifest_path = _local_path(
        record["case_manifest"], root=manifest_base, field="suite.case_manifest"
    )
    data_root = Path(asset_root) if asset_root is not None else evaluation_root
    cases = load_case_manifest(
        manifest_path, asset_root=data_root, verify_local=verify_local
    )

    if not isinstance(record["primary_metrics"], dict) or not record["primary_metrics"]:
        raise RecordError("suite.primary_metrics must be a non-empty mapping")
    primary_metrics: list[PrimaryMetric] = []
    for name, value in sorted(record["primary_metrics"].items()):
        safe_name = _record_id(name, field="suite.primary_metrics key")
        spec = _strict_mapping(
            value,
            field=f"suite.primary_metrics.{safe_name}",
            required={"direction", "minimum_effect", "confidence"},
        )
        direction = spec["direction"]
        if direction not in {"minimize", "maximize"}:
            raise RecordError(f"Undefined metric direction for {safe_name!r}")
        minimum_effect = _number(
            spec["minimum_effect"],
            field=f"suite.primary_metrics.{safe_name}.minimum_effect",
            minimum=0.0,
        )
        confidence = _number(
            spec["confidence"], field=f"suite.primary_metrics.{safe_name}.confidence"
        )
        if not 0.0 < confidence < 1.0:
            raise RecordError(
                f"Confidence for {safe_name!r} must be between zero and one"
            )
        definition = _resolve_registered(safe_name, metric_resolver, kind="metric")
        primary_metrics.append(
            PrimaryMetric(safe_name, direction, minimum_effect, confidence, definition)
        )

    for field_name in ("hard_limits", "operational_limits"):
        if not isinstance(record[field_name], dict):
            raise RecordError(f"suite.{field_name} must be a mapping")
    hard_limits = tuple(
        _load_metric_limit(
            name,
            value,
            field_name="suite.hard_limits",
            metric_resolver=metric_resolver,
        )
        for name, value in sorted(record["hard_limits"].items())
    )
    operational_limits = tuple(
        _load_metric_limit(
            name,
            value,
            field_name="suite.operational_limits",
            metric_resolver=metric_resolver,
        )
        for name, value in sorted(record["operational_limits"].items())
    )
    protected_slices = _unique_strings(
        record["protected_slices"], field="suite.protected_slices"
    )
    slice_record = _strict_mapping(
        record["slice_policy"],
        field="suite.slice_policy",
        required={"minimum_cases", "maximum_regression"},
    )
    slice_policy = SlicePolicy(
        minimum_cases=_positive_integer(
            slice_record["minimum_cases"], field="suite.slice_policy.minimum_cases"
        ),
        maximum_regression=_number(
            slice_record["maximum_regression"],
            field="suite.slice_policy.maximum_regression",
            minimum=0.0,
        ),
    )
    slice_counts = {
        slice_name: sum(slice_name in case.strata for case in cases)
        for slice_name in protected_slices
    }
    undersized = {
        name: count
        for name, count in slice_counts.items()
        if count < slice_policy.minimum_cases
    }
    if undersized:
        raise RecordError(
            "Protected slices do not meet minimum_cases: "
            + ", ".join(f"{name}={count}" for name, count in sorted(undersized.items()))
        )

    if not isinstance(record["judges"], list):
        raise RecordError("suite.judges must be a list")
    judges: list[JudgeMetric] = []
    for index, value in enumerate(record["judges"]):
        binding = _strict_mapping(
            value,
            field=f"suite.judges[{index}]",
            required={"metric", "judge"},
        )
        metric_name = _record_id(
            binding["metric"], field=f"suite.judges[{index}].metric"
        )
        definition = _resolve_registered(metric_name, metric_resolver, kind="metric")
        judge_name = _record_id(binding["judge"], field=f"suite.judges[{index}].judge")
        judge = _resolve_registered(judge_name, judge_resolver, kind="judge")
        if not isinstance(judge, ResolvedJudge) or judge.id != judge_name:
            raise RecordError(
                f"Judge resolver returned an invalid judge for {judge_name!r}"
            )
        judges.append(JudgeMetric(metric_name, judge, definition))

    if not isinstance(record["downstream_probes"], list):
        raise RecordError("suite.downstream_probes must be a list")
    probes: list[DownstreamProbe] = []
    for index, value in enumerate(record["downstream_probes"]):
        probe_record = _strict_mapping(
            value,
            field=f"suite.downstream_probes[{index}]",
            required={"id"},
        )
        probe_id = _record_id(
            probe_record["id"], field=f"suite.downstream_probes[{index}].id"
        )
        definition = _resolve_registered(
            probe_id, probe_resolver, kind="downstream probe"
        )
        probes.append(DownstreamProbe(probe_id, definition))

    promotion_record = _strict_mapping(
        record["promotion"],
        field="suite.promotion",
        required={
            "minimum_completed_cases",
            "paired_bootstrap_samples",
            "seed",
            "require_all_hard_limits",
            "require_all_downstream_probes",
        },
    )
    seed = promotion_record["seed"]
    if type(seed) is not int:
        raise RecordError("suite.promotion.seed must be an integer")
    require_hard = promotion_record["require_all_hard_limits"]
    require_probes = promotion_record["require_all_downstream_probes"]
    if type(require_hard) is not bool or type(require_probes) is not bool:
        raise RecordError("suite promotion requirements must be booleans")
    promotion = PromotionPolicy(
        minimum_completed_cases=_positive_integer(
            promotion_record["minimum_completed_cases"],
            field="suite.promotion.minimum_completed_cases",
        ),
        paired_bootstrap_samples=_positive_integer(
            promotion_record["paired_bootstrap_samples"],
            field="suite.promotion.paired_bootstrap_samples",
        ),
        seed=seed,
        require_all_hard_limits=require_hard,
        require_all_downstream_probes=require_probes,
    )
    if promotion.minimum_completed_cases > len(cases):
        raise RecordError(
            "suite.promotion.minimum_completed_cases exceeds manifest case count"
        )

    identity = {
        "schema_version": schema_version,
        "id": suite_id,
        "station": station,
        "mission": mission,
        "qualification_eligible": qualification_eligible,
        "case_manifest": case_manifest,
        "cases": [case.fingerprint for case in cases],
        "primary_metrics": [
            {
                "name": metric.name,
                "direction": metric.direction,
                "minimum_effect": metric.minimum_effect,
                "confidence": metric.confidence,
                "definition": _definition_identity(metric.name, metric.definition),
            }
            for metric in primary_metrics
        ],
        "hard_limits": [
            {
                "name": limit.name,
                "minimum": limit.minimum,
                "maximum": limit.maximum,
                "definition": _definition_identity(limit.name, limit.definition),
            }
            for limit in hard_limits
        ],
        "protected_slices": protected_slices,
        "slice_policy": {
            "minimum_cases": slice_policy.minimum_cases,
            "maximum_regression": slice_policy.maximum_regression,
        },
        "operational_limits": [
            {
                "name": limit.name,
                "minimum": limit.minimum,
                "maximum": limit.maximum,
                "definition": _definition_identity(limit.name, limit.definition),
            }
            for limit in operational_limits
        ],
        "judges": [
            {
                "metric": binding.metric,
                "metric_definition": _definition_identity(
                    binding.metric, binding.definition
                ),
                "judge": binding.judge.id,
                "judge_fingerprint": binding.judge.fingerprint,
            }
            for binding in judges
        ],
        "downstream_probes": [
            {
                "id": probe.id,
                "definition": _definition_identity(probe.id, probe.definition),
            }
            for probe in probes
        ],
        "promotion": {
            "minimum_completed_cases": promotion.minimum_completed_cases,
            "paired_bootstrap_samples": promotion.paired_bootstrap_samples,
            "seed": promotion.seed,
            "require_all_hard_limits": promotion.require_all_hard_limits,
            "require_all_downstream_probes": promotion.require_all_downstream_probes,
        },
    }
    return EvaluationSuite(
        schema_version=schema_version,
        id=suite_id,
        station=station,
        mission=mission,
        case_manifest=case_manifest,
        cases=cases,
        primary_metrics=tuple(primary_metrics),
        hard_limits=hard_limits,
        protected_slices=protected_slices,
        slice_policy=slice_policy,
        operational_limits=operational_limits,
        judges=tuple(judges),
        downstream_probes=tuple(probes),
        promotion=promotion,
        fingerprint=content_fingerprint(identity),
        qualification_eligible=qualification_eligible,
    )


def validate_candidate_suite(
    candidate: ResolvedCandidate, suite: EvaluationSuite
) -> None:
    """Reject station, socket, and execution-shape mismatch before runner work."""
    if candidate.station != suite.station:
        raise RecordError(
            f"Candidate station {candidate.station!r} does not match suite station {suite.station!r}"
        )
    required = set(candidate.consumes) - {"page_list"}
    optional = set(candidate.optional_consumes) - {"page_list"}
    allowed = required | optional
    for case in suite.cases:
        if candidate.grain == "page" and case.page_id is None:
            raise RecordError(
                f"Page-grain candidate requires page_id in case {case.case_id!r}"
            )
        if candidate.grain == "manuscript" and case.page_id is not None:
            raise RecordError(
                f"Manuscript-grain candidate forbids page_id in case {case.case_id!r}"
            )
        input_kinds = set(case.inputs)
        missing = required - input_kinds
        undeclared = input_kinds - allowed
        if missing:
            raise RecordError(
                f"Case {case.case_id!r} is missing required inputs: {sorted(missing)}"
            )
        if undeclared:
            raise RecordError(
                f"Case {case.case_id!r} has undeclared inputs: {sorted(undeclared)}"
            )
        page_ids = {page["page_id"] for page in case.pages}
        for kind, asset in case.inputs.items():
            artifact_grain = contract(kind).grain
            is_page_map = isinstance(asset, Mapping)
            expects_page_map = (
                candidate.grain == "manuscript" and artifact_grain == "page"
            )
            if expects_page_map != is_page_map:
                expected = (
                    "page_id-to-asset mapping" if expects_page_map else "single asset"
                )
                raise RecordError(
                    f"Case {case.case_id!r} input {kind!r} must be a {expected}"
                )
            if is_page_map and set(asset) != page_ids:
                raise RecordError(
                    f"Case {case.case_id!r} input {kind!r} must cover every case page exactly"
                )
