"""Factory inventory and append-only production history.

The SQLite ledger contains work orders and station runs. ``stage_state`` is the
latest successful run for each document, page, and station; prior runs remain
the audit trail. Artifacts carry independent provenance, so the ledger remains
an index rather than the archive.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from palimpsest.factory.config import FACTORY_DB_PATH
from palimpsest.factory.workspace.io import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  doc_id      TEXT PRIMARY KEY,
  recipe      TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'complete', 'parked', 'failed'))
);

CREATE TABLE IF NOT EXISTS work_runs (
  work_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      TEXT NOT NULL REFERENCES items(doc_id),
  owner       TEXT NOT NULL,
  status      TEXT NOT NULL
    CHECK (status IN ('running', 'done', 'failed', 'abandoned')),
  started_at  TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  finished_at TEXT,
  error       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_work_runs_active_doc
  ON work_runs (doc_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS stage_runs (
  run_id             INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id             TEXT NOT NULL REFERENCES items(doc_id),
  page_id            TEXT,
  station            TEXT NOT NULL,
  status             TEXT NOT NULL,
  station_fingerprint TEXT NOT NULL,
  model              TEXT,
  prompt_name        TEXT,
  prompt_hash        TEXT,
  params_hash        TEXT,
  config_fingerprint TEXT NOT NULL,
  input_fingerprint  TEXT NOT NULL,
  output_fingerprint TEXT,
  output_path        TEXT,
  tokens_in          INTEGER,
  tokens_out         INTEGER,
  cost_usd           REAL,
  started_at         TEXT NOT NULL,
  finished_at        TEXT,
  error              TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_runs_doc
  ON stage_runs (doc_id, station, page_id);

CREATE VIEW IF NOT EXISTS stage_state AS
SELECT runs.*
FROM stage_runs AS runs
JOIN (
  SELECT MAX(run_id) AS run_id
  FROM stage_runs
  WHERE status = 'done'
  GROUP BY doc_id, station, COALESCE(page_id, '')
) AS latest ON latest.run_id = runs.run_id;
"""


