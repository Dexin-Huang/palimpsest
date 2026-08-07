"""SQLite source catalog: immutable revisions plus current-state pointers.

The catalog owns its database location and timestamps: ``PALIMPSEST_CATALOG_DB``
overrides the default ``library/catalog.db`` (itself relative to
``PALIMPSEST_LIBRARY_ROOT``), so this package has no dependency on factory
configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from palimpsest.catalog.records import RecordPage, SourceRecord


def utc_now() -> str:
    """Current UTC time as ISO-8601 with a Z suffix (catalog record form)."""
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _env_path(key: str, default: Path) -> Path:
    raw = os.getenv(key)
    return Path(raw) if raw and raw.strip() else default


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LIBRARY_ROOT = _env_path("PALIMPSEST_LIBRARY_ROOT", _PROJECT_ROOT / "library")
CATALOG_DB_PATH = _env_path("PALIMPSEST_CATALOG_DB", _LIBRARY_ROOT / "catalog.db")


_END_CURSOR = "__palimpsest_end__"
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS catalog_sources (
    source_id TEXT PRIMARY KEY,
    head TEXT NOT NULL,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES catalog_sources(source_id),
    status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
    cursor TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    records_seen INTEGER NOT NULL DEFAULT 0,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_revised INTEGER NOT NULL DEFAULT 0,
    records_unchanged INTEGER NOT NULL DEFAULT 0,
    records_revived INTEGER NOT NULL DEFAULT 0,
    records_tombstoned INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS catalog_sync_one_running
    ON catalog_sync_runs(source_id) WHERE status = 'running';
CREATE TABLE IF NOT EXISTS catalog_records (
    record_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES catalog_sources(source_id),
    source_key TEXT NOT NULL,
    current_revision INTEGER NOT NULL,
    current_fingerprint TEXT NOT NULL,
    source_modified_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_seen_sync INTEGER NOT NULL REFERENCES catalog_sync_runs(sync_id),
    tombstoned INTEGER NOT NULL DEFAULT 0 CHECK (tombstoned IN (0, 1)),
    UNIQUE (source_id, source_key)
);
CREATE INDEX IF NOT EXISTS catalog_records_source
    ON catalog_records(source_id, tombstoned);
CREATE TABLE IF NOT EXISTS catalog_record_revisions (
    record_id TEXT NOT NULL REFERENCES catalog_records(record_id),
    revision INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    source_modified_at TEXT,
    fingerprint TEXT NOT NULL,
    source_url TEXT,
    normalized_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (record_id, revision)
);
CREATE TABLE IF NOT EXISTS catalog_record_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES catalog_records(record_id),
    sync_id INTEGER NOT NULL REFERENCES catalog_sync_runs(sync_id),
    event TEXT NOT NULL CHECK (event IN ('discovered', 'revised', 'revived', 'tombstoned')),
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_events_record
    ON catalog_record_events(record_id, event_id);
PRAGMA user_version = 1;
"""


@dataclass(frozen=True)
class CatalogSource:
    source_id: str
    head: str
    config: Mapping[str, Any]
    enabled: bool


@dataclass(frozen=True)
class SyncRun:
    sync_id: int
    source_id: str
    cursor: str | None

    @property
    def reached_end(self) -> bool:
        return self.cursor == _END_CURSOR


@dataclass(frozen=True)
class PageChanges:
    seen: int = 0
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    revived: int = 0


