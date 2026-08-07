"""SQLite indexes for immutable, file-backed evaluation evidence."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from palimpsest.factory.config import EVALUATION_DB_PATH
from palimpsest.factory.evaluation import _record
from palimpsest.factory.evaluation.candidate import RecordError, canonical_json
from palimpsest.factory.evaluation.report import (
    report_fingerprint as compute_report_fingerprint,
)

_REPORT_KEYS = {
    "schema_version",
    "run_id",
    "status",
    "decision",
    "started_at",
    "finished_at",
    "suite",
    "baseline",
    "challenger",
    "judges",
    "cases",
    "aggregates",
    "downstream_probes",
    "qualification",
    "environment",
    "report_fingerprint",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
  run_id                   TEXT PRIMARY KEY,
  suite_id                 TEXT NOT NULL,
  suite_fingerprint        TEXT NOT NULL,
  baseline_fingerprint     TEXT NOT NULL,
  challenger_fingerprint   TEXT NOT NULL,
  status                   TEXT NOT NULL,
  decision                 TEXT,
  report_path              TEXT,
  report_fingerprint       TEXT,
  started_at               TEXT NOT NULL,
  finished_at              TEXT,
  CHECK (
    (status = 'running' AND decision IS NULL AND report_path IS NULL
      AND report_fingerprint IS NULL AND finished_at IS NULL)
    OR
    (status <> 'running' AND report_path IS NOT NULL
      AND report_fingerprint IS NOT NULL AND finished_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_suite
  ON evaluation_runs (suite_id, started_at);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_candidates
  ON evaluation_runs (baseline_fingerprint, challenger_fingerprint);

CREATE TABLE IF NOT EXISTS evaluation_promotions (
  promotion_id                    TEXT PRIMARY KEY,
  action                          TEXT NOT NULL,
  recipe                          TEXT NOT NULL,
  station                         TEXT NOT NULL,
  previous_candidate_fingerprint  TEXT NOT NULL,
  next_candidate_fingerprint      TEXT NOT NULL,
  evaluation_run                  TEXT NOT NULL,
  canary_run                      TEXT,
  approved_by                     TEXT NOT NULL,
  created_at                      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluation_promotions_recipe
  ON evaluation_promotions (recipe, station, created_at);
CREATE INDEX IF NOT EXISTS idx_evaluation_promotions_run
  ON evaluation_promotions (evaluation_run);
"""


def _nonempty(value: object, *, field: str) -> str:
    return _record.string(value, field=field, error_cls=RecordError)


