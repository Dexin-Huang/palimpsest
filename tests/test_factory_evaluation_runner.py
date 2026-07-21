from __future__ import annotations

import pytest

from palimpsest.factory.evaluation.report import (
    CaseSideOutcome,
    PairedCaseOutcome,
    ReportIdentity,
    build_report,
    report_fingerprint,
    write_report,
)
from palimpsest.factory.workspace.io import read_json


def _successful(candidate_id: str, fingerprint: str) -> CaseSideOutcome:
    return CaseSideOutcome(
        candidate_id=candidate_id,
        candidate_fingerprint=fingerprint,
        succeeded=True,
        output_path=f"{candidate_id}.json",
        output_fingerprint=f"output-{fingerprint}",
        latency_seconds=1.25,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.01,
    )


def test_case_outcome_requires_consistent_success_and_failure_fields():
    with pytest.raises(ValueError, match="output identity"):
        CaseSideOutcome(
            candidate_id="candidate",
            candidate_fingerprint="fingerprint",
            succeeded=True,
            output_path=None,
            output_fingerprint=None,
            latency_seconds=0.0,
        )

    with pytest.raises(ValueError, match="error_kind"):
        CaseSideOutcome(
            candidate_id="candidate",
            candidate_fingerprint="fingerprint",
            succeeded=False,
            output_path=None,
            output_fingerprint=None,
            latency_seconds=0.0,
        )


def test_report_has_one_canonical_shape_and_content_identity(tmp_path):
    baseline = ReportIdentity("read/baseline", "baseline-fingerprint")
    challenger = ReportIdentity("read/challenger", "challenger-fingerprint")
    case = PairedCaseOutcome(
        case_id="case-1",
        baseline=_successful(baseline.id, baseline.fingerprint),
        challenger=_successful(challenger.id, challenger.fingerprint),
    )
    report = build_report(
        run_id="run-1",
        status="completed",
        decision="qualified",
        started_at="2026-07-20T00:00:00+00:00",
        finished_at="2026-07-20T00:01:00+00:00",
        suite=ReportIdentity("read/latin/v1", "suite-fingerprint"),
        baseline=baseline,
        challenger=challenger,
        cases=(case,),
        qualification={"decision": "qualified", "reasons": []},
    )

    assert report["baseline"] == {
        "id": "read/baseline",
        "fingerprint": "baseline-fingerprint",
    }
    assert report["cases"][0]["case_id"] == "case-1"
    assert report["report_fingerprint"] == report_fingerprint(report)

    report_path = tmp_path / "report.json"
    write_report(report_path, report)
    assert read_json(report_path) == report
    with pytest.raises(FileExistsError, match="already exists"):
        write_report(report_path, report)

    report["decision"] = "rejected"
    with pytest.raises(ValueError, match="fingerprint"):
        write_report(tmp_path / "tampered.json", report)