class CatalogDB:
    def __init__(self, path: Path | str = CATALOG_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f"catalog database schema {version} is unsupported")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "CatalogDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_source(self, source_id: str, head: str, config: Mapping[str, Any]) -> None:
        _validate_source_id(source_id)
        config_json = _canonical_json(config, "source config")
        existing = self._connection.execute(
            "SELECT head, config_json FROM catalog_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if existing is not None:
            if existing["head"] == head and existing["config_json"] == config_json:
                return
            raise ValueError(
                f"catalog source {source_id!r} already exists with different configuration"
            )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO catalog_sources(source_id, head, config_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, head, config_json, utc_now()),
            )

    def source(self, source_id: str) -> CatalogSource:
        row = self._connection.execute(
            "SELECT * FROM catalog_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown catalog source {source_id!r}")
        return CatalogSource(
            source_id=row["source_id"],
            head=row["head"],
            config=json.loads(row["config_json"]),
            enabled=bool(row["enabled"]),
        )

    def sources(self) -> tuple[CatalogSource, ...]:
        rows = self._connection.execute(
            "SELECT * FROM catalog_sources ORDER BY source_id"
        ).fetchall()
        return tuple(
            CatalogSource(
                source_id=row["source_id"],
                head=row["head"],
                config=json.loads(row["config_json"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        )

    def begin_sync(self, source_id: str, *, resume: bool = False) -> SyncRun:
        source = self.source(source_id)
        if not source.enabled:
            raise ValueError(f"catalog source {source_id!r} is disabled")
        with self._connection:
            active = self._connection.execute(
                """
                SELECT sync_id, status, cursor
                FROM catalog_sync_runs
                WHERE source_id = ? AND status IN ('running', 'failed')
                ORDER BY sync_id DESC LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if active is not None:
                if not resume:
                    raise ValueError(
                        f"catalog source {source_id!r} has resumable sync "
                        f"{active['sync_id']}; pass resume=True"
                    )
                self._connection.execute(
                    """
                    UPDATE catalog_sync_runs
                    SET status = 'running', finished_at = NULL, error = NULL
                    WHERE sync_id = ?
                    """,
                    (active["sync_id"],),
                )
                return SyncRun(active["sync_id"], source_id, active["cursor"])
            if resume:
                raise ValueError(f"catalog source {source_id!r} has no sync to resume")
            cursor = self._connection.execute(
                """
                INSERT INTO catalog_sync_runs(source_id, status, started_at)
                VALUES (?, 'running', ?)
                """,
                (source_id, utc_now()),
            )
            return SyncRun(cursor.lastrowid, source_id, None)

    def apply_page(self, run: SyncRun, page: RecordPage) -> PageChanges:
        now = utc_now()
        changes = PageChanges()
        with self._connection:
            self._assert_running(run)
            for record in page.records:
                change = self._apply_record(run, record, now)
                changes = PageChanges(
                    seen=changes.seen + 1,
                    inserted=changes.inserted + change.inserted,
                    revised=changes.revised + change.revised,
                    unchanged=changes.unchanged + change.unchanged,
                    revived=changes.revived + change.revived,
                )
            cursor = page.next_cursor if page.next_cursor is not None else _END_CURSOR
            self._connection.execute(
                """
                UPDATE catalog_sync_runs
                SET cursor = ?,
                    records_seen = records_seen + ?,
                    records_inserted = records_inserted + ?,
                    records_revised = records_revised + ?,
                    records_unchanged = records_unchanged + ?,
                    records_revived = records_revived + ?
                WHERE sync_id = ?
                """,
                (
                    cursor,
                    changes.seen,
                    changes.inserted,
                    changes.revised,
                    changes.unchanged,
                    changes.revived,
                    run.sync_id,
                ),
            )
        return changes

    def complete_sync(self, run: SyncRun) -> int:
        now = utc_now()
        with self._connection:
            self._assert_running(run)
            stale = self._connection.execute(
                """
                SELECT record_id FROM catalog_records
                WHERE source_id = ? AND tombstoned = 0 AND last_seen_sync != ?
                """,
                (run.source_id, run.sync_id),
            ).fetchall()
            if stale:
                record_ids = [row["record_id"] for row in stale]
                self._connection.executemany(
                    "UPDATE catalog_records SET tombstoned = 1 WHERE record_id = ?",
                    ((record_id,) for record_id in record_ids),
                )
                self._connection.executemany(
                    """
                    INSERT INTO catalog_record_events(record_id, sync_id, event, observed_at)
                    VALUES (?, ?, 'tombstoned', ?)
                    """,
                    ((record_id, run.sync_id, now) for record_id in record_ids),
                )
            self._connection.execute(
                """
                UPDATE catalog_sync_runs
                SET status = 'complete', finished_at = ?, cursor = NULL,
                    records_tombstoned = ?
                WHERE sync_id = ?
                """,
                (now, len(stale), run.sync_id),
            )
        return len(stale)

    def fail_sync(self, run: SyncRun, error: BaseException) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE catalog_sync_runs
                SET status = 'failed', finished_at = ?, error = ?
                WHERE sync_id = ? AND status = 'running'
                """,
                (utc_now(), f"{type(error).__name__}: {error}", run.sync_id),
            )

    def stats(self) -> dict[str, Any]:
        sources = self._connection.execute(
            "SELECT COUNT(*) FROM catalog_sources"
        ).fetchone()[0]
        records = self._connection.execute(
            "SELECT COUNT(*) FROM catalog_records"
        ).fetchone()[0]
        active = self._connection.execute(
            "SELECT COUNT(*) FROM catalog_records WHERE tombstoned = 0"
        ).fetchone()[0]
        revisions = self._connection.execute(
            "SELECT COUNT(*) FROM catalog_record_revisions"
        ).fetchone()[0]
        return {
            "sources": sources,
            "records": records,
            "active_records": active,
            "tombstoned_records": records - active,
            "revisions": revisions,
        }

    def records(
        self, source_id: str, *, limit: int = 100
    ) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.source(source_id)
        rows = self._connection.execute(
            """
            SELECT r.record_id, r.source_key, r.current_revision, r.tombstoned,
                   v.source_url, v.normalized_json
            FROM catalog_records r
            JOIN catalog_record_revisions v
              ON v.record_id = r.record_id AND v.revision = r.current_revision
            WHERE r.source_id = ?
            ORDER BY r.source_key LIMIT ?
            """,
            (source_id, limit),
        ).fetchall()
        return tuple(
            {
                "record_id": row["record_id"],
                "source_key": row["source_key"],
                "revision": row["current_revision"],
                "tombstoned": bool(row["tombstoned"]),
                "source_url": row["source_url"],
                "record": json.loads(row["normalized_json"]),
            }
            for row in rows
        )

    def sync_run(self, sync_id: int) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM catalog_sync_runs WHERE sync_id = ?", (sync_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown catalog sync {sync_id}")
        return dict(row)

    def _apply_record(
        self, run: SyncRun, record: SourceRecord, now: str
    ) -> PageChanges:
        record_id = _record_id(run.source_id, record.source_key)
        normalized_json = _canonical_json(
            record.normalized.as_dict(), "normalized record"
        )
        raw_json = _canonical_json(record.raw, "raw source record")
        fingerprint = _fingerprint(
            record.source_url,
            record.source_modified_at,
            normalized_json,
            raw_json,
        )
        existing = self._connection.execute(
            "SELECT * FROM catalog_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        if existing is not None and existing["last_seen_sync"] == run.sync_id:
            raise ValueError(
                f"duplicate source_key {record.source_key!r} in catalog sync "
                f"{run.sync_id}"
            )
        if existing is None:
            self._connection.execute(
                """
                INSERT INTO catalog_records(
                    record_id, source_id, source_key, current_revision,
                    current_fingerprint, source_modified_at, first_seen_at,
                    last_seen_at, last_seen_sync, tombstoned
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 0)
                """,
                (
                    record_id,
                    run.source_id,
                    record.source_key,
                    fingerprint,
                    record.source_modified_at,
                    now,
                    now,
                    run.sync_id,
                ),
            )
            self._insert_revision(
                record_id,
                1,
                record,
                now,
                fingerprint,
                normalized_json,
                raw_json,
            )
            self._insert_event(record_id, run.sync_id, "discovered", now)
            return PageChanges(inserted=1)

        revived = int(bool(existing["tombstoned"]))
        revised = int(existing["current_fingerprint"] != fingerprint)
        revision = existing["current_revision"] + revised
        self._connection.execute(
            """
            UPDATE catalog_records
            SET current_revision = ?, current_fingerprint = ?, source_modified_at = ?,
                last_seen_at = ?, last_seen_sync = ?, tombstoned = 0
            WHERE record_id = ?
            """,
            (
                revision,
                fingerprint,
                record.source_modified_at,
                now,
                run.sync_id,
                record_id,
            ),
        )
        if revised:
            self._insert_revision(
                record_id,
                revision,
                record,
                now,
                fingerprint,
                normalized_json,
                raw_json,
            )
            self._insert_event(record_id, run.sync_id, "revised", now)
        if revived:
            self._insert_event(record_id, run.sync_id, "revived", now)
        return PageChanges(
            revised=revised,
            unchanged=int(not revised),
            revived=revived,
        )

    def _insert_revision(
        self,
        record_id: str,
        revision: int,
        record: SourceRecord,
        now: str,
        fingerprint: str,
        normalized_json: str,
        raw_json: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO catalog_record_revisions(
                record_id, revision, observed_at, source_modified_at, fingerprint,
                source_url, normalized_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                revision,
                now,
                record.source_modified_at,
                fingerprint,
                record.source_url,
                normalized_json,
                raw_json,
            ),
        )

    def _insert_event(self, record_id: str, sync_id: int, event: str, now: str) -> None:
        self._connection.execute(
            """
            INSERT INTO catalog_record_events(record_id, sync_id, event, observed_at)
            VALUES (?, ?, ?, ?)
            """,
            (record_id, sync_id, event, now),
        )

    def _assert_running(self, run: SyncRun) -> None:
        row = self._connection.execute(
            "SELECT source_id, status FROM catalog_sync_runs WHERE sync_id = ?",
            (run.sync_id,),
        ).fetchone()
        if row is None or row["source_id"] != run.source_id:
            raise ValueError(
                f"catalog sync {run.sync_id} does not belong to {run.source_id}"
            )
        if row["status"] != "running":
            raise ValueError(f"catalog sync {run.sync_id} is not running")


def _record_id(source_id: str, source_key: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{source_key}".encode("utf-8")).hexdigest()
    return f"source-record:{digest}"


def _fingerprint(
    source_url: str | None,
    source_modified_at: str | None,
    normalized_json: str,
    raw_json: str,
) -> str:
    payload = json.dumps(
        [source_url, source_modified_at, normalized_json, raw_json],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: Any, label: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not JSON-serializable: {error}") from error


def _validate_source_id(source_id: str) -> None:
    if not _SOURCE_ID.fullmatch(source_id):
        raise ValueError(
            "source_id must be lowercase ASCII letters, digits, dots, hyphens, or underscores"
        )
