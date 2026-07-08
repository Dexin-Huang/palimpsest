"""Station registry: recipes reference stations by name; implementations
plug in here without the conductor knowing them."""

from __future__ import annotations

from palimpsest.factory.core.station import Station

_STATIONS: dict[str, Station] = {}


def register(station: Station) -> Station:
    if station.name in _STATIONS:
        raise ValueError(f"Station already registered: {station.name}")
    _STATIONS[station.name] = station
    return station


def get(name: str) -> Station:
    _ensure_loaded()
    try:
        return _STATIONS[name]
    except KeyError:
        raise KeyError(
            f"Unknown station: {name!r}. Registered: {sorted(_STATIONS)}"
        ) from None


def _ensure_loaded() -> None:
    # Importing the package registers every built-in station exactly once.
    import palimpsest.factory.stations  # noqa: F401
