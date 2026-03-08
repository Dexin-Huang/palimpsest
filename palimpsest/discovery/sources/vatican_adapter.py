from __future__ import annotations

from dataclasses import dataclass

from .adapter import DiscoverySourceAdapter, SourceCollection, SourceDocumentRef
from .vatican_listings import (
    ListingItem,
    get_available_collections,
    scrape_collection_listing,
)


def _to_source_ref(item: ListingItem) -> SourceDocumentRef:
    return SourceDocumentRef(
        source_id="vatican",
        collection=item.collection,
        shelfmark=item.shelfmark,
        manuscript_id=item.manuscript_id,
        manifest_url=item.manifest_url,
        viewer_url=item.viewer_url,
        thumbnail_url=item.thumbnail_url,
        quality=item.quality,
    )


@dataclass
class VaticanSourceAdapter(DiscoverySourceAdapter):
    """Adapter for DigiVatLib listing pages and IIIF manifests."""

    source_id: str = "vatican"
    label: str = "Biblioteca Apostolica Vaticana"

    def list_collections(self) -> list[SourceCollection]:
        collections = get_available_collections()
        return [
            SourceCollection(
                source_id=self.source_id,
                key=item["name"],
                label=item["label"],
                listing_url=item["url"],
                automation_fit=5,
                north_star_fit=4,
                access="public_open_iiif",
            )
            for item in collections
        ]

    def scrape_collection(self, collection: str, **kwargs) -> list[SourceDocumentRef]:
        items = scrape_collection_listing(collection, **kwargs)
        return [_to_source_ref(item) for item in items]
