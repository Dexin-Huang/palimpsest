from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import DiscoveryDB
from .records import Manuscript


@dataclass
class Scholarship:
    """Quarantined carryover record outside the canonical discovery schema."""

    id: int | None = None
    manuscript_id: str = ""
    source: str = ""  # e.g., "Schuba 1981"
    has_edition: bool = False
    has_transcription: bool = False
    citation: str | None = None
    notes: str | None = None


@dataclass
class OurWork:
    """Quarantined carryover record outside the canonical discovery schema."""

    id: int | None = None
    manuscript_id: str = ""
    page_id: str = ""  # e.g., "f020r"
    status: str = "pending"  # pending/in_progress/complete
    page_json_path: str | None = None
    transcribed_at: str | None = None
    quality_score: str | None = None
    notes: str | None = None


class DiscoveryCompatibilityDB:
    """Compatibility-only wrapper over quarantined discovery carryover tables.

    This keeps scholarship, our_work, and JSONL bridge code out of the
    canonical discovery DB surface without changing the underlying schema.
    """

    def __init__(self, db: DiscoveryDB):
        self._db = db

    @property
    def db_path(self) -> Path:
        return self._db.db_path

    def close(self) -> None:
        self._db.close()

    def add_scholarship(self, entry: Scholarship) -> int:
        cursor = self._db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO scholarship (
                manuscript_id, source, has_edition, has_transcription, citation, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry.manuscript_id,
                entry.source,
                entry.has_edition,
                entry.has_transcription,
                entry.citation,
                entry.notes,
            ),
        )
        self._db.conn.commit()
        return cursor.lastrowid

    def get_scholarship(self, manuscript_id: str) -> list[Scholarship]:
        cursor = self._db.conn.cursor()
        cursor.execute(
            "SELECT * FROM scholarship WHERE manuscript_id = ?",
            (manuscript_id,),
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

    def add_work(self, work: OurWork, agent: str = "system") -> int:
        cursor = self._db.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO our_work (
                manuscript_id, page_id, status, page_json_path,
                transcribed_at, quality_score, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                work.manuscript_id,
                work.page_id,
                work.status,
                work.page_json_path,
                work.transcribed_at,
                work.quality_score,
                work.notes,
            ),
        )
        self._db.conn.commit()

        self._db.log_action(
            work.manuscript_id,
            f"work_{work.status}",
            agent,
            {"page_id": work.page_id, "status": work.status},
        )
        return cursor.lastrowid

    def update_work(
        self,
        manuscript_id: str,
        page_id: str,
        updates: dict[str, Any],
        agent: str = "system",
    ) -> bool:
        if not updates:
            return False

        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        values = list(updates.values()) + [manuscript_id, page_id]

        cursor = self._db.conn.cursor()
        cursor.execute(
            f"UPDATE our_work SET {set_clause} WHERE manuscript_id = ? AND page_id = ?",
            values,
        )
        self._db.conn.commit()

        if cursor.rowcount <= 0:
            return False

        self._db.log_action(
            manuscript_id,
            "work_updated",
            agent,
            {"page_id": page_id, **updates},
        )
        return True

    def get_work(self, manuscript_id: str) -> list[OurWork]:
        cursor = self._db.conn.cursor()
        cursor.execute(
            "SELECT * FROM our_work WHERE manuscript_id = ? ORDER BY page_id",
            (manuscript_id,),
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

    def get_work_stats(self, manuscript_id: str) -> dict[str, int]:
        cursor = self._db.conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM our_work
            WHERE manuscript_id = ?
            GROUP BY status
            """,
            (manuscript_id,),
        )

        stats = {"pending": 0, "in_progress": 0, "complete": 0}
        for row in cursor.fetchall():
            stats[row["status"]] = row["count"]
        return stats

    def import_jsonl(self, jsonl_path: str | Path, agent: str = "import") -> int:
        """Compatibility bridge for the older JSONL discovery format."""

        path = Path(jsonl_path)
        count = 0

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)
                iiif = data.get("iiif") or {}
                scholarship = data.get("scholarship") or {}
                discovery = data.get("discovery") or {}
                content = data.get("content") or {}
                physical = data.get("physical") or {}

                manuscript = Manuscript(
                    id=data.get("manuscript_id", data.get("id")),
                    shelfmark=data["shelfmark"],
                    repository=data.get("repository", "unknown"),
                    iiif_manifest_url=iiif.get("manifest_url"),
                    canvas_count=iiif.get("canvas_count"),
                    obscurity_score=scholarship.get("obscurity_score"),
                    wtf_score=discovery.get("wtf_factor"),
                    status=data.get("status", "discovered"),
                    collection=data.get("collection"),
                    title=content.get("title"),
                    date_range=physical.get("date_range"),
                    languages=content.get("languages"),
                    subject_areas=content.get("subject_areas"),
                    description=content.get("description"),
                    source_catalog=data.get("source_catalog"),
                    discovered_at=discovery.get("discovered_date"),
                )

                try:
                    self._db.add_manuscript(manuscript, agent=agent)
                    count += 1

                    for entry in scholarship.get("catalog_entries", []):
                        self.add_scholarship(
                            Scholarship(
                                manuscript_id=manuscript.id,
                                source=entry.get("source", "unknown"),
                                has_edition=False,
                                has_transcription=scholarship.get(
                                    "transcriptions_exist",
                                    False,
                                ),
                                citation=entry.get("citation"),
                            )
                        )

                    for entry in (data.get("our_work") or {}).get("findings", []):
                        self.add_work(
                            OurWork(
                                manuscript_id=manuscript.id,
                                page_id=entry.get("folio", "unknown"),
                                status="complete" if entry.get("type") else "pending",
                                notes=entry.get("description"),
                            )
                        )

                    for entry in data.get("audit_log", []):
                        self._db.log_action(
                            manuscript.id,
                            entry.get("action", "imported"),
                            entry.get("agent", "import"),
                            {"original_details": entry.get("details")},
                        )
                except sqlite3.IntegrityError:
                    pass

        return count

    def export_jsonl(self, output_path: str | Path) -> int:
        """Compatibility bridge for the older JSONL discovery format."""

        path = Path(output_path)
        count = 0

        with path.open("w", encoding="utf-8") as handle:
            for manuscript in self._db.list_manuscripts(limit=10000):
                data = {
                    "manuscript_id": manuscript.id,
                    "shelfmark": manuscript.shelfmark,
                    "repository": manuscript.repository,
                    "iiif": {
                        "manifest_url": manuscript.iiif_manifest_url,
                        "canvas_count": manuscript.canvas_count,
                    }
                    if manuscript.iiif_manifest_url
                    else None,
                    "status": manuscript.status,
                    "scholarship": {
                        "obscurity_score": manuscript.obscurity_score,
                    },
                    "discovery": {
                        "wtf_factor": manuscript.wtf_score,
                        "discovered_date": manuscript.discovered_at,
                    },
                    "content": {
                        "title": manuscript.title,
                        "languages": manuscript.languages,
                        "subject_areas": manuscript.subject_areas,
                        "description": manuscript.description,
                    },
                    "source_catalog": manuscript.source_catalog,
                    "physical": {
                        "date_range": manuscript.date_range,
                    },
                }

                opportunity = self._db.get_opportunity(manuscript.id)
                if opportunity:
                    data["opportunity"] = {
                        "initial_interest": opportunity.initial_interest,
                        "initial_score": opportunity.initial_score,
                        "interest_score": opportunity.interest_score,
                        "interest_reason": opportunity.interest_reason,
                        "triage_method": opportunity.triage_method,
                        "triage_model": opportunity.triage_model,
                        "triage_at": opportunity.triage_at,
                        "first_seen_at": opportunity.first_seen_at,
                        "last_seen_at": opportunity.last_seen_at,
                        "status": opportunity.status,
                    }
                    if opportunity.triage_json:
                        data["opportunity"]["triage_json"] = opportunity.triage_json

                data = {key: value for key, value in data.items() if value is not None}
                handle.write(json.dumps(data, ensure_ascii=False) + "\n")
                count += 1

        return count


__all__ = [
    "DiscoveryCompatibilityDB",
    "OurWork",
    "Scholarship",
]
