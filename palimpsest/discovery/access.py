from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .compat import DiscoveryCompatibilityDB, OurWork, Scholarship
from .database import DiscoveryDB
from .records import AuditEntry, Enrichment, Manuscript, Opportunity


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


class DiscoveryCompatibilityStore:
    """Compatibility wrapper over quarantined discovery carryover tables.

    Use this only when older migration or reporting paths still need access to
    scholarship or our_work records. New DB-first discovery code should depend
    on ``DiscoveryDB`` directly.
    """

    def __init__(self, compat: DiscoveryCompatibilityDB):
        self._compat = compat

    @classmethod
    def open(cls, db_path: str | Path) -> "DiscoveryCompatibilityStore":
        return cls(DiscoveryCompatibilityDB(DiscoveryDB(db_path)))

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
    "Enrichment",
    "Manuscript",
    "Opportunity",
    "OurWork",
    "Scholarship",
]
