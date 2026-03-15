"""Canonical discovery package surface.

This package root exports the DB-first discovery models and triage helpers that
remain part of the active product path. ``DiscoveryStore`` is the preferred
internal access boundary for discovery-owned code. Compatibility-only carryover
operations live in ``palimpsest.discovery.compat`` and should not be added back
to this root.
"""

from .access import DiscoveryReadAccess, DiscoveryStore, DiscoveryWriteAccess
from .database import (
    DISCOVERY_CANONICAL_MANUSCRIPT_FIELDS,
    DISCOVERY_CANONICAL_TABLES,
    DISCOVERY_QUARANTINED_MANUSCRIPT_FIELDS,
    DISCOVERY_QUARANTINED_TABLES,
    DiscoveryDB,
)
from .records import AuditEntry, Enrichment, Manuscript, Opportunity

from .triage import (
    TriageResult,
    build_triage_metadata,
    combined_interest_score,
    save_triage_result,
    triage_manuscript,
    triage_from_db,
    load_triage_prompt,
)

__all__ = [
    # Database
    "DISCOVERY_CANONICAL_MANUSCRIPT_FIELDS",
    "DISCOVERY_CANONICAL_TABLES",
    "DISCOVERY_QUARANTINED_MANUSCRIPT_FIELDS",
    "DISCOVERY_QUARANTINED_TABLES",
    "DiscoveryReadAccess",
    "DiscoveryStore",
    "DiscoveryWriteAccess",
    "DiscoveryDB",
    "Enrichment",
    "Manuscript",
    "Opportunity",
    "AuditEntry",
    # Triage
    "TriageResult",
    "build_triage_metadata",
    "combined_interest_score",
    "save_triage_result",
    "triage_manuscript",
    "triage_from_db",
    "load_triage_prompt",
]
