"""Discovery database and triage for manuscript cataloging and tracking."""

from .database import (
    DiscoveryDB,
    Manuscript,
    Opportunity,
    Scholarship,
    OurWork,
    AuditEntry,
)

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
    "DiscoveryDB",
    "Manuscript",
    "Opportunity",
    "Scholarship",
    "OurWork",
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
