from __future__ import annotations

import ast
from pathlib import Path

import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core import station as station_module
from palimpsest.factory.core.station import Station
from palimpsest.factory.stations.acquire import Acquire
from palimpsest.factory.stations.align import Align
from palimpsest.factory.stations.deframe import Deframe, SpreadSafeDeframe
from palimpsest.factory.stations.dewatermark import Dewatermark
from palimpsest.factory.stations.flatten import Flatten
from palimpsest.factory.stations.read import Read
from palimpsest.factory.stations.segment import Segment


PAGE_STATIONS = (
    Acquire,
    Deframe,
    SpreadSafeDeframe,
    Dewatermark,
    Flatten,
    Segment,
    Read,
    Align,
)
EXPECTED_DEPENDENCIES = {
    Acquire: (),
    Deframe: (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    ),
    SpreadSafeDeframe: (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    ),
    Dewatermark: (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    ),
    Flatten: (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    ),
    Segment: (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    ),
    Read: (
        "factory/config.py",
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/gemini.py",
        "factory/gateway/omp.py",
        "factory/gateway/pricing.py",
        "factory/gateway/protocol.py",
        "factory/imaging.py",
        "factory/stations/image_input.py",
        "factory/usage.py",
    ),
    Align: (
        "factory/glyphs.py",
        "factory/stations/image_input.py",
    ),
}
PACKAGE_ROOT = Path(station_module.__file__).resolve().parents[2]
SHARED_ABI = frozenset(station_module._SHARED_RUNTIME_SOURCES)
IMPORT_GRAPH_BOUNDARIES = SHARED_ABI | {"factory/core/registry.py"}


def _relative_source(module_name: str) -> str | None:
    prefix = "palimpsest."
    if not module_name.startswith(prefix):
        return None
    relative_module = module_name.removeprefix(prefix).replace(".", "/")
    module_path = PACKAGE_ROOT / f"{relative_module}.py"
    if module_path.is_file():
        return module_path.relative_to(PACKAGE_ROOT).as_posix()
    package_path = PACKAGE_ROOT / relative_module / "__init__.py"
    if package_path.is_file():
        return package_path.relative_to(PACKAGE_ROOT).as_posix()
    raise AssertionError(f"Cannot resolve imported Palimpsest module {module_name!r}")


def _production_import_closure(station_type: type[Station]) -> set[str]:
    station_path = Path(
        __import__(station_type.__module__, fromlist=["__file__"]).__file__
    )
    station_source = station_path.resolve().relative_to(PACKAGE_ROOT).as_posix()
    pending = [station_source]
    visited: set[str] = set()
    dependencies: set[str] = set()

    while pending:
        source = pending.pop()
        if source in visited or source in IMPORT_GRAPH_BOUNDARIES:
            continue
        visited.add(source)
        tree = ast.parse((PACKAGE_ROOT / source).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str]
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                imported_source = _relative_source(name)
                if (
                    imported_source is None
                    or imported_source == station_source
                    or imported_source in SHARED_ABI
                ):
                    continue
                if imported_source not in dependencies:
                    dependencies.add(imported_source)
                    pending.append(imported_source)

    return dependencies


@pytest.mark.parametrize("station_type", PAGE_STATIONS, ids=lambda cls: cls.name)
def test_page_station_declares_exact_production_import_closure(
    station_type: type[Station],
) -> None:
    station = station_type()
    expected = EXPECTED_DEPENDENCIES[station_type]

    assert station.production_dependencies == expected
    assert set(expected) == _production_import_closure(station_type)

    relative_sources = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in station.production_source_paths
    }
    station_source = (
        Path(__import__(station_type.__module__, fromlist=["__file__"]).__file__)
        .resolve()
        .relative_to(PACKAGE_ROOT)
        .as_posix()
    )
    assert relative_sources == {*SHARED_ABI, station_source, *expected}
    assert all(
        not source.startswith("factory/evaluation/") for source in relative_sources
    )


@pytest.mark.parametrize("station_type", PAGE_STATIONS, ids=lambda cls: cls.name)
def test_page_station_registers_and_computes_fingerprint(
    station_type: type[Station],
) -> None:
    station = registry.get(station_type.name, station_type().variant)

    assert type(station) is station_type
    assert len(station.implementation_fingerprint) == 16
    assert int(station.implementation_fingerprint, 16) >= 0


@pytest.mark.parametrize("station_type", PAGE_STATIONS, ids=lambda cls: cls.name)
def test_fingerprint_reacts_to_each_declared_dependency(
    station_type: type[Station], monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = station_type().implementation_fingerprint
    original_read_bytes = Path.read_bytes

    for dependency in EXPECTED_DEPENDENCIES[station_type]:
        changed_path = (PACKAGE_ROOT / dependency).resolve()

        def read_bytes_with_change(path: Path, *, target: Path = changed_path) -> bytes:
            content = original_read_bytes(path)
            if path.resolve() == target:
                return content + b"\nmodeled production dependency change\n"
            return content

        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_bytes", read_bytes_with_change)
            changed = station_type().implementation_fingerprint

        assert changed != baseline, dependency


def test_fingerprints_do_not_read_evaluation_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes

    def reject_evaluation_source(path: Path) -> bytes:
        relative = path.resolve().relative_to(PACKAGE_ROOT).as_posix()
        if relative.startswith("factory/evaluation/"):
            raise AssertionError(f"Fingerprint read evaluation source {relative}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_evaluation_source)

    for station_type in PAGE_STATIONS:
        assert station_type().implementation_fingerprint


@pytest.mark.parametrize(
    ("dependencies", "message"),
    (
        (
            ("factory/imaging.py", "factory/imaging.py"),
            "declares duplicate production dependency",
        ),
        (("factory/does_not_exist.py",), "production dependency does not exist"),
        (
            ("factory/evaluation/__init__.py",),
            "production dependency cannot include evaluation source",
        ),
    ),
    ids=("duplicate", "missing", "evaluation"),
)
def test_registration_rejects_invalid_production_dependency_closures(
    dependencies: tuple[str, ...],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidDependencyStation(Station):
        name = "invalid-page-dependency"
        grain = "page"
        consumes = ("page_list",)
        produces = "page_image_framed"
        production_dependencies = dependencies

    monkeypatch.setattr(registry, "_STATIONS", {})

    with pytest.raises(ValueError, match=message):
        registry.register(InvalidDependencyStation())
