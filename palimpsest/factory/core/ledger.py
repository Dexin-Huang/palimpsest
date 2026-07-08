"""The factory ledger: inventory + append-only production log (FACTORY.md §2.5).

One SQLite database with three tables:

- ``prospects`` — everything the scout heads find, promoted or not
- ``items`` — work orders: prospects promoted onto the line
- ``stage_runs`` — append-only log of every station execution, page-grained

Current line state is the ``stage_state`` view (latest successful run per
doc/page/station), never a mutable column — history is the audit trail and
is never overwritten (design rule §6.7).

The database is an index, not the archive: artifacts on disk carry their own
provenance stamps, and this file must remain rebuildable from the workspace
(design rule §6.4).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from palimpsest.factory.config import FACTORY_DB_PATH
from palimpsest.factory.workspace.io import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
  prospect_id   TEXT PRIMARY KEY,
  head          TEXT NOT NULL,
  archive_ref   TEXT NOT NULL,
  manifest_url  TEXT,
  title         TEXT,
  language      TEXT,
  date_range    TEXT,
  triage_score  INTEGER,
  triage_json   TEXT,
  found_at      TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'found'
    CHECK (status IN ('found', 'triaged', 'promoted', 'rejected'))
);

CREATE TABLE IF NOT EXISTS items (
  doc_id       TEXT PRIMARY KEY,
  prospect_id  TEXT REFERENCES prospects(prospect_id),
  recipe       TEXT NOT NULL,
  mode         TEXT NOT NULL CHECK (mode IN ('source', 'opportunity')),
  promoted_at  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'complete', 'parked', 'failed'))
);

CREATE TABLE IF NOT EXISTS stage_runs (
  run_id             INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id             TEXT NOT NULL REFERENCES items(doc_id),
  page_id            TEXT,
  station            TEXT NOT NULL,
  status             TEXT NOT NULL,
  station_version    TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects (status);
CREATE INDEX IF NOT EXISTS idx_prospects_head ON prospects (head);

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

    # -- inventory: prospects ------------------------------------------------

    def add_prospect(
        self,
        prospect_id: str,
        *,
        head: str,
        archive_ref: str,
        manifest_url: str | None = None,
        title: str | None = None,
        language: str | None = None,
        date_range: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO prospects
                  (prospect_id, head, archive_ref, manifest_url, title,
                   language, date_range, found_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (prospect_id) DO UPDATE SET
                  manifest_url = excluded.manifest_url,
                  title = excluded.title,
                  language = excluded.language,
                  date_range = excluded.date_range
                """,
                (prospect_id, head, archive_ref, manifest_url, title,
                 language, date_range, utc_now()),
            )

    def record_triage(self, prospect_id: str, *, score: int, triage_json: str) -> None:
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE prospects
                SET triage_score = ?, triage_json = ?, status = 'triaged'
                WHERE prospect_id = ? AND status IN ('found', 'triaged')
                """,
                (score, triage_json, prospect_id),
            ).rowcount
        if updated == 0:
            raise KeyError(f"No triageable prospect: {prospect_id}")

    # -- work orders: items --------------------------------------------------

    def promote(self, prospect_id: str, *, doc_id: str, recipe: str, mode: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO items (doc_id, prospect_id, recipe, mode, promoted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, prospect_id, recipe, mode, utc_now()),
            )
            self._conn.execute(
                "UPDATE prospects SET status = 'promoted' WHERE prospect_id = ?",
                (prospect_id,),
            )

    def adopt(self, doc_id: str, *, recipe: str, mode: str = "source") -> None:
        """Put an existing library document on the line without a prospect —
        the seam for documents that predate the scouts."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO items (doc_id, prospect_id, recipe, mode, promoted_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (doc_id, recipe, mode, utc_now()),
            )

    def list_items(self, *, status: str | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT items.*, prospects.head, prospects.archive_ref,
                   prospects.title, prospects.triage_score
            FROM items LEFT JOIN prospects USING (prospect_id)
        """
        params: tuple = ()
        if status is not None:
            query += " WHERE items.status = ?"
            params = (status,)
        return self._conn.execute(query + " ORDER BY promoted_at", params).fetchall()

    # -- production log: stage_runs -------------------------------------------

    def begin_run(
        self,
        doc_id: str,
        station: str,
        *,
        page_id: str | None = None,
        station_version: str,
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
                  (doc_id, page_id, station, status, station_version, model,
                   prompt_name, prompt_hash, params_hash, config_fingerprint,
                   input_fingerprint, started_at)
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, page_id, station, station_version, model, prompt_name,
                 prompt_hash, params_hash, config_fingerprint, input_fingerprint,
                 utc_now()),
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
            run_id, status="done",
            output_path=output_path, output_fingerprint=output_fingerprint,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
        )

    def fail_run(self, run_id: int, *, kind: str, detail: str | None = None) -> None:
        """``kind`` is a short machine-matchable slug (``rate_limit``,
        ``empty_response``); ``detail`` is the full human-readable error."""
        self._finish_run(run_id, status=f"failed:{kind}", error=detail)

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
                (status, output_path, output_fingerprint, tokens_in, tokens_out,
                 cost_usd, utc_now(), error, run_id),
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
