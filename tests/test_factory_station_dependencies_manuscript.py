from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import palimpsest
from palimpsest.factory.core.registry import get
from palimpsest.factory.stations.assemble_page import AssemblePage
from palimpsest.factory.stations.emend import Emend
from palimpsest.factory.stations.publish import Publish
from palimpsest.factory.stations.reconstruct import Reconstruct
from palimpsest.factory.stations.reference import Reference
from palimpsest.factory.stations.render_epub import RenderEpub
from palimpsest.factory.stations.survey import Survey
from palimpsest.factory.stations.translate import Translate

_PACKAGE_ROOT = Path(palimpsest.__file__).resolve().parent
_SHARED_ABI = {
    "factory/core/artifact.py",
    "factory/core/cell.py",
    "factory/core/contracts.py",
    "factory/core/registry.py",
    "factory/core/station.py",
    "factory/prompt_store.py",
    "factory/workspace/io.py",
    "factory/workspace/layout.py",
}
_GATEWAY = (
    "factory/gateway/__init__.py",
    "factory/gateway/client.py",
    "factory/gateway/omp.py",
    "factory/gateway/protocol.py",
    "factory/usage.py",
)
_STATIONS = {
    AssemblePage: (),
    Emend: (
        "factory/agent_cell.py",
        "factory/apparatus.py",
    ),
    Publish: (
        "factory/usage.py",
    ),
    Reconstruct: _GATEWAY,
    Reference: ("factory/agent_cell.py",),
    RenderEpub: ("factory/brand.py",),
    Survey: _GATEWAY,
    Translate: (
        *_GATEWAY[:-1],
        "factory/seams.py",
        _GATEWAY[-1],
    ),
}


def _module_source(module_name: str) -> str | None:
    if not module_name.startswith("palimpsest."):
        return None
    relative = module_name.removeprefix("palimpsest.").replace(".", "/")
    module_path = _PACKAGE_ROOT / f"{relative}.py"
    if module_path.is_file():
        return module_path.relative_to(_PACKAGE_ROOT).as_posix()
    package_path = _PACKAGE_ROOT / relative / "__init__.py"
    if package_path.is_file():
        return package_path.relative_to(_PACKAGE_ROOT).as_posix()
    return None


def _direct_palimpsest_imports(station_type: type) -> set[str]:
    source_path = Path(inspect.getsourcefile(station_type) or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names = []
            for alias in node.names:
                child = _module_source(f"{node.module}.{alias.name}")
                if child is not None:
                    imports.add(child)
                else:
                    module_names.append(node.module)
        else:
            continue
        imports.update(
            source
            for module_name in module_names
            if (source := _module_source(module_name)) is not None
        )
    return imports


@pytest.mark.parametrize(("station_type", "dependencies"), _STATIONS.items())
def test_manuscript_station_source_closures_are_exact_and_local(
    station_type: type, dependencies: tuple[str, ...]
) -> None:
    station = station_type()
    assert get(station.name, station.variant) is not None
    assert station.production_dependencies == dependencies
    assert not (_SHARED_ABI & set(dependencies))

    station_source = Path(inspect.getsourcefile(station_type) or "").resolve()
    expected = {
        *_SHARED_ABI,
        station_source.relative_to(_PACKAGE_ROOT).as_posix(),
        *dependencies,
    }
    closure = {
        path.relative_to(_PACKAGE_ROOT).as_posix()
        for path in station.production_source_paths
    }

    assert closure == expected
    assert _direct_palimpsest_imports(station_type) <= closure
    assert not any(
        forbidden in Path(source).parts
        for source in closure
        for forbidden in ("evaluation", "tests", "docs")
    )
    assert {source for source in closure if source.startswith("factory/stations/")} == {
        station_source.relative_to(_PACKAGE_ROOT).as_posix()
    }
    assert re.fullmatch(r"[0-9a-f]{16}", station.implementation_fingerprint)
    assert (
        station_type().implementation_fingerprint == station.implementation_fingerprint
    )


@pytest.mark.parametrize(
    ("station_type", "dependency"),
    [
        (station_type, dependency)
        for station_type, dependencies in _STATIONS.items()
        for dependency in dependencies
    ],
)
def test_each_declared_manuscript_dependency_affects_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    station_type: type,
    dependency: str,
) -> None:
    baseline = station_type().implementation_fingerprint
    dependency_path = (_PACKAGE_ROOT / dependency).resolve()
    original_read_bytes = Path.read_bytes

    def read_bytes_with_dependency_change(path: Path) -> bytes:
        content = original_read_bytes(path)
        if path.resolve() == dependency_path:
            return content + b"\n# localized fingerprint probe\n"
        return content

    monkeypatch.setattr(Path, "read_bytes", read_bytes_with_dependency_change)

    assert station_type().implementation_fingerprint != baseline
