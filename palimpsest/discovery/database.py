"""SQLite-based discovery database for manuscript cataloging.

This module provides a structured database for tracking:
- Discovered manuscripts (from IIIF crawls, catalog searches)
- Scholarship status (existing editions, transcriptions)
- Our work (transcription progress, page JSON files)
- Audit log (all actions taken on manuscripts)

Usage:
    from palimpsest.discovery import DiscoveryDB

    db = DiscoveryDB("discovery/manuscripts.db")
    db.add_manuscript(Manuscript(
        id="vat_pal_lat_1267",
        shelfmark="Pal.lat.1267",
        repository="BAV",
        ...
    ))
"""

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator


@dataclass
class Manuscript:
    """Core manuscript record."""

    id: str  # e.g., "vat_pal_lat_1267"
    shelfmark: str  # e.g., "Pal.lat.1267"
    repository: str  # e.g., "BAV"

    # IIIF info
    iiif_manifest_url: Optional[str] = None
    canvas_count: Optional[int] = None

    # Scores
    obscurity_score: Optional[int] = None  # 1-10
    wtf_score: Optional[int] = None  # 1-10
    interest_score: Optional[int] = None  # computed or manual

    # Status
    status: str = "discovered"  # discovered/analyzing/transcribed/scanlated
    priority: int = 50  # 1-100

    # Metadata
    collection: Optional[str] = None
    title: Optional[str] = None
    date_range: Optional[str] = None
    languages: Optional[List[str]] = None
    subject_areas: Optional[List[str]] = None
    description: Optional[str] = None

    # Timestamps
    discovered_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Opportunity:
    """Triage record for discovery opportunities."""

    manuscript_id: str
    initial_interest: Optional[bool] = None
    initial_score: Optional[int] = None
    interest_score: Optional[int] = None
    interest_reason: Optional[str] = None
    triage_method: Optional[str] = None  # metadata_only, gemini_flash, human
    triage_model: Optional[str] = None
    triage_at: Optional[str] = None
    triage_json: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    status: str = "new"


@dataclass
class Scholarship:
    """Scholarship tracking for a manuscript."""

    id: Optional[int] = None
    manuscript_id: str = ""
    source: str = ""  # e.g., "Schuba 1981"
    has_edition: bool = False
    has_transcription: bool = False
    citation: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class OurWork:
    """Our work tracking for a manuscript page."""

    id: Optional[int] = None
    manuscript_id: str = ""
    page_id: str = ""  # e.g., "f020r"
    status: str = "pending"  # pending/in_progress/complete
    page_json_path: Optional[str] = None
    transcribed_at: Optional[str] = None
    quality_score: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class AuditEntry:
    """Audit log entry."""

    id: Optional[int] = None
    manuscript_id: str = ""
    timestamp: str = ""
    action: str = ""  # discovered/analyzed/transcribed/reconstructed
    agent: str = ""  # human/gemini-3-flash/etc.
    details: Optional[str] = None  # JSON string


