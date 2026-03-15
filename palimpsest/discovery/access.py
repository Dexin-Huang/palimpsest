from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .compat import DiscoveryCompatibilityDB, OurWork, Scholarship
from .database import DiscoveryDB
from .records import AuditEntry, Enrichment, Manuscript, Opportunity


class DiscoveryReadAccess(Protocol):
    """Canonical read surface for active discovery code."""

    def get_manuscript(self, manuscript_id: str) -> Manuscript | None: ...

    def get_manuscript_by_shelfmark(self, shelfmark: str) -> Manuscript | None: ...

    def list_manuscripts(
        self,
        status: str | None = None,
        min_obscurity: int | None = None,
        max_obscurity: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Manuscript]: ...

    def get_opportunity(self, manuscript_id: str) -> Opportunity | None: ...

    def list_opportunities(
        self,
        status: str | None = None,
        min_initial_score: int | None = None,
        interesting: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Opportunity]: ...

    def get_enrichment(self, manuscript_id: str) -> Enrichment | None: ...

    def list_unenriched(self, limit: int = 100) -> list[str]: ...

    def get_audit_log(
        self,
        manuscript_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]: ...

    def get_stats(self) -> dict[str, Any]: ...


class DiscoveryWriteAccess(DiscoveryReadAccess, Protocol):
    """Canonical write surface for ingest, triage, and enrichment."""

    def add_manuscript(self, ms: Manuscript, agent: str = "system") -> None: ...

    def update_manuscript(
        self,
        manuscript_id: str,
        updates: dict[str, Any],
        agent: str = "system",
    ) -> bool: ...

    def ensure_opportunity(
        self,
        manuscript_id: str,
        first_seen_at: str | None = None,
        last_seen_at: str | None = None,
        status: str = "new",
    ) -> None: ...

    def update_opportunity(
        self,
        manuscript_id: str,
        updates: dict[str, Any],
    ) -> bool: ...

    def upsert_enrichment(self, enrichment: Enrichment) -> None: ...

    def log_action(
        self,
        manuscript_id: str,
        action: str,
        agent: str,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    def close(self) -> None: ...


class DiscoveryCompatibilityAccess(Protocol):
    """Compatibility surface for quarantined carryover tables."""

    def add_scholarship(self, entry: Scholarship) -> int: ...

    def get_scholarship(self, manuscript_id: str) -> list[Scholarship]: ...

    def add_work(self, work: OurWork, agent: str = "system") -> int: ...

    def update_work(
        self,
        manuscript_id: str,
        page_id: str,
        updates: dict[str, Any],
        agent: str = "system",
    ) -> bool: ...

    def get_work(self, manuscript_id: str) -> list[OurWork]: ...

    def get_work_stats(self, manuscript_id: str) -> dict[str, int]: ...

    def import_jsonl(self, jsonl_path: str | Path, agent: str = "import") -> int: ...

    def export_jsonl(self, output_path: str | Path) -> int: ...

    def close(self) -> None: ...


def _open_discovery_db(db_path: str | Path) -> DiscoveryDB:
    return DiscoveryDB(db_path)


class DiscoveryStore:
    """Canonical access wrapper over the discovery-owned database surface.

    Active discovery code should depend on this wrapper or the protocol types
    above instead of importing ``DiscoveryDB`` directly. Compatibility-only
    tables remain reachable through ``palimpsest.discovery.compat``.
    """

    def __init__(self, db: DiscoveryDB):
        self._db = db

    @classmethod
    def open(cls, db_path: str | Path) -> "DiscoveryStore":
        return cls(_open_discovery_db(db_path))

    @property
    def db_path(self) -> Path:
        return self._db.db_path

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "DiscoveryStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add_manuscript(self, ms: Manuscript, agent: str = "system") -> None:
        self._db.add_manuscript(ms, agent=agent)

    def get_manuscript(self, manuscript_id: str) -> Manuscript | None:
        return self._db.get_manuscript(manuscript_id)

    def get_manuscript_by_shelfmark(self, shelfmark: str) -> Manuscript | None:
        return self._db.get_manuscript_by_shelfmark(shelfmark)

    def update_manuscript(
        self,
        manuscript_id: str,
        updates: dict[str, Any],
        agent: str = "system",
    ) -> bool:
        return self._db.update_manuscript(manuscript_id, updates, agent=agent)

    def list_manuscripts(
        self,
        status: str | None = None,
        min_obscurity: int | None = None,
        max_obscurity: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Manuscript]:
        return self._db.list_manuscripts(
            status=status,
            min_obscurity=min_obscurity,
            max_obscurity=max_obscurity,
            limit=limit,
            offset=offset,
        )

    def ensure_opportunity(
        self,
        manuscript_id: str,
        first_seen_at: str | None = None,
        last_seen_at: str | None = None,
        status: str = "new",
    ) -> None:
        self._db.ensure_opportunity(
            manuscript_id,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            status=status,
        )

    def get_opportunity(self, manuscript_id: str) -> Opportunity | None:
        return self._db.get_opportunity(manuscript_id)

    def update_opportunity(
        self,
        manuscript_id: str,
        updates: dict[str, Any],
    ) -> bool:
        return self._db.update_opportunity(manuscript_id, updates)

    def list_opportunities(
        self,
        status: str | None = None,
        min_initial_score: int | None = None,
        interesting: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Opportunity]:
        return self._db.list_opportunities(
            status=status,
            min_initial_score=min_initial_score,
            interesting=interesting,
            limit=limit,
            offset=offset,
        )

    def upsert_enrichment(self, enrichment: Enrichment) -> None:
        self._db.upsert_enrichment(enrichment)

    def get_enrichment(self, manuscript_id: str) -> Enrichment | None:
        return self._db.get_enrichment(manuscript_id)

    def list_unenriched(self, limit: int = 100) -> list[str]:
        return self._db.list_unenriched(limit=limit)

    def log_action(
        self,
        manuscript_id: str,
        action: str,
        agent: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._db.log_action(
            manuscript_id,
            action,
            agent,
            details=details,
        )

    def get_audit_log(
        self,
        manuscript_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        return self._db.get_audit_log(manuscript_id=manuscript_id, limit=limit)

    def get_stats(self) -> dict[str, Any]:
        return self._db.get_stats()


class DiscoveryCompatibilityStore:
    """Compatibility wrapper over quarantined discovery carryover tables.

    Use this only when older migration or reporting paths still need access to
    scholarship or our_work records. New DB-first discovery code should depend
    on ``DiscoveryStore`` instead.
    """

    def __init__(self, compat: DiscoveryCompatibilityDB):
        self._compat = compat

    @classmethod
    def open(cls, db_path: str | Path) -> "DiscoveryCompatibilityStore":
        return cls(DiscoveryCompatibilityDB(_open_discovery_db(db_path)))

    @property
    def db_path(self) -> Path:
        return self._compat.db_path

    def close(self) -> None:
        self._compat.close()

    def __enter__(self) -> "DiscoveryCompatibilityStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add_scholarship(self, entry: Scholarship) -> int:
        return self._compat.add_scholarship(entry)

    def get_scholarship(self, manuscript_id: str) -> list[Scholarship]:
        return self._compat.get_scholarship(manuscript_id)

    def add_work(self, work: OurWork, agent: str = "system") -> int:
        return self._compat.add_work(work, agent=agent)

    def update_work(
        self,
        manuscript_id: str,
        page_id: str,
        updates: dict[str, Any],
        agent: str = "system",
    ) -> bool:
        return self._compat.update_work(
            manuscript_id,
            page_id,
            updates,
            agent=agent,
        )

    def get_work(self, manuscript_id: str) -> list[OurWork]:
        return self._compat.get_work(manuscript_id)

    def get_work_stats(self, manuscript_id: str) -> dict[str, int]:
        return self._compat.get_work_stats(manuscript_id)

    def import_jsonl(self, jsonl_path: str | Path, agent: str = "import") -> int:
        return self._compat.import_jsonl(jsonl_path, agent=agent)

    def export_jsonl(self, output_path: str | Path) -> int:
        return self._compat.export_jsonl(output_path)


__all__ = [
    "AuditEntry",
    "DiscoveryCompatibilityAccess",
    "DiscoveryCompatibilityStore",
    "DiscoveryReadAccess",
    "DiscoveryStore",
    "DiscoveryWriteAccess",
    "Enrichment",
    "Manuscript",
    "Opportunity",
    "OurWork",
    "Scholarship",
]
