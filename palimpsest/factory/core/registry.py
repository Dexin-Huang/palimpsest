"""Station variant registry, keyed by logical station name and variant."""

from __future__ import annotations

from palimpsest.factory.core.contracts import CONTRACTS, SOURCE_KINDS, contract
from palimpsest.factory.core.station import Station

_STATIONS: dict[str, dict[str, Station]] = {}


def register(station: Station) -> Station:
    if not isinstance(station.name, str) or not station.name.strip():
        raise ValueError("Station name must be a non-empty string")
    if not isinstance(station.variant, str) or not station.variant.strip():
        raise ValueError(f"Station {station.name!r} variant must be a non-empty string")

    registered_variants = _STATIONS.get(station.name)
    if registered_variants is not None and station.variant in registered_variants:
        raise ValueError(
            f"Station variant already registered: {station.name!r}/{station.variant!r}"
        )

    for kind in (*station.consumes, *station.optional_consumes, station.produces):
        if kind not in CONTRACTS:
            raise ValueError(
                f"Station {station.name!r} references unknown artifact kind "
                f"{kind!r} — declare it in core/contracts.py first"
            )

    if registered_variants:
        reference = next(iter(registered_variants.values()))
        if station.socket != reference.socket:
            raise ValueError(
                f"Station {station.name!r} variant {station.variant!r} has an "
                f"incompatible artifact socket; expected {reference.socket!r}, "
                f"got {station.socket!r}"
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
            for variants_by_name in _STATIONS.values()
            for registered in variants_by_name.values()
            if registered.name != station.name
            and registered.produces == station.produces
        ),
        None,
    )
    if existing_producer is not None:
        raise ValueError(
            f"Artifact {station.produces!r} already has producer "
            f"{existing_producer!r}; cannot also register {station.name!r}"
        )

    # Resolve declared files before mutating the registry. The concrete station
    # module is checked when its fingerprint is first requested, which keeps
    # lightweight test implementations registerable without hashing test code.
    station.validate_production_dependencies()
    _STATIONS.setdefault(station.name, {})[station.variant] = station
    return station


def all_stations() -> list[Station]:
    """Return one representative per logical station for the contract graph."""
    _ensure_loaded()
    return [_graph_representative(name) for name in sorted(_STATIONS)]


def get(name: str, variant: str | None = None) -> Station:
    """Resolve an explicit variant or the conservative production default."""
    _ensure_loaded()
    try:
        variants_by_name = _STATIONS[name]
    except KeyError:
        raise KeyError(
            f"Unknown station: {name!r}. Registered: {sorted(_STATIONS)}"
        ) from None

    if variant is not None:
        try:
            return variants_by_name[variant]
        except KeyError:
            raise KeyError(
                f"Unknown variant {variant!r} for station {name!r}. "
                f"Registered: {sorted(variants_by_name)}"
            ) from None

    if "default" in variants_by_name:
        return variants_by_name["default"]
    if len(variants_by_name) == 1:
        return next(iter(variants_by_name.values()))
    raise KeyError(
        f"Station {name!r} has multiple variants and no 'default'; "
        f"select one explicitly from {sorted(variants_by_name)}"
    )


def _graph_representative(name: str) -> Station:
    variants_by_name = _STATIONS[name]
    if "default" in variants_by_name:
        return variants_by_name["default"]
    return variants_by_name[sorted(variants_by_name)[0]]


def _ensure_loaded() -> None:
    # Importing the package registers every built-in station exactly once.
    import palimpsest.factory.stations  # noqa: F401