class DiscoveryDB:
    """SQLite database for manuscript discovery and tracking.

    Provides CRUD operations with automatic audit logging.
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

        # Manuscripts table
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

                discovered_at TEXT,
                updated_at TEXT
            )
        """)

        # Opportunities table (triage + digitization tracking)
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

        # Scholarship table
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

        # Our work table
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

        # Audit log table
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
                collection, title, date_range, languages, subject_areas, description,
                discovered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ms.id, ms.shelfmark, ms.repository, ms.iiif_manifest_url, ms.canvas_count,
            ms.obscurity_score, ms.wtf_score, ms.interest_score,
            ms.status, ms.priority,
            ms.collection, ms.title, ms.date_range,
            json.dumps(ms.languages) if ms.languages else None,
            json.dumps(ms.subject_areas) if ms.subject_areas else None,
            ms.description,
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
        return self._row_to_manuscript(row)

    def get_manuscript_by_shelfmark(self, shelfmark: str) -> Optional[Manuscript]:
        """Get a manuscript by shelfmark."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM manuscripts WHERE shelfmark = ?", (shelfmark,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_manuscript(row)

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

        # Handle JSON fields
        if "languages" in updates and isinstance(updates["languages"], list):
            updates["languages"] = json.dumps(updates["languages"])
        if "subject_areas" in updates and isinstance(updates["subject_areas"], list):
            updates["subject_areas"] = json.dumps(updates["subject_areas"])

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
        return [self._row_to_manuscript(row) for row in cursor.fetchall()]

    def get_next_priority(
        self,
        min_obscurity: int = 7,
        status: str = "discovered"
    ) -> Optional[Manuscript]:
        """Get the next highest-priority manuscript to work on.

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
        return self._row_to_manuscript(row)

    def _row_to_manuscript(self, row: sqlite3.Row) -> Manuscript:
        """Convert database row to Manuscript object."""
        return Manuscript(
            id=row["id"],
            shelfmark=row["shelfmark"],
            repository=row["repository"],
            iiif_manifest_url=row["iiif_manifest_url"],
            canvas_count=row["canvas_count"],
            obscurity_score=row["obscurity_score"],
            wtf_score=row["wtf_score"],
            interest_score=row["interest_score"],
            status=row["status"],
            priority=row["priority"],
            collection=row["collection"],
            title=row["title"],
            date_range=row["date_range"],
            languages=json.loads(row["languages"]) if row["languages"] else None,
            subject_areas=json.loads(row["subject_areas"]) if row["subject_areas"] else None,
            description=row["description"],
            discovered_at=row["discovered_at"],
            updated_at=row["updated_at"],
        )

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
        return Opportunity(
            manuscript_id=row["manuscript_id"],
            initial_interest=bool(row["initial_interest"]) if row["initial_interest"] is not None else None,
            initial_score=row["initial_score"],
            interest_score=row["interest_score"],
            interest_reason=row["interest_reason"],
            triage_method=row["triage_method"],
            triage_model=row["triage_model"],
            triage_at=row["triage_at"],
            triage_json=row["triage_json"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            status=row["status"] or "new",
        )

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
        return [
            Opportunity(
                manuscript_id=r["manuscript_id"],
                initial_interest=bool(r["initial_interest"]) if r["initial_interest"] is not None else None,
                initial_score=r["initial_score"],
                interest_score=r["interest_score"],
                interest_reason=r["interest_reason"],
                triage_method=r["triage_method"],
                triage_model=r["triage_model"],
                triage_at=r["triage_at"],
                triage_json=r["triage_json"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
                status=r["status"] or "new",
            )
            for r in rows
        ]

    # ==================== Scholarship CRUD ====================

    def add_scholarship(self, entry: Scholarship) -> int:
        """Add a scholarship entry. Returns the new ID."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO scholarship (
                manuscript_id, source, has_edition, has_transcription, citation, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            entry.manuscript_id, entry.source, entry.has_edition,
            entry.has_transcription, entry.citation, entry.notes
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_scholarship(self, manuscript_id: str) -> List[Scholarship]:
        """Get all scholarship entries for a manuscript."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM scholarship WHERE manuscript_id = ?",
            (manuscript_id,)
        )
        return [
            Scholarship(
                id=row["id"],
                manuscript_id=row["manuscript_id"],
                source=row["source"],
                has_edition=bool(row["has_edition"]),
                has_transcription=bool(row["has_transcription"]),
                citation=row["citation"],
                notes=row["notes"],
            )
            for row in cursor.fetchall()
        ]

    # ==================== Our Work CRUD ====================

    def add_work(self, work: OurWork, agent: str = "system") -> int:
        """Add a work entry for a page. Returns the new ID."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO our_work (
                manuscript_id, page_id, status, page_json_path,
                transcribed_at, quality_score, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            work.manuscript_id, work.page_id, work.status, work.page_json_path,
            work.transcribed_at, work.quality_score, work.notes
        ))
        self.conn.commit()

        self._log_action(
            work.manuscript_id,
            f"work_{work.status}",
            agent,
            {"page_id": work.page_id, "status": work.status}
        )

        return cursor.lastrowid

    def update_work(
        self,
        manuscript_id: str,
        page_id: str,
        updates: Dict[str, Any],
        agent: str = "system"
    ) -> bool:
        """Update a work entry."""
        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [manuscript_id, page_id]

        cursor = self.conn.cursor()
        cursor.execute(
            f"UPDATE our_work SET {set_clause} WHERE manuscript_id = ? AND page_id = ?",
            values
        )
        self.conn.commit()

        if cursor.rowcount > 0:
            self._log_action(manuscript_id, "work_updated", agent, {
                "page_id": page_id, **updates
            })
            return True
        return False

    def get_work(self, manuscript_id: str) -> List[OurWork]:
        """Get all work entries for a manuscript."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM our_work WHERE manuscript_id = ? ORDER BY page_id",
            (manuscript_id,)
        )
        return [
            OurWork(
                id=row["id"],
                manuscript_id=row["manuscript_id"],
                page_id=row["page_id"],
                status=row["status"],
                page_json_path=row["page_json_path"],
                transcribed_at=row["transcribed_at"],
                quality_score=row["quality_score"],
                notes=row["notes"],
            )
            for row in cursor.fetchall()
        ]

    def get_work_stats(self, manuscript_id: str) -> Dict[str, int]:
        """Get work progress stats for a manuscript."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM our_work
            WHERE manuscript_id = ?
            GROUP BY status
        """, (manuscript_id,))

        stats = {"pending": 0, "in_progress": 0, "complete": 0}
        for row in cursor.fetchall():
            stats[row["status"]] = row["count"]
        return stats

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

        return [
            AuditEntry(
                id=row["id"],
                manuscript_id=row["manuscript_id"],
                timestamp=row["timestamp"],
                action=row["action"],
                agent=row["agent"],
                details=row["details"],
            )
            for row in cursor.fetchall()
        ]

    # ==================== Import/Export ====================

    def import_jsonl(self, jsonl_path: str | Path, agent: str = "import") -> int:
        """Import manuscripts from JSONL file.

        Expects each line to be a JSON object with at least:
        - manuscript_id (used as id)
        - shelfmark
        - repository

        Returns:
            Number of manuscripts imported
        """
        path = Path(jsonl_path)
        count = 0

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                # Map from JSONL format to Manuscript
                ms = Manuscript(
                    id=data.get("manuscript_id", data.get("id")),
                    shelfmark=data["shelfmark"],
                    repository=data.get("repository", "unknown"),
                    iiif_manifest_url=data.get("iiif", {}).get("manifest_url"),
                    canvas_count=data.get("iiif", {}).get("canvas_count"),
                    obscurity_score=data.get("scholarship", {}).get("obscurity_score"),
                    wtf_score=data.get("discovery", {}).get("wtf_factor"),
                    status=data.get("status", "discovered"),
                    collection=data.get("collection"),
                    title=data.get("content", {}).get("title"),
                    date_range=data.get("physical", {}).get("date_range"),
                    languages=data.get("content", {}).get("languages"),
                    subject_areas=data.get("content", {}).get("subject_areas"),
                    description=data.get("content", {}).get("description"),
                    discovered_at=data.get("discovery", {}).get("discovered_date"),
                )

                try:
                    self.add_manuscript(ms, agent=agent)
                    count += 1

                    # Import scholarship entries
                    for entry in data.get("scholarship", {}).get("catalog_entries", []):
                        self.add_scholarship(Scholarship(
                            manuscript_id=ms.id,
                            source=entry.get("source", "unknown"),
                            has_edition=False,
                            has_transcription=data.get("scholarship", {}).get(
                                "transcriptions_exist", False
                            ),
                            citation=entry.get("citation"),
                        ))

                    # Import our work entries
                    for entry in data.get("our_work", {}).get("findings", []):
                        self.add_work(OurWork(
                            manuscript_id=ms.id,
                            page_id=entry.get("folio", "unknown"),
                            status="complete" if entry.get("type") else "pending",
                            notes=entry.get("description"),
                        ))

                    # Import audit log entries
                    for entry in data.get("audit_log", []):
                        self._log_action(
                            ms.id,
                            entry.get("action", "imported"),
                            entry.get("agent", "import"),
                            {"original_details": entry.get("details")}
                        )

                except sqlite3.IntegrityError:
                    # Already exists, skip
                    pass

        return count

    def export_jsonl(self, output_path: str | Path) -> int:
        """Export all manuscripts to JSONL file."""
        path = Path(output_path)
        count = 0

        with open(path, "w", encoding="utf-8") as f:
            for ms in self.list_manuscripts(limit=10000):
                data = {
                    "manuscript_id": ms.id,
                    "shelfmark": ms.shelfmark,
                    "repository": ms.repository,
                    "iiif": {
                        "manifest_url": ms.iiif_manifest_url,
                        "canvas_count": ms.canvas_count,
                    } if ms.iiif_manifest_url else None,
                    "status": ms.status,
                    "scholarship": {
                        "obscurity_score": ms.obscurity_score,
                    },
                    "discovery": {
                        "wtf_factor": ms.wtf_score,
                        "discovered_date": ms.discovered_at,
                    },
                    "content": {
                        "title": ms.title,
                        "languages": ms.languages,
                        "subject_areas": ms.subject_areas,
                        "description": ms.description,
                    },
                    "physical": {
                        "date_range": ms.date_range,
                    },
                }

                opp = self.get_opportunity(ms.id)
                if opp:
                    data["opportunity"] = {
                        "initial_interest": opp.initial_interest,
                        "initial_score": opp.initial_score,
                        "interest_score": opp.interest_score,
                        "interest_reason": opp.interest_reason,
                        "triage_method": opp.triage_method,
                        "triage_model": opp.triage_model,
                        "triage_at": opp.triage_at,
                        "first_seen_at": opp.first_seen_at,
                        "last_seen_at": opp.last_seen_at,
                        "status": opp.status,
                    }
                    if opp.triage_json:
                        data["opportunity"]["triage_json"] = opp.triage_json

                # Remove None values
                data = {k: v for k, v in data.items() if v is not None}

                f.write(json.dumps(data, ensure_ascii=False) + "\n")
                count += 1

        return count

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        cursor = self.conn.cursor()

        # Manuscript counts by status
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM manuscripts
            GROUP BY status
        """)
        status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

        # Total manuscripts
        cursor.execute("SELECT COUNT(*) as count FROM manuscripts")
        total = cursor.fetchone()["count"]

        # Obscurity distribution
        cursor.execute("""
            SELECT obscurity_score, COUNT(*) as count
            FROM manuscripts
            WHERE obscurity_score IS NOT NULL
            GROUP BY obscurity_score
            ORDER BY obscurity_score
        """)
        obscurity_dist = {row["obscurity_score"]: row["count"] for row in cursor.fetchall()}

        # Work progress
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM our_work
            GROUP BY status
        """)
        work_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

        # Recent activity
        cursor.execute("""
            SELECT action, COUNT(*) as count
            FROM audit_log
            WHERE timestamp > datetime('now', '-7 days')
            GROUP BY action
        """)
        recent_actions = {row["action"]: row["count"] for row in cursor.fetchall()}

        return {
            "total_manuscripts": total,
            "by_status": status_counts,
            "by_obscurity": obscurity_dist,
            "work_progress": work_counts,
            "recent_actions": recent_actions,
        }
