from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceCollection:
    """One browseable collection exposed by a source adapter."""

    source_id: str
    key: str
    label: str
    listing_url: str | None = None
    notes: str | None = None
    automation_fit: int | None = None
    north_star_fit: int | None = None
    access: str | None = None


@dataclass(frozen=True)
class SourceDocumentRef:
    """Minimal cross-source manuscript reference for discovery intake."""

    source_id: str
    collection: str
    shelfmark: str
    manuscript_id: str
    manifest_url: str | None = None
    viewer_url: str | None = None
    thumbnail_url: str | None = None
    quality: str | None = None
    source_catalog: dict | None = None
    extra: dict = field(default_factory=dict)

    def as_record(self) -> dict:
        payload = asdict(self)
        payload["repository"] = self.source_id
        return payload


class DiscoverySourceAdapter(Protocol):
    """Minimal adapter contract for a digitized manuscript source."""

    source_id: str
    label: str
    automation_fit: int | None
    north_star_fit: int | None
    access: str | None

    def list_collections(self) -> list[SourceCollection]:
        """Return available top-level collections."""

    def scrape_collection(self, collection: str, **kwargs) -> list[SourceDocumentRef]:
        """Return manuscript refs for one collection."""
