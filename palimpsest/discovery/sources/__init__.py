"""Discovery source modules and adapter primitives for digital repositories."""

from .adapter import DiscoverySourceAdapter, SourceCollection, SourceDocumentRef
from .gallica_adapter import GallicaSourceAdapter
from .idp_adapter import IDPSourceAdapter
from .iiif_batch import fetch_manifests_async, fetch_manifests_sync
from .registry import get_source_adapter, get_source_adapters
from .vatican_adapter import VaticanSourceAdapter
from .vatican_listings import get_available_collections, scrape_all_collections, scrape_collection_listing

__all__ = [
    "DiscoverySourceAdapter",
    "SourceCollection",
    "SourceDocumentRef",
    "GallicaSourceAdapter",
    "IDPSourceAdapter",
    "VaticanSourceAdapter",
    "get_source_adapters",
    "get_source_adapter",
    "scrape_collection_listing",
    "scrape_all_collections",
    "get_available_collections",
    "fetch_manifests_async",
    "fetch_manifests_sync",
]
