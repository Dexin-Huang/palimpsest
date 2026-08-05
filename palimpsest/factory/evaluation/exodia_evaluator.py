"""Artifact-bound Exodia adapter for one visible Palimpsest evaluation case.

Beside the scalar metrics, the response record carries two research-loop keys:

- ``constraints``: one ``"pass"``/``"fail"`` verdict per suite-declared hard
  limit, computed from this candidate's own per-case metric values against
  the declared bound; a metric missing from the per-case values is a
  conservative ``"fail"``, and no declared limit is ever omitted.
- ``asi``: deterministic diagnostic side-information with exactly these
  fields: ``submission_status`` (``"completed"`` | ``"baseline-failed"`` |
  ``"challenger-failed"``), ``dominant_failure`` (``"baseline"`` |
  ``"challenger"`` | ``"hard-limit"`` | ``"unknown-hard-limit"`` |
  ``"unknown-cost"`` | ``"missing-primary-metric"`` | ``None`` — the evidence
  kind that decided ``failureClass``), ``hard_limit_values`` (metric -> float
  for the values actually compared), ``metric_values`` (metric -> float for
  every per-case candidate value), ``case_id`` (str), ``strata`` (sorted list
  of str), ``baseline_succeeded`` (bool), ``challenger_succeeded`` (bool),
  and ``unknown_cost`` (bool). When the report's challenger side carries
  process telemetry, ``asi`` additionally holds ``process_stats`` (object
  with the non-negative integers ``assistant_turns``, ``tool_calls``, and
  ``output_tokens``); when absent from the report it is absent here — never
  fabricated. No wall-clock timestamps, no absolute paths, no NaN/Infinity.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from palimpsest.factory.core.artifact import content_fingerprint
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.inline_extension import (
    OMP_EXTENSION_MEDIA_TYPE,
    render_candidate,
)
from palimpsest.factory.evaluation.metrics import MetricRegistry
from palimpsest.factory.evaluation.runner import run_evaluation
from palimpsest.factory.evaluation.station_metrics import register_station_metrics
from palimpsest.factory.evaluation.station_metrics.read import (
    character_error_structure,
)
from palimpsest.factory.evaluation.store import EvaluationStore
from palimpsest.factory.evaluation.suite import CaseAsset, MetricLimit, load_suite

_PROTOCOL_VERSION = 2
_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_SAFE_INTEGER = (1 << 53) - 1
_SHA256 = "sha256:"
_HARNESS_TOOL_REQUIREMENT_PREFIX = "harness.tool."
_SUITE_ARTIFACT_ID = "palimpsest-suite"
_BASELINE_EXTENSION_ID = "harness-baseline"
_SUITE_MEDIA_TYPE = "application/yaml"
_OUTPUT_KEYS = {
    "metrics",
    "cost",
    "latencyMs",
    "failureClass",
    "evidenceIds",
    "traceArtifactRefs",
    "evaluatorArtifactRefs",
    "constraints",
    "asi",
}


class EvaluationIntegrityError(ValueError):
    """The request or visible benchmark evidence is internally inconsistent."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in result:
            raise EvaluationIntegrityError(f"Duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=False)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _object(
    value: object,
    *,
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise EvaluationIntegrityError(f"{field} must be an object")
    record = value
    if any(type(key) is not str for key in record):
        raise EvaluationIntegrityError(f"{field} keys must be strings")
    allowed = required | (optional or set())
    unknown = set(record) - allowed
    missing = required - set(record)
    if unknown:
        raise EvaluationIntegrityError(
            f"{field} contains unsupported fields: {sorted(unknown)}"
        )
    if missing:
        raise EvaluationIntegrityError(f"{field} is missing fields: {sorted(missing)}")
    return record


def _string(value: object, *, field: str) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise EvaluationIntegrityError(f"{field} must be a non-empty string")
    return value


def _sha256(value: object, *, field: str) -> str:
    digest = _string(value, field=field)
    if len(digest) != 71 or not digest.startswith(_SHA256):
        raise EvaluationIntegrityError(f"{field} must be a canonical SHA-256 digest")
    try:
        int(digest[len(_SHA256) :], 16)
    except ValueError as error:
        raise EvaluationIntegrityError(
            f"{field} must be a canonical SHA-256 digest"
        ) from error
    if digest != digest.lower():
        raise EvaluationIntegrityError(f"{field} must be a canonical SHA-256 digest")
    return digest


def _safe_integer(value: object, *, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER:
        raise EvaluationIntegrityError(
            f"{field} must be a non-negative JSON safe integer"
        )
    return value


def _process_stats(value: object, *, field: str) -> dict[str, int]:
    record = _object(
        value,
        field=field,
        required={"assistant_turns", "tool_calls"},
        optional={"output_tokens"},
    )
    return {
        key: _safe_integer(record[key], field=f"{field}.{key}")
        for key in sorted(record)
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_embedded_json(value: object, *, field: str) -> dict[str, Any]:
    serialized = _string(value, field=field)
    try:
        parsed = json.loads(
            serialized,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                EvaluationIntegrityError(f"Invalid JSON number {token}")
            ),
        )
    except (json.JSONDecodeError, UnicodeError) as error:
        raise EvaluationIntegrityError(f"{field} must be valid JSON") from error
    record = _object(
        parsed,
        field=field,
        required=set(parsed) if type(parsed) is dict else set(),
    )
    if _canonical_json(record) != serialized:
        raise EvaluationIntegrityError(f"{field} must use canonical JSON")
    return record


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationIntegrityError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _validate_candidate(
    candidate_value: object,
    harness_value: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = _object(
        candidate_value,
        field="request.candidate",
        required={
            "ref",
            "specialty",
            "modelRef",
            "harnessConfig",
            "harnessDigest",
            "runtimeRequirements",
            "lineage",
            "createdBy",
            "createdAt",
        },
    )
    ref = _object(
        candidate["ref"],
        field="request.candidate.ref",
        required={"id", "version", "digest"},
    )
    _string(ref["id"], field="request.candidate.ref.id")
    _string(ref["version"], field="request.candidate.ref.version")
    _sha256(ref["digest"], field="request.candidate.ref.digest")
    _string(candidate["specialty"], field="request.candidate.specialty")
    model_ref = _string(candidate["modelRef"], field="request.candidate.modelRef")

    embedded_harness = _object(
        _load_embedded_json(
            candidate["harnessConfig"], field="request.candidate.harnessConfig"
        ),
        field="request.candidate.harnessConfig",
        required={"extensionBundle"},
    )
    extension_bundle = _string(
        embedded_harness["extensionBundle"],
        field="request.candidate.harnessConfig.extensionBundle",
    )
    expected_harness_digest = (
        _SHA256
        + hashlib.sha256(
            b"@exodia/harness-config:v1\0" + candidate["harnessConfig"].encode("utf-8")
        ).hexdigest()
    )
    if (
        _sha256(candidate["harnessDigest"], field="request.candidate.harnessDigest")
        != expected_harness_digest
    ):
        raise EvaluationIntegrityError("request.candidate harnessDigest mismatch")

    requirements = _object(
        candidate["runtimeRequirements"],
        field="request.candidate.runtimeRequirements",
        required=set(candidate["runtimeRequirements"])
        if type(candidate["runtimeRequirements"]) is dict
        else set(),
    )
    required_tool_ids: list[str] = []
    for key, requirement in requirements.items():
        requirement_key = _string(
            key, field="request.candidate.runtimeRequirements key"
        )
        requirement_value = _string(
            requirement, field=f"request.candidate.runtimeRequirements.{key}"
        )
        if requirement_key.startswith(_HARNESS_TOOL_REQUIREMENT_PREFIX):
            tool_id = requirement_key.removeprefix(_HARNESS_TOOL_REQUIREMENT_PREFIX)
            if not tool_id:
                raise EvaluationIntegrityError(
                    "request.candidate runtime tool requirement has an empty id"
                )
            if requirement_value != "required":
                raise EvaluationIntegrityError(
                    "request.candidate runtime tool requirements must equal 'required'"
                )
            required_tool_ids.append(tool_id)
    required_tool_ids.sort()
    if len(required_tool_ids) != len(set(required_tool_ids)):
        raise EvaluationIntegrityError(
            "request.candidate runtime tool requirements repeat a tool id"
        )

    harness = _object(
        harness_value,
        field="request.harness",
        required={"extensionBundle", "model", "variant"},
        optional={"toolBindings"},
    )
    if (
        _string(harness["extensionBundle"], field="request.harness.extensionBundle")
        != extension_bundle
    ):
        raise EvaluationIntegrityError(
            "request.harness.extensionBundle does not match CandidateRig"
        )
    if _string(harness["model"], field="request.harness.model") != model_ref:
        raise EvaluationIntegrityError(
            "request.harness.model does not match CandidateRig modelRef"
        )
    _string(harness["variant"], field="request.harness.variant")

    bindings = harness.get("toolBindings")
    bound_tool_ids: list[str] = []
    if bindings is not None:
        if type(bindings) is not list or not bindings:
            raise EvaluationIntegrityError(
                "request.harness.toolBindings must be a non-empty array"
            )
        previous: str | None = None
        for index, binding_value in enumerate(bindings):
            field = f"request.harness.toolBindings[{index}]"
            binding = _object(
                binding_value,
                field=field,
                required={"id", "kind", "model"},
            )
            tool_id = _string(binding["id"], field=f"{field}.id")
            if previous is not None and previous >= tool_id:
                raise EvaluationIntegrityError(
                    "request.harness.toolBindings must be sorted and unique by id"
                )
            if _string(binding["kind"], field=f"{field}.kind") != "draft_model":
                raise EvaluationIntegrityError(
                    f"{field}.kind is not supported by the Palimpsest adapter"
                )
            _string(binding["model"], field=f"{field}.model")
            previous = tool_id
            bound_tool_ids.append(tool_id)
    if bound_tool_ids != required_tool_ids:
        raise EvaluationIntegrityError(
            "request.harness.toolBindings do not match CandidateRig runtime requirements"
        )

    lineage = _object(
        candidate["lineage"],
        field="request.candidate.lineage",
        required={"id", "derivation", "sourceRevisions", "changeArtifactRefs"},
        optional={"parent"},
    )
    _string(lineage["id"], field="request.candidate.lineage.id")
    _string(lineage["derivation"], field="request.candidate.lineage.derivation")
    for name in ("sourceRevisions", "changeArtifactRefs"):
        values = lineage[name]
        if type(values) is not list:
            raise EvaluationIntegrityError(
                f"request.candidate.lineage.{name} must be an array"
            )
        for index, item in enumerate(values):
            _string(item, field=f"request.candidate.lineage.{name}[{index}]")
    if "parent" in lineage:
        parent = _object(
            lineage["parent"],
            field="request.candidate.lineage.parent",
            required={"id", "version", "digest"},
        )
        _string(parent["id"], field="request.candidate.lineage.parent.id")
        _string(parent["version"], field="request.candidate.lineage.parent.version")
        _sha256(parent["digest"], field="request.candidate.lineage.parent.digest")
    _string(candidate["createdBy"], field="request.candidate.createdBy")
    _string(candidate["createdAt"], field="request.candidate.createdAt")
    return candidate, harness


def _validate_case(value: object) -> dict[str, Any]:
    case = _object(
        value,
        field="request.case",
        required={
            "id",
            "inputArtifactRef",
            "strata",
            "sourceId",
            "sourceRevision",
            "semanticFamilyId",
        },
        optional={"goldArtifactRef", "adjudicationEvidenceId"},
    )
    for name in (
        "id",
        "inputArtifactRef",
        "sourceId",
        "sourceRevision",
        "semanticFamilyId",
    ):
        _string(case[name], field=f"request.case.{name}")
    if "goldArtifactRef" in case:
        _string(case["goldArtifactRef"], field="request.case.goldArtifactRef")
    if "adjudicationEvidenceId" in case:
        _string(
            case["adjudicationEvidenceId"],
            field="request.case.adjudicationEvidenceId",
        )
    strata = case["strata"]
    if type(strata) is not list or not strata:
        raise EvaluationIntegrityError("request.case.strata must be a non-empty array")
    validated = [
        _string(item, field=f"request.case.strata[{index}]")
        for index, item in enumerate(strata)
    ]
    if len(set(validated)) != len(validated):
        raise EvaluationIntegrityError(
            "request.case.strata must not contain duplicates"
        )
    return case


def _validate_context(value: object) -> dict[str, Any]:
    context = _object(
        value,
        field="request.context",
        required={"runId", "suiteId", "suiteVersion", "caseIndex", "evaluationIndex"},
    )
    for name in ("runId", "suiteId", "suiteVersion"):
        _string(context[name], field=f"request.context.{name}")
    for name in ("caseIndex", "evaluationIndex"):
        _safe_integer(context[name], field=f"request.context.{name}")
    return context


def _verified_artifacts(value: object) -> dict[str, dict[str, Any]]:
    if type(value) is not list or not value:
        raise EvaluationIntegrityError("request.artifacts must be a non-empty array")
    artifacts: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value):
        field = f"request.artifacts[{index}]"
        artifact = _object(
            item,
            field=field,
            required={"id", "path", "revision", "sha256", "sizeBytes", "mediaType"},
        )
        artifact_id = _string(artifact["id"], field=f"{field}.id")
        if artifact_id in artifacts:
            raise EvaluationIntegrityError(f"Duplicate artifact id: {artifact_id!r}")
        path_text = _string(artifact["path"], field=f"{field}.path")
        path = Path(path_text)
        if not path.is_absolute() or path.is_symlink():
            raise EvaluationIntegrityError(
                f"{field}.path must be an absolute regular file"
            )
        try:
            file_stat = path.stat()
        except OSError as error:
            raise EvaluationIntegrityError(f"{field}.path is unavailable") from error
        if not path.is_file():
            raise EvaluationIntegrityError(f"{field}.path must be a regular file")
        size = _safe_integer(artifact["sizeBytes"], field=f"{field}.sizeBytes")
        if file_stat.st_size != size:
            raise EvaluationIntegrityError(f"{field}.sizeBytes does not match the file")
        expected = _sha256(artifact["sha256"], field=f"{field}.sha256")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as error:
            raise EvaluationIntegrityError(f"Cannot verify {field}.path") from error
        actual = _SHA256 + digest.hexdigest()
        if actual != expected:
            raise EvaluationIntegrityError(f"{field}.sha256 does not match the file")
        _string(artifact["revision"], field=f"{field}.revision")
        _string(artifact["mediaType"], field=f"{field}.mediaType")
        artifact["path"] = path
        artifacts[artifact_id] = artifact
    return artifacts


def _parse_request(
    value: object,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    request = _object(
        value,
        field="request",
        required={
            "protocol_version",
            "candidate",
            "harness",
            "case",
            "context",
            "artifacts",
        },
    )
    if (
        type(request["protocol_version"]) is not int
        or request["protocol_version"] != _PROTOCOL_VERSION
    ):
        raise EvaluationIntegrityError(
            f"request.protocol_version must be integer {_PROTOCOL_VERSION}"
        )
    candidate, harness = _validate_candidate(request["candidate"], request["harness"])
    case = _validate_case(request["case"])
    context = _validate_context(request["context"])
    artifacts = _verified_artifacts(request["artifacts"])
    return candidate, harness, case, context, artifacts


def _artifact(
    artifacts: Mapping[str, dict[str, Any]],
    artifact_id: str,
    *,
    role: str,
    media_type: str | None = None,
) -> dict[str, Any]:
    try:
        artifact = artifacts[artifact_id]
    except KeyError as error:
        raise EvaluationIntegrityError(
            f"Missing {role} artifact handle {artifact_id!r}"
        ) from error
    if media_type is not None and artifact["mediaType"] != media_type:
        raise EvaluationIntegrityError(
            f"{role} artifact {artifact_id!r} must use media type {media_type!r}"
        )
    return artifact


def _verified_artifact_bytes(artifact: Mapping[str, Any], *, role: str) -> bytes:
    try:
        content = artifact["path"].read_bytes()
    except OSError as error:
        raise EvaluationIntegrityError(f"{role} artifact became unavailable") from error
    actual = _SHA256 + hashlib.sha256(content).hexdigest()
    if len(content) != artifact["sizeBytes"] or actual != artifact["sha256"]:
        raise EvaluationIntegrityError(f"{role} artifact changed after verification")
    return content


def _suite_manifest_id(source: bytes) -> str:
    try:
        value = yaml.load(source.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (TypeError, UnicodeError, yaml.YAMLError) as error:
        raise EvaluationIntegrityError(
            "Palimpsest suite artifact is not valid UTF-8 YAML"
        ) from error
    suite = _object(
        value,
        field="Palimpsest suite",
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
    manifest_id = _string(
        suite["case_manifest"], field="Palimpsest suite.case_manifest"
    )
    path = PurePosixPath(manifest_id)
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in manifest_id
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise EvaluationIntegrityError(
            "Palimpsest suite.case_manifest must be a safe relative artifact id"
        )
    return manifest_id


def _single_case_asset(
    values: Mapping[str, CaseAsset | Mapping[str, CaseAsset]], *, role: str
) -> CaseAsset:
    if len(values) != 1:
        raise EvaluationIntegrityError(
            f"Selected Palimpsest case must declare exactly one {role} asset"
        )
    value = next(iter(values.values()))
    if isinstance(value, Mapping):
        raise EvaluationIntegrityError(
            f"Selected Palimpsest case {role} must be a single page asset"
        )
    return value


def _asset_path_map(
    selected: Any,
    case: Mapping[str, Any],
    artifacts: Mapping[str, dict[str, Any]],
) -> dict[str, Path]:
    input_handle = _artifact(
        artifacts,
        case["inputArtifactRef"],
        role="case input",
    )
    input_asset = _single_case_asset(selected.inputs, role="input")
    if input_asset.sha256 != input_handle["sha256"][len(_SHA256) :]:
        raise EvaluationIntegrityError(
            "CapabilitySuiteCase input artifact digest does not match the Palimpsest case"
        )
    gold_id = case.get("goldArtifactRef")
    if gold_id is None:
        raise EvaluationIntegrityError(
            "The selected Palimpsest scoring case requires goldArtifactRef"
        )
    gold_handle = _artifact(artifacts, gold_id, role="case gold")
    gold_asset = _single_case_asset(selected.references, role="gold")
    if gold_asset.sha256 != gold_handle["sha256"][len(_SHA256) :]:
        raise EvaluationIntegrityError(
            "CapabilitySuiteCase gold artifact digest does not match the Palimpsest case"
        )
    return {
        input_asset.sha256: input_handle["path"],
        gold_asset.sha256: gold_handle["path"],
    }


def _artifact_ref(artifact: Mapping[str, Any]) -> str:
    return f"benchmark-artifact:{artifact['id']}@{artifact['sha256']}"


def _failure_evidence(kind: str, message: object) -> str:
    encoded = _canonical_json({"kind": kind, "message": str(message)}).encode("utf-8")
    return f"palimpsest-failure:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _number(value: object, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationIntegrityError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise EvaluationIntegrityError(f"{field} is outside its valid range")
    return result


def _hard_limit_constraints(
    hard_limits: Iterable[MetricLimit], values: Mapping[str, float]
) -> dict[str, str]:
    """Per-case verdict for every suite-declared hard limit.

    A metric missing from this candidate's per-case values is a conservative
    ``"fail"``; no declared limit is ever omitted.
    """
    verdicts: dict[str, str] = {}
    for binding in hard_limits:
        value = values.get(binding.name)
        passed = (
            value is not None
            and math.isfinite(value)
            and (binding.minimum is None or value >= binding.minimum)
            and (binding.maximum is None or value <= binding.maximum)
        )
        verdicts[binding.name] = "pass" if passed else "fail"
    return dict(sorted(verdicts.items()))


def _validate_asi(value: object, *, field: str) -> None:
    """Reject non-finite or non-plain side-information before it is emitted."""
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise AssertionError(f"internal asi value {field} is not finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise AssertionError(f"internal asi key in {field} is not a string")
            _validate_asi(item, field=f"{field}.{key}")
        return
    if type(value) in (list, tuple):
        for index, item in enumerate(value):
            _validate_asi(item, field=f"{field}[{index}]")
        return
    raise AssertionError(f"internal asi value {field} is not plain JSON")


def _challenger_error_structure(
    report: Mapping[str, Any],
    *,
    case_id: str,
    gold_artifact: Mapping[str, Any],
    runs_root: Path,
) -> dict[str, object] | None:
    """Gold-alignment diagnostics for ``asi``; never fails the observation.

    Returns ``None`` when the case has no readable challenger transcription or
    gold text. Unexpected internal failures return a bounded ``unavailable``
    marker instead of raising, because diagnostics must not change the
    evaluation outcome.
    """

    try:
        cases = report.get("cases")
        if not isinstance(cases, list):
            return None
        matches = [
            entry
            for entry in cases
            if isinstance(entry, Mapping) and entry.get("case_id") == case_id
        ]
        if len(matches) != 1:
            return None
        challenger = matches[0].get("challenger")
        if (
            not isinstance(challenger, Mapping)
            or challenger.get("succeeded") is not True
        ):
            return None
        output_path_value = challenger.get("output_path")
        output_fingerprint = challenger.get("output_fingerprint")
        if type(output_path_value) is not str or type(output_fingerprint) is not str:
            return None
        output_path = Path(output_path_value).resolve()
        if not output_path.is_relative_to(runs_root.resolve()):
            return {"unavailable": "challenger output escaped the evaluation run"}
        if content_fingerprint(output_path) != output_fingerprint:
            return {"unavailable": "challenger output fingerprint drifted"}
        try:
            output_record = json.loads(output_path.read_text(encoding="utf-8"))
            gold_record = json.loads(
                _verified_artifact_bytes(
                    gold_artifact, role="gold transcription"
                ).decode("utf-8")
            )
        except (ValueError, UnicodeError):
            return None
        if type(output_record) is not dict or type(gold_record) is not dict:
            return None
        candidate_text = output_record.get("text")
        gold_text = gold_record.get("text")
        if type(candidate_text) is not str or type(gold_text) is not str:
            return None
        if not gold_text.strip():
            return None
        return character_error_structure(candidate_text, gold_text)
    except Exception as error:  # noqa: BLE001 - diagnostics never fail evaluation
        return {"unavailable": str(error)[:200]}


def _retain_outputs(
    report: Mapping[str, Any],
    *,
    case_id: str,
    candidate_ref: object,
    runs_root: Path,
) -> None:
    """Copy fingerprint-verified transcription outputs for human review.

    Active only when ``PALIMPSEST_RETAIN_OUTPUTS`` names a directory. Retained
    copies are review evidence, not evaluation evidence: every failure prints
    to stderr and never changes the observation. Files are append-only under
    ``<dir>/<case-slug>/<side>-<nanoseconds>.json``.
    """

    target_value = os.environ.get("PALIMPSEST_RETAIN_OUTPUTS", "").strip()
    if not target_value:
        return
    try:
        cases = report.get("cases")
        if not isinstance(cases, list):
            return
        matches = [
            entry
            for entry in cases
            if isinstance(entry, Mapping) and entry.get("case_id") == case_id
        ]
        if len(matches) != 1:
            return
        case_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", case_id)
        stamp = time.time_ns()
        for side in ("baseline", "challenger"):
            record = matches[0].get(side)
            if not isinstance(record, Mapping) or record.get("succeeded") is not True:
                continue
            output_path_value = record.get("output_path")
            output_fingerprint = record.get("output_fingerprint")
            if (
                type(output_path_value) is not str
                or type(output_fingerprint) is not str
            ):
                continue
            output_path = Path(output_path_value).resolve()
            if not output_path.is_relative_to(runs_root.resolve()):
                continue
            if content_fingerprint(output_path) != output_fingerprint:
                continue
            payload = {
                "schema_version": 1,
                "case_id": case_id,
                "side": side,
                "candidate_ref": candidate_ref if side == "challenger" else None,
                "output_fingerprint": output_fingerprint,
                "record": json.loads(output_path.read_text(encoding="utf-8")),
            }
            destination = Path(target_value) / case_slug
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{side}-{stamp}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as error:  # noqa: BLE001 - review copies never fail evaluation
        print(f"output retention failed: {error}", file=sys.stderr)


def _observation_from_report(
    *,
    report: Mapping[str, Any],
    suite: Any,
    case_id: str,
    strata: tuple[str, ...],
    elapsed_ms: float,
    evaluator_artifacts: list[Mapping[str, Any]],
    error_structure: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    report_cases = report.get("cases")
    if not isinstance(report_cases, list):
        raise EvaluationIntegrityError("Palimpsest report cases are malformed")
    matches = [
        entry
        for entry in report_cases
        if isinstance(entry, Mapping) and entry.get("case_id") == case_id
    ]
    if len(matches) != 1:
        raise EvaluationIntegrityError(
            "Palimpsest report is missing the requested case"
        )
    case_report = matches[0]
    baseline = case_report.get("baseline")
    challenger = case_report.get("challenger")
    if not isinstance(baseline, Mapping) or not isinstance(challenger, Mapping):
        raise EvaluationIntegrityError("Palimpsest report case sides are malformed")
    baseline_succeeded = baseline.get("succeeded")
    challenger_succeeded = challenger.get("succeeded")
    if type(baseline_succeeded) is not bool or type(challenger_succeeded) is not bool:
        raise EvaluationIntegrityError(
            "Palimpsest report case success state is malformed"
        )

    aggregates = report.get("aggregates")
    if not isinstance(aggregates, Mapping):
        raise EvaluationIntegrityError("Palimpsest report aggregates are malformed")
    cost_accounting = aggregates.get("cost_ceiling")
    if not isinstance(cost_accounting, Mapping):
        raise EvaluationIntegrityError("Palimpsest report cost accounting is malformed")
    cost = _number(
        cost_accounting.get("total_known_cost_usd"),
        field="Palimpsest report total_known_cost_usd",
        minimum=0.0,
    )
    unknown_cost = cost_accounting.get("unknown_cost")
    if type(unknown_cost) is not bool:
        raise EvaluationIntegrityError("Palimpsest report unknown_cost is malformed")
    hard_limits = aggregates.get("hard_limits")
    if not isinstance(hard_limits, list):
        raise EvaluationIntegrityError("Palimpsest report hard limits are malformed")
    expected_hard_limits = {binding.name for binding in suite.hard_limits}
    hard_limit_decisions: dict[str, str] = {}
    for index, item in enumerate(hard_limits):
        if not isinstance(item, Mapping) or set(item) != {
            "metric",
            "value",
            "minimum",
            "maximum",
            "decision",
        }:
            raise EvaluationIntegrityError(
                f"Palimpsest report hard_limits[{index}] is malformed"
            )
        metric = _string(
            item["metric"], field=f"Palimpsest report hard_limits[{index}].metric"
        )
        decision = item["decision"]
        if decision not in {"pass", "fail", "unknown"}:
            raise EvaluationIntegrityError(
                f"Palimpsest report hard_limits[{index}].decision is malformed"
            )
        if metric in hard_limit_decisions:
            raise EvaluationIntegrityError(
                f"Palimpsest report repeats hard limit {metric!r}"
            )
        hard_limit_decisions[metric] = decision
    if set(hard_limit_decisions) != expected_hard_limits:
        raise EvaluationIntegrityError(
            "Palimpsest report hard limits do not match the loaded suite"
        )

    report_fingerprint = _sha256(
        _SHA256
        + _string(
            report.get("report_fingerprint"), field="Palimpsest report fingerprint"
        ),
        field="Palimpsest report fingerprint",
    )
    report_ref = f"palimpsest-report:{report_fingerprint}"
    traces = [report_ref]
    for role, side in (("baseline", baseline), ("challenger", challenger)):
        fingerprint = side.get("output_fingerprint")
        if fingerprint is not None:
            fingerprint = _string(
                fingerprint, field=f"Palimpsest report {role} output fingerprint"
            )
            traces.append(f"palimpsest-{role}-output:{fingerprint}")

    evidence = [f"palimpsest-case:{case_id}@{report_fingerprint}"]
    failure_class: str | None = None
    dominant_failure: str | None = None
    if not baseline_succeeded:
        failure_class = "infrastructure"
        dominant_failure = "baseline"
        evidence.append(
            _failure_evidence(
                "baseline",
                {
                    "kind": baseline.get("error_kind"),
                    "message": baseline.get("error_message"),
                },
            )
        )
    elif not challenger_succeeded:
        failure_class = "target"
        dominant_failure = "challenger"
        evidence.append(
            _failure_evidence(
                "challenger",
                {
                    "kind": challenger.get("error_kind"),
                    "message": challenger.get("error_message"),
                },
            )
        )
    elif "fail" in hard_limit_decisions.values():
        failure_class = "target"
        dominant_failure = "hard-limit"
        evidence.append(
            _failure_evidence(
                "hard-limit",
                sorted(
                    metric
                    for metric, decision in hard_limit_decisions.items()
                    if decision == "fail"
                ),
            )
        )
    elif "unknown" in hard_limit_decisions.values():
        failure_class = "evaluation-integrity"
        dominant_failure = "unknown-hard-limit"
        evidence.append(_failure_evidence("unknown-hard-limit", report_fingerprint))
    elif unknown_cost:
        failure_class = "evaluation-integrity"
        dominant_failure = "unknown-cost"
        evidence.append(_failure_evidence("unknown-cost", report_fingerprint))

    if not baseline_succeeded:
        submission_status = "baseline-failed"
    elif not challenger_succeeded:
        submission_status = "challenger-failed"
    else:
        submission_status = "completed"

    values: dict[str, float] = {}
    quality_values: list[float] = []
    if baseline_succeeded and challenger_succeeded:
        observations = case_report.get("observations")
        if not isinstance(observations, Mapping):
            raise EvaluationIntegrityError(
                "Palimpsest report metric observations are malformed"
            )
        primary = {binding.name: binding for binding in suite.primary_metrics}
        for metric_name, observation in observations.items():
            if not isinstance(metric_name, str) or not isinstance(observation, Mapping):
                raise EvaluationIntegrityError(
                    "Palimpsest report metric observation is malformed"
                )
            candidate_value = observation.get("candidate")
            if candidate_value is None:
                continue
            values[metric_name] = _number(
                candidate_value,
                field=f"Palimpsest report metric {metric_name!r}",
            )
        for metric_name, binding in primary.items():
            if metric_name not in values:
                failure_class = "evaluation-integrity"
                dominant_failure = "missing-primary-metric"
                evidence.append(
                    _failure_evidence("missing-primary-metric", metric_name)
                )
                continue
            value = values[metric_name]
            bounded = max(0.0, min(1.0, value))
            quality_values.append(
                bounded if binding.direction == "maximize" else 1.0 - bounded
            )

    quality = (
        sum(quality_values) / len(quality_values)
        if len(quality_values) == len(suite.primary_metrics)
        else 0.0
    )
    constraints = _hard_limit_constraints(suite.hard_limits, values)
    asi: dict[str, object] = {
        "submission_status": submission_status,
        "dominant_failure": dominant_failure,
        "hard_limit_values": dict(
            sorted(
                (binding.name, values[binding.name])
                for binding in suite.hard_limits
                if binding.name in values
            )
        ),
        "metric_values": dict(sorted(values.items())),
        "case_id": case_id,
        "strata": sorted(strata),
        "baseline_succeeded": baseline_succeeded,
        "challenger_succeeded": challenger_succeeded,
        "unknown_cost": unknown_cost,
    }
    if dominant_failure in {"baseline", "challenger"}:
        failed_side = baseline if dominant_failure == "baseline" else challenger
        error_kind = failed_side.get("error_kind")
        error_message = failed_side.get("error_message")
        if error_kind is not None and not isinstance(error_kind, str):
            raise EvaluationIntegrityError(
                f"Palimpsest report {dominant_failure} error_kind is malformed"
            )
        if error_message is not None and not isinstance(error_message, str):
            raise EvaluationIntegrityError(
                f"Palimpsest report {dominant_failure} error_message is malformed"
            )
        asi["failure_detail"] = {
            "side": dominant_failure,
            "kind": None if error_kind is None else error_kind[:128],
            "message": None if error_message is None else error_message[:500],
        }
    challenger_process_stats = challenger.get("process_stats")
    if challenger_process_stats is not None:
        asi["process_stats"] = _process_stats(
            challenger_process_stats,
            field="Palimpsest report challenger process_stats",
        )
    if error_structure is not None:
        asi["error_structure"] = dict(error_structure)
    _validate_asi(asi, field="asi")
    result = {
        "metrics": {"quality": quality, "values": dict(sorted(values.items()))},
        "cost": cost,
        "latencyMs": _number(elapsed_ms, field="evaluation latency", minimum=0.0),
        "failureClass": failure_class,
        "evidenceIds": list(dict.fromkeys(evidence)),
        "traceArtifactRefs": list(dict.fromkeys(traces)),
        "evaluatorArtifactRefs": list(
            dict.fromkeys(_artifact_ref(artifact) for artifact in evaluator_artifacts)
        ),
        "constraints": constraints,
        "asi": asi,
    }
    if set(result) != _OUTPUT_KEYS:
        raise AssertionError("internal observation schema drift")
    return result


def evaluate(request: object) -> dict[str, object]:
    candidate, harness, case, context, artifacts = _parse_request(request)
    suite_artifact = _artifact(
        artifacts,
        _SUITE_ARTIFACT_ID,
        role="Palimpsest suite",
        media_type=_SUITE_MEDIA_TYPE,
    )
    suite_source = _verified_artifact_bytes(suite_artifact, role="Palimpsest suite")
    manifest_id = _suite_manifest_id(suite_source)
    manifest_artifact = _artifact(
        artifacts, manifest_id, role="Palimpsest case manifest"
    )
    baseline_extension = _artifact(
        artifacts,
        _BASELINE_EXTENSION_ID,
        role="baseline extension",
        media_type=OMP_EXTENSION_MEDIA_TYPE,
    )
    challenger_extension = _artifact(
        artifacts,
        harness["extensionBundle"],
        role="candidate extension",
        media_type=OMP_EXTENSION_MEDIA_TYPE,
    )

    with tempfile.TemporaryDirectory(prefix="palimpsest-exodia-") as temporary:
        root = Path(temporary)
        evaluation_root = root / "evaluation"
        suites_root = evaluation_root / "suites"
        cases_root = evaluation_root / "cases"
        suite_path = suites_root / "palimpsest-suite.yaml"
        manifest_path = cases_root.joinpath(*PurePosixPath(manifest_id).parts)
        suite_path.parent.mkdir(parents=True)
        manifest_path.parent.mkdir(parents=True)
        suite_path.write_bytes(suite_source)
        manifest_path.write_bytes(
            _verified_artifact_bytes(manifest_artifact, role="Palimpsest case manifest")
        )

        metrics = MetricRegistry()
        register_station_metrics(metrics)
        try:
            suite = load_suite(
                suite_path,
                metric_resolver=metrics,
                probe_resolver={},
                judge_resolver={},
                cases_root=cases_root,
                asset_root=evaluation_root,
                verify_local=False,
            )
        except Exception as error:
            raise EvaluationIntegrityError(
                "Verified Palimpsest suite or manifest could not be resolved"
            ) from error
        selected = tuple(item for item in suite.cases if item.case_id == case["id"])
        if len(selected) != 1:
            raise EvaluationIntegrityError(
                "Requested CapabilitySuiteCase is missing from the Palimpsest suite"
            )
        selected_case = selected[0]
        if set(case["strata"]) != set(selected_case.strata):
            raise EvaluationIntegrityError(
                "CapabilitySuiteCase strata do not match the Palimpsest case"
            )
        asset_paths = _asset_path_map(selected_case, case, artifacts)

        def resolve_asset(asset: CaseAsset) -> Path:
            try:
                return asset_paths[asset.sha256]
            except KeyError as error:
                raise EvaluationIntegrityError(
                    "Palimpsest requested an undeclared case asset"
                ) from error

        try:
            baseline_rendered = render_candidate(
                _verified_artifact_bytes(baseline_extension, role="baseline extension"),
                role="baseline",
                model=candidate["modelRef"],
                output_dir=root / "rendered-baseline",
            )
            challenger_rendered = render_candidate(
                _verified_artifact_bytes(
                    challenger_extension, role="candidate extension"
                ),
                role="challenger",
                model=candidate["modelRef"],
                output_dir=root / "rendered-challenger",
                tool_bindings=tuple(harness["toolBindings"])
                if "toolBindings" in harness
                else None,
                variant=harness["variant"],
            )
        except (TypeError, ValueError, UnicodeError) as error:
            raise EvaluationIntegrityError(
                "Verified extension bytes could not render a Palimpsest candidate"
            ) from error
        if (
            baseline_rendered.source_sha256
            != baseline_extension["sha256"][len(_SHA256) :]
        ):
            raise EvaluationIntegrityError("Rendered baseline extension digest drifted")
        if (
            challenger_rendered.source_sha256
            != challenger_extension["sha256"][len(_SHA256) :]
        ):
            raise EvaluationIntegrityError(
                "Rendered challenger extension digest drifted"
            )
        try:
            baseline = load_candidate(baseline_rendered.candidate_path)
            challenger = load_candidate(challenger_rendered.candidate_path)
        except Exception as error:
            raise EvaluationIntegrityError(
                "Rendered Palimpsest candidate could not be resolved"
            ) from error

        run_material = _canonical_json(
            {
                "candidate": candidate["ref"],
                "case": case["id"],
                "context": context,
            }
        ).encode("utf-8")
        run_id = "exodia-" + hashlib.sha256(run_material).hexdigest()
        started = time.perf_counter()
        try:
            with EvaluationStore(root / "evaluation.sqlite3") as store:
                result = run_evaluation(
                    run_id=run_id,
                    suite=suite,
                    baseline=baseline,
                    challenger=challenger,
                    store=store,
                    run_root=root / "runs",
                    asset_resolver=resolve_asset,
                    executor="subprocess",
                    workers=1,
                    cases=(selected_case,),
                    environment={
                        "boundary": "R_train",
                        "protocol_version": _PROTOCOL_VERSION,
                        "suite_version": context["suiteVersion"],
                    },
                )
        except (TypeError, ValueError) as error:
            raise EvaluationIntegrityError(
                "Palimpsest evaluation produced invalid scoring evidence"
            ) from error
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        evaluator_artifacts = [
            suite_artifact,
            manifest_artifact,
            artifacts[case["inputArtifactRef"]],
            artifacts[case["goldArtifactRef"]],
            baseline_extension,
            challenger_extension,
        ]
        error_structure = _challenger_error_structure(
            result.report,
            case_id=case["id"],
            gold_artifact=artifacts[case["goldArtifactRef"]],
            runs_root=root / "runs",
        )
        _retain_outputs(
            result.report,
            case_id=case["id"],
            candidate_ref=candidate["ref"],
            runs_root=root / "runs",
        )
        return _observation_from_report(
            report=result.report,
            suite=suite,
            case_id=case["id"],
            strata=selected_case.strata,
            elapsed_ms=elapsed_ms,
            evaluator_artifacts=evaluator_artifacts,
            error_structure=error_structure,
        )


def _read_request() -> object:
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise EvaluationIntegrityError("Request exceeds the protocol size limit")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\r" in raw:
        raise EvaluationIntegrityError(
            "stdin must contain exactly one newline-terminated JSON request"
        )
    try:
        text = raw[:-1].decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                EvaluationIntegrityError(f"Invalid JSON number {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationIntegrityError(
            "stdin request is not valid UTF-8 JSON"
        ) from error


def main() -> int:
    try:
        response = evaluate(_read_request())
    except EvaluationIntegrityError as error:
        sys.stderr.write(f"palimpsest-exodia integrity error: {error}\n")
        return 2
    except Exception as error:
        sys.stderr.write(
            f"palimpsest-exodia infrastructure error: {type(error).__name__}: {error}\n"
        )
        return 3
    sys.stdout.buffer.write(_canonical_json(response).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
