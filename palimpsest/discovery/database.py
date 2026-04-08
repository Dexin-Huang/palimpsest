"""SQLite-based discovery database for manuscript cataloging.

Canonical discovery-owned schema:
- manuscripts
- opportunities
- enrichment
- audit_log

Quarantined carryover schema kept only for compatibility until extraction:
- scholarship
- our_work

Usage:
    from palimpsest.discovery.database import DiscoveryDB

    with DiscoveryDB("discovery/manuscripts.db") as db:
        db.add_manuscript(Manuscript(
            id="vat_pal_lat_1267",
            shelfmark="Pal.lat.1267",
            repository="BAV",
            ...
        ))
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .records import (
    AuditEntry,
    Enrichment,
    Manuscript,
    Opportunity,
    audit_entry_from_row,
    encode_json_field,
    enrichment_from_row,
    manuscript_from_row,
    normalize_manuscript_updates,
    opportunity_from_row,
)
from .stats import collect_discovery_stats

DISCOVERY_CANONICAL_TABLES = (
    "manuscripts",
    "opportunities",
    "enrichment",
    "audit_log",
)

DISCOVERY_QUARANTINED_TABLES = (
    "scholarship",
    "our_work",
)

DISCOVERY_CANONICAL_MANUSCRIPT_FIELDS = (
    "id",
    "shelfmark",
    "repository",
    "iiif_manifest_url",
    "canvas_count",
    "collection",
    "title",
    "date_range",
    "languages",
    "subject_areas",
    "description",
    "source_catalog",
    "discovered_at",
    "updated_at",
)

DISCOVERY_QUARANTINED_MANUSCRIPT_FIELDS = (
    "obscurity_score",
    "wtf_score",
    "priority",
)

class DiscoveryDB:
    """SQLite database for manuscript discovery and tracking.

    Provides CRUD operations with automatic audit logging.
    The canonical discovery-owned surface is limited to manuscripts,
    opportunities, enrichment, and audit. Compatibility-only carryover
    tables live in ``palimpsest.discovery.compat``.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Will be created if not exists.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()

        # Canonical discovery schema: manuscripts + opportunities + enrichment + audit_log.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manuscripts (
                id TEXT PRIMARY KEY,
                shelfmark TEXT UNIQUE NOT NULL,
                repository TEXT NOT NULL,
                iiif_manifest_url TEXT,
                canvas_count INTEGER,

                obscurity_score INTEGER,
                wtf_score INTEGER,
                interest_score INTEGER,

                status TEXT DEFAULT 'discovered',
                priority INTEGER DEFAULT 50,

                collection TEXT,
                title TEXT,
                date_range TEXT,
                languages TEXT,  -- JSON array
                subject_areas TEXT,  -- JSON array
                description TEXT,
                source_catalog_json TEXT,

                discovered_at TEXT,
                updated_at TEXT
            )
        """)
        self._ensure_column("manuscripts", "source_catalog_json", "TEXT")

        # Canonical opportunity tracking for DB-first ingest and triage.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                manuscript_id TEXT PRIMARY KEY REFERENCES manuscripts(id),
                initial_interest BOOLEAN,
                initial_score INTEGER,
                interest_score INTEGER,
                interest_reason TEXT,
                triage_method TEXT,
                triage_model TEXT,
                triage_at TEXT,
                triage_json TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                status TEXT DEFAULT 'new'
            )
        """)

        # Quarantined carryover tables. Keep them readable for compatibility, but
        # treat them as non-canonical when planning extraction or new features.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scholarship (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manuscript_id TEXT NOT NULL REFERENCES manuscripts(id),
                source TEXT NOT NULL,
                has_edition BOOLEAN DEFAULT FALSE,
                has_transcription BOOLEAN DEFAULT FALSE,
                citation TEXT,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS our_work (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manuscript_id TEXT NOT NULL REFERENCES manuscripts(id),
                page_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                page_json_path TEXT,
                transcribed_at TEXT,
                quality_score TEXT,
                notes TEXT,
                UNIQUE(manuscript_id, page_id)
            )
        """)

        # Canonical audit trail for discovery actions.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manuscript_id TEXT,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                agent TEXT NOT NULL,
                details TEXT
            )
        """)

        # Canonical enrichment signals for discovery triage.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS enrichment (
                manuscript_id TEXT PRIMARY KEY REFERENCES manuscripts(id),

                -- Catalog analysis
                catalog_description_words INTEGER,
                manifest_metadata_fields INTEGER,
                has_bibliography BOOLEAN,
                has_incipit BOOLEAN,

                -- External database presence
                in_viaf BOOLEAN,
                viaf_id TEXT,
                in_europeana BOOLEAN,
                in_mirabile BOOLEAN,
                in_pinakes BOOLEAN,

                -- Scholarship signals
                google_scholar_hits INTEGER,
                google_search_hits INTEGER,
                transcription_exists BOOLEAN,
                transcription_source TEXT,

                -- Content signals
                common_text_detected TEXT,
                original_content_signals TEXT,  -- JSON array
                author_famous BOOLEAN,

                -- Computed
                studied_score INTEGER,

                -- Metadata
                enriched_at TEXT,
                enrichment_notes TEXT
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_manuscripts_status
            ON manuscripts(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_manuscripts_obscurity
            ON manuscripts(obscurity_score)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_manuscript
            ON audit_log(manuscript_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_our_work_manuscript
            ON our_work(manuscript_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_opportunities_status
            ON opportunities(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_opportunities_interest
            ON opportunities(interest_score)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_opportunities_first_seen
            ON opportunities(first_seen_at)
        """)

        self.conn.commit()

    def _ensure_column(self, table: str, column: str, column_def: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cursor.fetchall()}
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
            self.conn.commit()

    def close(self):
        """Close database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ==================== Manuscript CRUD ====================

    def add_manuscript(self, ms: Manuscript, agent: str = "system") -> None:
        """Add a new manuscript to the database.

        Args:
            ms: Manuscript record to add
            agent: Who/what is adding this (for audit log)
        """
        now = datetime.utcnow().isoformat() + "Z"
        if not ms.discovered_at:
            ms.discovered_at = now
        ms.updated_at = now

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO manuscripts (
                id, shelfmark, repository, iiif_manifest_url, canvas_count,
                obscurity_score, wtf_score, interest_score,
                status, priority,
                collection, title, date_range, languages, subject_areas, description, source_catalog_json,
                discovered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ms.id, ms.shelfmark, ms.repository, ms.iiif_manifest_url, ms.canvas_count,
            ms.obscurity_score, ms.wtf_score, ms.interest_score,
            ms.status, ms.priority,
            ms.collection, ms.title, ms.date_range,
            encode_json_field(ms.languages),
            encode_json_field(ms.subject_areas),
            ms.description,
            encode_json_field(ms.source_catalog),
            ms.discovered_at, ms.updated_at
        ))
        self.conn.commit()

        self._log_action(ms.id, "discovered", agent, {"shelfmark": ms.shelfmark})

    def get_manuscript(self, manuscript_id: str) -> Optional[Manuscript]:
        """Get a manuscript by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM manuscripts WHERE id = ?", (manuscript_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return manuscript_from_row(row)

    def get_manuscript_by_shelfmark(self, shelfmark: str) -> Optional[Manuscript]:
        """Get a manuscript by shelfmark."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM manuscripts WHERE shelfmark = ?", (shelfmark,))
        row = cursor.fetchone()
        if not row:
            return None
        return manuscript_from_row(row)

    def update_manuscript(
        self,
        manuscript_id: str,
        updates: Dict[str, Any],
        agent: str = "system"
    ) -> bool:
        """Update manuscript fields.

        Args:
            manuscript_id: ID of manuscript to update
            updates: Dict of field names to new values
            agent: Who/what is updating (for audit log)

        Returns:
            True if manuscript was found and updated
        """
        if not updates:
            return False

        updates = normalize_manuscript_updates(updates)

        updates["updated_at"] = datetime.utcnow().isoformat() + "Z"

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [manuscript_id]

        cursor = self.conn.cursor()
        cursor.execute(
            f"UPDATE manuscripts SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()

        if cursor.rowcount > 0:
            self._log_action(manuscript_id, "updated", agent, updates)
            return True
        return False

    def list_manuscripts(
        self,
        status: Optional[str] = None,
        min_obscurity: Optional[int] = None,
        max_obscurity: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Manuscript]:
        """List manuscripts with optional filters."""
        query = "SELECT * FROM manuscripts WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if min_obscurity is not None:
            query += " AND obscurity_score >= ?"
            params.append(min_obscurity)
        if max_obscurity is not None:
            query += " AND obscurity_score <= ?"
            params.append(max_obscurity)

        query += " ORDER BY priority DESC, obscurity_score DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return [manuscript_from_row(row) for row in cursor.fetchall()]

    def get_next_priority(
        self,
        min_obscurity: int = 7,
        status: str = "discovered"
    ) -> Optional[Manuscript]:
        """Legacy prioritization helper over quarantined manuscript fields.

        Args:
            min_obscurity: Minimum obscurity score (default 7)
            status: Status to filter by (default "discovered")

        Returns:
            Highest priority manuscript matching criteria, or None
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM manuscripts
            WHERE status = ? AND obscurity_score >= ?
            ORDER BY priority DESC, obscurity_score DESC, wtf_score DESC
            LIMIT 1
        """, (status, min_obscurity))
        row = cursor.fetchone()
        if not row:
            return None
        return manuscript_from_row(row)

    # ==================== Opportunities CRUD ====================

    def ensure_opportunity(
        self,
        manuscript_id: str,
        first_seen_at: Optional[str] = None,
        last_seen_at: Optional[str] = None,
        status: str = "new",
    ) -> None:
        """Create an opportunity record if missing, and update last_seen_at."""
        now = datetime.utcnow().isoformat() + "Z"
        first_seen_at = first_seen_at or now
        last_seen_at = last_seen_at or now

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO opportunities (
                manuscript_id, first_seen_at, last_seen_at, status
            ) VALUES (?, ?, ?, ?)
        """, (manuscript_id, first_seen_at, last_seen_at, status))

        cursor.execute("""
            UPDATE opportunities
            SET last_seen_at = ?
            WHERE manuscript_id = ?
        """, (last_seen_at, manuscript_id))
        self.conn.commit()

    def get_opportunity(self, manuscript_id: str) -> Optional[Opportunity]:
        """Get opportunity by manuscript ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM opportunities WHERE manuscript_id = ?",
            (manuscript_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return opportunity_from_row(row)

    def update_opportunity(
        self,
        manuscript_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update opportunity fields."""
        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [manuscript_id]

        cursor = self.conn.cursor()
        cursor.execute(
            f"UPDATE opportunities SET {set_clause} WHERE manuscript_id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_opportunities(
        self,
        status: Optional[str] = None,
        min_initial_score: Optional[int] = None,
        interesting: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Opportunity]:
        """List opportunities with optional filters."""
        query = "SELECT * FROM opportunities WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if min_initial_score is not None:
            query += " AND initial_score >= ?"
            params.append(min_initial_score)
        if interesting is not None:
            query += " AND initial_interest = ?"
            params.append(1 if interesting else 0)

        query += " ORDER BY first_seen_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [opportunity_from_row(row) for row in rows]

    # ==================== Enrichment CRUD ====================

    def upsert_enrichment(self, enrichment: Enrichment) -> None:
        """Insert or update enrichment record."""
        now = datetime.utcnow().isoformat() + "Z"
        enrichment.enriched_at = now

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO enrichment (
                manuscript_id,
                catalog_description_words, manifest_metadata_fields,
                has_bibliography, has_incipit,
                in_viaf, viaf_id, in_europeana, in_mirabile, in_pinakes,
                google_scholar_hits, google_search_hits,
                transcription_exists, transcription_source,
                common_text_detected, original_content_signals, author_famous,
                studied_score, enriched_at, enrichment_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            enrichment.manuscript_id,
            enrichment.catalog_description_words,
            enrichment.manifest_metadata_fields,
            enrichment.has_bibliography,
            enrichment.has_incipit,
            enrichment.in_viaf,
            enrichment.viaf_id,
            enrichment.in_europeana,
            enrichment.in_mirabile,
            enrichment.in_pinakes,
            enrichment.google_scholar_hits,
            enrichment.google_search_hits,
            enrichment.transcription_exists,
            enrichment.transcription_source,
            enrichment.common_text_detected,
            encode_json_field(enrichment.original_content_signals),
            enrichment.author_famous,
            enrichment.studied_score,
            enrichment.enriched_at,
            enrichment.enrichment_notes,
        ))
        self.conn.commit()

    def get_enrichment(self, manuscript_id: str) -> Optional[Enrichment]:
        """Get enrichment record by manuscript ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM enrichment WHERE manuscript_id = ?",
            (manuscript_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return enrichment_from_row(row)

    def list_unenriched(self, limit: int = 100) -> List[str]:
        """List manuscript IDs that don't have enrichment records."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT m.id FROM manuscripts m
            LEFT JOIN enrichment e ON m.id = e.manuscript_id
            WHERE e.manuscript_id IS NULL
            LIMIT ?
        """, (limit,))
        return [row["id"] for row in cursor.fetchall()]

    # ==================== Audit Log ====================

    def _log_action(
        self,
        manuscript_id: str,
        action: str,
        agent: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Internal: Add an audit log entry."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO audit_log (manuscript_id, timestamp, action, agent, details)
            VALUES (?, ?, ?, ?, ?)
        """, (
            manuscript_id,
            datetime.utcnow().isoformat() + "Z",
            action,
            agent,
            json.dumps(details) if details else None
        ))
        self.conn.commit()

    def log_action(
        self,
        manuscript_id: str,
        action: str,
        agent: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add an audit log entry (public API)."""
        self._log_action(manuscript_id, action, agent, details)

    def get_audit_log(
        self,
        manuscript_id: Optional[str] = None,
        limit: int = 100
    ) -> List[AuditEntry]:
        """Get audit log entries."""
        cursor = self.conn.cursor()

        if manuscript_id:
            cursor.execute("""
                SELECT * FROM audit_log
                WHERE manuscript_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (manuscript_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM audit_log
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

        return [audit_entry_from_row(row) for row in cursor.fetchall()]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        return collect_discovery_stats(self.conn)