def fingerprint(*parts: str | None) -> str:
    """Stable hash over an ordered set of provenance parts."""
    joined = "\x1f".join("" if part is None else part for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


class Ledger:
    def __init__(self, db_path: Path = FACTORY_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # The conductor shares one connection across its worker threads,
        # serialized by its own lock; WAL mode keeps readers unblocked.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- work orders ----------------------------------------------------------

    def adopt(self, doc_id: str, *, recipe: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO items (doc_id, recipe, created_at)
                VALUES (?, ?, ?)
                """,
                (doc_id, recipe, utc_now()),
            )

    def item(self, doc_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM items WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()

    def set_item_status(self, doc_id: str, status: str) -> None:
        if status not in {"active", "complete", "parked", "failed"}:
            raise ValueError(f"Unknown item status: {status!r}")
        with self._conn:
            updated = self._conn.execute(
                "UPDATE items SET status = ? WHERE doc_id = ?",
                (status, doc_id),
            ).rowcount
        if updated == 0:
            raise KeyError(f"No work order for {doc_id!r}")

    def list_items(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM items ORDER BY created_at"
        ).fetchall()

    # -- durable work-order ownership ----------------------------------------

    def claim_work(self, doc_id: str, *, owner: str) -> int:
        now = utc_now()
        try:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO work_runs
                      (doc_id, owner, status, started_at, heartbeat_at)
                    VALUES (?, ?, 'running', ?, ?)
                    """,
                    (doc_id, owner, now, now),
                )
                self._conn.execute(
                    """
                    UPDATE stage_runs
                    SET status = 'failed:abandoned', finished_at = ?,
                        error = 'orphaned before a new work run claimed the document'
                    WHERE doc_id = ? AND status = 'running'
                    """,
                    (now, doc_id),
                )
        except sqlite3.IntegrityError as error:
            active = self._conn.execute(
                """
                SELECT owner, heartbeat_at FROM work_runs
                WHERE doc_id = ? AND status = 'running'
                """,
                (doc_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError(
                    f"Work order {doc_id!r} is already running under "
                    f"{active['owner']} (heartbeat {active['heartbeat_at']})"
                ) from error
            raise
        return cursor.lastrowid

    def heartbeat_work(self, work_run_id: int) -> None:
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE work_runs SET heartbeat_at = ?
                WHERE work_run_id = ? AND status = 'running'
                """,
                (utc_now(), work_run_id),
            ).rowcount
        if updated == 0:
            raise KeyError(f"No running work_run with id {work_run_id}")

    def finish_work(
        self,
        work_run_id: int,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in {"done", "failed"}:
            raise ValueError(f"Invalid terminal work status: {status!r}")
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE work_runs
                SET status = ?, finished_at = ?, heartbeat_at = ?, error = ?
                WHERE work_run_id = ? AND status = 'running'
                """,
                (status, utc_now(), utc_now(), error, work_run_id),
            ).rowcount
        if updated == 0:
            raise KeyError(f"No running work_run with id {work_run_id}")

    def reconcile_abandoned(self, doc_id: str, *, stale_before: str) -> int:
        """Close expired work claims and their unfinished cell attempts."""
        now = utc_now()
        with self._conn:
            abandoned = self._conn.execute(
                """
                UPDATE work_runs
                SET status = 'abandoned', finished_at = ?,
                    error = 'heartbeat expired'
                WHERE doc_id = ? AND status = 'running' AND heartbeat_at < ?
                """,
                (now, doc_id, stale_before),
            ).rowcount
            if abandoned:
                self._conn.execute(
                    """
                    UPDATE stage_runs
                    SET status = 'failed:abandoned', finished_at = ?,
                        error = 'owning work run was abandoned'
                    WHERE doc_id = ? AND status = 'running'
                    """,
                    (now, doc_id),
                )
        return abandoned

    # -- production log: stage_runs -------------------------------------------

    def begin_run(
        self,
        doc_id: str,
        station: str,
        *,
        page_id: str | None = None,
        station_fingerprint: str,
        config_fingerprint: str,
        input_fingerprint: str,
        model: str | None = None,
        prompt_name: str | None = None,
        prompt_hash: str | None = None,
        params_hash: str | None = None,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO stage_runs
                  (doc_id, page_id, station, status, station_fingerprint, model,
                   prompt_name, prompt_hash, params_hash, config_fingerprint,
                   input_fingerprint, started_at)
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    page_id,
                    station,
                    station_fingerprint,
                    model,
                    prompt_name,
                    prompt_hash,
                    params_hash,
                    config_fingerprint,
                    input_fingerprint,
                    utc_now(),
                ),
            )
        return cursor.lastrowid

    def complete_run(
        self,
        run_id: int,
        *,
        output_path: str | None = None,
        output_fingerprint: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self._finish_run(
            run_id,
            status="done",
            output_path=output_path,
            output_fingerprint=output_fingerprint,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    def fail_run(
        self,
        run_id: int,
        *,
        kind: str,
        detail: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Finish a failed attempt while retaining any known billed usage."""
        self._finish_run(
            run_id,
            status=f"failed:{kind}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            error=detail,
        )

    def _finish_run(
        self,
        run_id: int,
        *,
        status: str,
        output_path: str | None = None,
        output_fingerprint: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, output_path = ?, output_fingerprint = ?,
                    tokens_in = ?, tokens_out = ?, cost_usd = ?,
                    finished_at = ?, error = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (
                    status,
                    output_path,
                    output_fingerprint,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    utc_now(),
                    error,
                    run_id,
                ),
            ).rowcount
        if updated == 0:
            raise KeyError(f"No running stage_run with id {run_id}")

    # -- current state ---------------------------------------------------------

    def state(self, doc_id: str) -> list[sqlite3.Row]:
        """Latest successful run per (station, page) for one document."""
        return self._conn.execute(
            "SELECT * FROM stage_state WHERE doc_id = ? ORDER BY station, page_id",
            (doc_id,),
        ).fetchall()
