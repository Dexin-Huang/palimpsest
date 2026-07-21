"""Station registry: recipes reference stations by name; implementations
plug in here without the conductor knowing them."""

from __future__ import annotations

from palimpsest.factory.core.contracts import CONTRACTS, SOURCE_KINDS, contract
from palimpsest.factory.core.station import Station

_STATIONS: dict[str, Station] = {}


def register(station: Station) -> Station:
    if station.name in _STATIONS:
        raise ValueError(f"Station already registered: {station.name}")
    for kind in (*station.consumes, *station.optional_consumes, station.produces):
        if kind not in CONTRACTS:
            raise ValueError(
                f"Station {station.name!r} references unknown artifact kind "
                f"{kind!r} — declare it in core/contracts.py first"
            )
    output = contract(station.produces)
    if output.grain != station.grain:
        raise ValueError(
            f"Station {station.name!r} is {station.grain}-grain but produces "
            f"{output.kind!r}, which is {output.grain}-grain"
        )
    if station.produces in SOURCE_KINDS:
        raise ValueError(
            f"Station {station.name!r} cannot produce source artifact "
            f"{station.produces!r}"
        )
    existing_producer = next(
        (
            registered.name
            for registered in _STATIONS.values()
            if registered.produces == station.produces
        ),
        None,
    )
    if existing_producer is not None:
        raise ValueError(
            f"Artifact {station.produces!r} already has producer "
            f"{existing_producer!r}; cannot also register {station.name!r}"
        )
    _STATIONS[station.name] = station
    return station


def all_stations() -> list[Station]:
    _ensure_loaded()
    return [_STATIONS[name] for name in sorted(_STATIONS)]


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