def _digest(value: object, *, field: str) -> str:
    result = _nonempty(value, field=field)
    if not _SHA256.fullmatch(result):
        raise RecordError(f"{field} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: object, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    result = _nonempty(value, field=field)
    if "T" not in result or not result.endswith("Z"):
        raise RecordError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(result[:-1] + "+00:00")
    except ValueError as error:
        raise RecordError(f"{field} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise RecordError(f"{field} must be UTC")
    return result


def _identity(value: object, *, field: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"id", "fingerprint"}:
        raise RecordError(f"{field} must contain exactly id and fingerprint")
    return _nonempty(value["id"], field=f"{field}.id"), _digest(
        value["fingerprint"], field=f"{field}.fingerprint"
    )


_unique_json_object = _record.make_duplicate_key_json_hook(RecordError)


def _read_canonical_report(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RecordError(f"Cannot read evaluation report {path}: {error}") from error
    try:
        report = json.loads(raw, object_pairs_hook=_unique_json_object)
        canonical_json(report)
    except (json.JSONDecodeError, RecordError) as error:
        raise RecordError(f"Invalid evaluation report JSON {path}: {error}") from error
    if not isinstance(report, dict) or set(report) != _REPORT_KEYS:
        actual = set(report) if isinstance(report, dict) else set()
        raise RecordError(
            f"Evaluation report has invalid keys; missing={sorted(_REPORT_KEYS - actual)}, "
            f"unknown={sorted(actual - _REPORT_KEYS)}"
        )
    _record.schema_version(
        report["schema_version"],
        field="Evaluation report schema_version",
        error_cls=RecordError,
    )
    expected = compute_report_fingerprint(report)
    actual_fingerprint = _digest(
        report["report_fingerprint"], field="report.report_fingerprint"
    )
    if actual_fingerprint != expected:
        raise RecordError(
            f"Evaluation report fingerprint mismatch: expected {expected}, got {actual_fingerprint}"
        )
    _nonempty(report["run_id"], field="report.run_id")
    status = _nonempty(report["status"], field="report.status")
    if status == "running":
        raise RecordError("Completed report files cannot have running status")
    decision = report["decision"]
    if decision is not None:
        _nonempty(decision, field="report.decision")
    _timestamp(report["started_at"], field="report.started_at")
    _timestamp(report["finished_at"], field="report.finished_at")
    _identity(report["suite"], field="report.suite")
    _identity(report["baseline"], field="report.baseline")
    _identity(report["challenger"], field="report.challenger")
    if not isinstance(report["judges"], list):
        raise RecordError("report.judges must be a list")
    for index, judge in enumerate(report["judges"]):
        _identity(judge, field=f"report.judges[{index}]")
    if not isinstance(report["cases"], list):
        raise RecordError("report.cases must be a list")
    if not isinstance(report["aggregates"], dict):
        raise RecordError("report.aggregates must be a mapping")
    if not isinstance(report["environment"], dict):
        raise RecordError("report.environment must be a mapping")
    if not isinstance(report["downstream_probes"], list):
        raise RecordError("report.downstream_probes must be a list")
    qualification = report["qualification"]
    if not isinstance(qualification, dict) or set(qualification) != {
        "decision",
        "reasons",
    }:
        raise RecordError(
            "report.qualification must contain exactly decision and reasons"
        )
    if qualification["decision"] is not None:
        _nonempty(qualification["decision"], field="report.qualification.decision")
    if not isinstance(qualification["reasons"], list) or not all(
        isinstance(reason, str) and reason for reason in qualification["reasons"]
    ):
        raise RecordError("report.qualification.reasons must be a list of strings")
    return report


@dataclass(frozen=True, slots=True)
class EvaluationRunIndex:
    run_id: str
    suite_id: str
    suite_fingerprint: str
    baseline_fingerprint: str
    challenger_fingerprint: str
    status: str
    decision: str | None
    report_path: str | None
    report_fingerprint: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class EvaluationPromotionIndex:
    promotion_id: str
    action: str
    recipe: str
    station: str
    previous_candidate_fingerprint: str
    next_candidate_fingerprint: str
    evaluation_run: str
    canary_run: str | None
    approved_by: str
    created_at: str


def _run_from_row(row: sqlite3.Row) -> EvaluationRunIndex:
    return EvaluationRunIndex(**dict(row))


def _promotion_from_row(row: sqlite3.Row) -> EvaluationPromotionIndex:
    return EvaluationPromotionIndex(**dict(row))


def _report_index(report: dict[str, Any], path: Path) -> EvaluationRunIndex:
    suite_id, suite_fingerprint = _identity(report["suite"], field="report.suite")
    _, baseline_fingerprint = _identity(report["baseline"], field="report.baseline")
    _, challenger_fingerprint = _identity(
        report["challenger"], field="report.challenger"
    )
    return EvaluationRunIndex(
        run_id=report["run_id"],
        suite_id=suite_id,
        suite_fingerprint=suite_fingerprint,
        baseline_fingerprint=baseline_fingerprint,
        challenger_fingerprint=challenger_fingerprint,
        status=report["status"],
        decision=report["decision"],
        report_path=str(path.resolve()),
        report_fingerprint=report["report_fingerprint"],
        started_at=report["started_at"],
        finished_at=report["finished_at"],
    )


class EvaluationStore:
    """Own only evaluation index tables; canonical reports remain authoritative."""

    def __init__(self, db_path: str | Path = EVALUATION_DB_PATH) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvaluationStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def begin_run(
        self,
        *,
        run_id: str,
        suite_id: str,
        suite_fingerprint: str,
        baseline_fingerprint: str,
        challenger_fingerprint: str,
        started_at: str,
    ) -> EvaluationRunIndex:
        values = EvaluationRunIndex(
            run_id=_nonempty(run_id, field="run_id"),
            suite_id=_nonempty(suite_id, field="suite_id"),
            suite_fingerprint=_digest(suite_fingerprint, field="suite_fingerprint"),
            baseline_fingerprint=_digest(
                baseline_fingerprint, field="baseline_fingerprint"
            ),
            challenger_fingerprint=_digest(
                challenger_fingerprint, field="challenger_fingerprint"
            ),
            status="running",
            decision=None,
            report_path=None,
            report_fingerprint=None,
            started_at=_timestamp(started_at, field="started_at"),
            finished_at=None,
        )
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO evaluation_runs
                      (run_id, suite_id, suite_fingerprint, baseline_fingerprint,
                       challenger_fingerprint, status, decision, report_path,
                       report_fingerprint, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        getattr(values, name) for name in EvaluationRunIndex.__slots__
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RecordError(f"Evaluation run already exists: {run_id!r}") from error
        return values

    def index_report(self, report_path: str | Path) -> EvaluationRunIndex:
        """Commit a terminal report once, or finish its matching running row."""
        path = Path(report_path)
        values = _report_index(_read_canonical_report(path), path)
        existing = self.run(values.run_id)
        if existing is not None and existing.status != "running":
            if existing == values:
                return existing
            raise RecordError(
                f"Completed evaluation run is immutable: {values.run_id!r}"
            )
        if existing is not None and (
            existing.suite_id != values.suite_id
            or existing.suite_fingerprint != values.suite_fingerprint
            or existing.baseline_fingerprint != values.baseline_fingerprint
            or existing.challenger_fingerprint != values.challenger_fingerprint
            or existing.started_at != values.started_at
        ):
            raise RecordError(
                f"Report identities do not match running evaluation {values.run_id!r}"
            )
        with self._conn:
            if existing is None:
                self._insert_terminal(values)
            else:
                updated = self._conn.execute(
                    """
                    UPDATE evaluation_runs
                    SET status = ?, decision = ?, report_path = ?,
                        report_fingerprint = ?, finished_at = ?
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (
                        values.status,
                        values.decision,
                        values.report_path,
                        values.report_fingerprint,
                        values.finished_at,
                        values.run_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise RecordError(
                        f"Evaluation run is no longer running: {values.run_id!r}"
                    )
        return values

    def _insert_terminal(self, values: EvaluationRunIndex) -> None:
        self._conn.execute(
            """
            INSERT INTO evaluation_runs
              (run_id, suite_id, suite_fingerprint, baseline_fingerprint,
               challenger_fingerprint, status, decision, report_path,
               report_fingerprint, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(getattr(values, name) for name in EvaluationRunIndex.__slots__),
        )

    def rebuild_from_reports(self, reports_root: str | Path) -> int:
        """Atomically rebuild run indexes from canonical report.json files."""
        root = Path(reports_root).resolve()
        if not root.is_dir():
            raise RecordError(f"Evaluation reports root does not exist: {root}")
        indexed: list[EvaluationRunIndex] = []
        seen: set[str] = set()
        for path in sorted(root.rglob("report.json")):
            values = _report_index(_read_canonical_report(path), path)
            if values.run_id in seen:
                raise RecordError(f"Duplicate report run_id: {values.run_id!r}")
            seen.add(values.run_id)
            indexed.append(values)
        with self._conn:
            self._conn.execute("DELETE FROM evaluation_runs")
            for values in indexed:
                self._insert_terminal(values)
        return len(indexed)

    def run(self, run_id: str) -> EvaluationRunIndex | None:
        row = self._conn.execute(
            "SELECT * FROM evaluation_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else _run_from_row(row)

    def runs(self, *, suite_id: str | None = None) -> tuple[EvaluationRunIndex, ...]:
        if suite_id is None:
            rows = self._conn.execute(
                "SELECT * FROM evaluation_runs ORDER BY started_at, run_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM evaluation_runs
                   WHERE suite_id = ? ORDER BY started_at, run_id""",
                (suite_id,),
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    def record_promotion(self, promotion: EvaluationPromotionIndex) -> None:
        values = EvaluationPromotionIndex(
            promotion_id=_nonempty(promotion.promotion_id, field="promotion_id"),
            action=_nonempty(promotion.action, field="action"),
            recipe=_nonempty(promotion.recipe, field="recipe"),
            station=_nonempty(promotion.station, field="station"),
            previous_candidate_fingerprint=_digest(
                promotion.previous_candidate_fingerprint,
                field="previous_candidate_fingerprint",
            ),
            next_candidate_fingerprint=_digest(
                promotion.next_candidate_fingerprint,
                field="next_candidate_fingerprint",
            ),
            evaluation_run=_nonempty(promotion.evaluation_run, field="evaluation_run"),
            canary_run=(
                None
                if promotion.canary_run is None
                else _nonempty(promotion.canary_run, field="canary_run")
            ),
            approved_by=_nonempty(promotion.approved_by, field="approved_by"),
            created_at=_timestamp(promotion.created_at, field="created_at"),
        )
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO evaluation_promotions
                      (promotion_id, action, recipe, station,
                       previous_candidate_fingerprint, next_candidate_fingerprint,
                       evaluation_run, canary_run, approved_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        getattr(values, name)
                        for name in EvaluationPromotionIndex.__slots__
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RecordError(
                f"Evaluation promotion already exists: {values.promotion_id!r}"
            ) from error

    def promotions(self) -> tuple[EvaluationPromotionIndex, ...]:
        rows = self._conn.execute(
            "SELECT * FROM evaluation_promotions ORDER BY created_at, promotion_id"
        ).fetchall()
        return tuple(_promotion_from_row(row) for row in rows)
