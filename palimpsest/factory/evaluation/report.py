"""Canonical evaluation outcomes and immutable scorecard reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from palimpsest.factory.workspace.io import atomic_write_json

REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CaseSideOutcome:
    """One candidate's observable execution result for one benchmark case."""

    candidate_id: str
    candidate_fingerprint: str
    succeeded: bool
    output_path: str | None
    output_fingerprint: str | None
    latency_seconds: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    process_stats: Mapping[str, int] | None = None
    error_kind: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.latency_seconds < 0:
            raise ValueError("latency_seconds must be non-negative")
        if self.succeeded:
            if self.output_path is None or self.output_fingerprint is None:
                raise ValueError("Successful case outcomes require output identity")
            if self.error_kind is not None or self.error_message is not None:
                raise ValueError("Successful case outcomes cannot carry an error")
        elif self.error_kind is None:
            raise ValueError("Failed case outcomes require error_kind")


@dataclass(frozen=True)
class PairedCaseOutcome:
    """Baseline and challenger outcomes produced from the same case inputs."""

    case_id: str
    baseline: CaseSideOutcome
    challenger: CaseSideOutcome


@dataclass(frozen=True)
class ReportIdentity:
    id: str
    fingerprint: str


def report_fingerprint(payload: Mapping[str, Any]) -> str:
    """Hash a report without allowing its own fingerprint to affect identity."""

    canonical = dict(payload)
    canonical.pop("report_fingerprint", None)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_report(
    *,
    run_id: str,
    status: str,
    decision: str,
    started_at: str,
    finished_at: str,
    suite: ReportIdentity,
    baseline: ReportIdentity,
    challenger: ReportIdentity,
    cases: Sequence[PairedCaseOutcome],
    judges: Sequence[ReportIdentity] = (),
    aggregates: Mapping[str, Any] | None = None,
    downstream_probes: Sequence[Mapping[str, Any]] = (),
    qualification: Mapping[str, Any] | None = None,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the sole canonical top-level scorecard shape."""

    if not run_id:
        raise ValueError("run_id is required")
    if not status:
        raise ValueError("status is required")
    if not decision:
        raise ValueError("decision is required")
    if not started_at or not finished_at:
        raise ValueError("started_at and finished_at are required")

    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "decision": decision,
        "started_at": started_at,
        "finished_at": finished_at,
        "suite": asdict(suite),
        "baseline": asdict(baseline),
        "challenger": asdict(challenger),
        "judges": [asdict(judge) for judge in judges],
        "cases": [
            {
                "case_id": case.case_id,
                "baseline": asdict(case.baseline),
                "challenger": asdict(case.challenger),
            }
            for case in cases
        ],
        "aggregates": dict(aggregates or {}),
        "downstream_probes": [dict(probe) for probe in downstream_probes],
        "qualification": dict(
            qualification
            or {
                "decision": decision,
                "reasons": [],
            }
        ),
        "environment": dict(environment or {}),
    }
    payload["report_fingerprint"] = report_fingerprint(payload)
    return payload


def write_report(path: Path, payload: Mapping[str, Any]) -> Path:
    """Validate report identity and publish it atomically."""
    if path.exists():
        raise FileExistsError(f"Evaluation report already exists: {path}")

    expected = report_fingerprint(payload)
    if payload.get("report_fingerprint") != expected:
        raise ValueError("Evaluation report fingerprint does not match its content")
    atomic_write_json(path, dict(payload))
    return path
