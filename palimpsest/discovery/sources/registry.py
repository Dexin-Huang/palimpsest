from __future__ import annotations

from .adapter import DiscoverySourceAdapter
from .gallica_adapter import GallicaSourceAdapter
from .idp_adapter import IDPSourceAdapter
from .vatican_adapter import VaticanSourceAdapter


def get_source_adapters() -> dict[str, DiscoverySourceAdapter]:
    """Return the small registry of first-class discovery sources."""
    adapters = [
        GallicaSourceAdapter(),
        VaticanSourceAdapter(),
        IDPSourceAdapter(),
    ]
    return {adapter.source_id: adapter for adapter in adapters}


def get_source_adapter(source_id: str) -> DiscoverySourceAdapter:
    adapters = get_source_adapters()
    try:
        return adapters[source_id]
    except KeyError as exc:
        available = ", ".join(sorted(adapters))
        raise KeyError(f"Unknown source adapter {source_id!r}. Available: {available}") from exc
