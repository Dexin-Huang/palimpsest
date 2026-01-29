"""Discovery database for manuscript cataloging and tracking."""

from .database import (
    DiscoveryDB,
    Manuscript,
    Opportunity,
    Scholarship,
    OurWork,
    AuditEntry,
)

__all__ = [
    "DiscoveryDB",
    "Manuscript",
    "Opportunity",
    "Scholarship",
    "OurWork",
    "AuditEntry",
]
