from __future__ import annotations

from pathlib import Path

import pytest

from palimpsest.factory import graph
from palimpsest.factory.core import recipe, registry
from palimpsest.factory.core import station as station_module
from palimpsest.factory.core.station import Station


class SocketStation(Station):
    name = "socket_test"
    variant = "default"
    grain = "page"
    consumes = ("page_list",)
    produces = "page_image_framed"

    @property
    def implementation_fingerprint(self) -> str:
        return f"fingerprint-{self.variant}"


def station_variant(variant: str, **overrides: object) -> Station:
    attributes = {"variant": variant, **overrides}
    implementation = type(f"SocketStation_{variant}", (SocketStation,), attributes)
    return implementation()


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_STATIONS", {})
    monkeypatch.setattr(registry, "_ensure_loaded", lambda: None)


def test_compatible_variants_coexist_with_conservative_default(
    isolated_registry: None,
) -> None:
    default = SocketStation()
    experimental = station_variant("experimental/v1")

    registry.register(default)
    registry.register(experimental)

    assert registry.get("socket_test") is default
    assert registry.get("socket_test", "experimental/v1") is experimental
    assert registry.variants("socket_test") == [default, experimental]
    assert registry.all_stations() == [default]
    assert registry.all_variants() == [default, experimental]


def test_duplicate_station_variant_fails_registration(
    isolated_registry: None,
) -> None:
    registry.register(SocketStation())

    with pytest.raises(ValueError, match="Station variant already registered"):
        registry.register(SocketStation())


@pytest.mark.parametrize(
    "override",
    [
        {"grain": "manuscript"},
        {"consumes": ("metadata",)},
        {"optional_consumes": ("metadata",)},
        {"produces": "page_image_unmarked"},
    ],
)
def test_incompatible_station_variant_socket_fails_registration(
    isolated_registry: None,
    override: dict[str, object],
) -> None:
    registry.register(SocketStation())

    with pytest.raises(ValueError, match="incompatible artifact socket"):
        registry.register(station_variant("incompatible", **override))


def test_multiple_variants_without_default_require_explicit_selection(
    isolated_registry: None,
) -> None:
    first = station_variant("first")
    second = station_variant("second")
    registry.register(first)
    registry.register(second)

    with pytest.raises(KeyError, match="select one explicitly"):
        registry.get("socket_test")
    assert registry.get("socket_test", "second") is second


def test_recipe_slot_resolves_exactly_the_selected_variant(
    isolated_registry: None,
    tmp_path: Path,
) -> None:
    registry.register(SocketStation())
    selected = station_variant("experimental/v1")
    registry.register(selected)
    (tmp_path / "variant_recipe.yaml").write_text(
        """name: variant_recipe
language: test
line:
  - station: socket_test
    variant: experimental/v1
""",
        encoding="utf-8",
    )

    loaded = recipe.load("variant_recipe", tmp_path)

    assert len(loaded.steps) == 1
    assert loaded.steps[0].station is selected
    assert loaded.steps[0].options == {}


def test_graph_lists_logical_station_once_when_variants_coexist(
    isolated_registry: None,
) -> None:
    registry.register(SocketStation())
    registry.register(station_variant("experimental/v1"))

    data = graph.build()

    assert [item["station"] for item in data["stations"]] == ["socket_test"]
    assert data["stations"][0]["implementation"] == "fingerprint-default"
    assert graph.to_mermaid().count('station_socket_test(["') == 1


def test_localized_fingerprint_ignores_source_outside_variant_closure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared.py"
    implementation = tmp_path / "implementation.py"
    dependency = tmp_path / "domain.py"
    unrelated = tmp_path / "unrelated.py"
    for path, content in (
        (shared, "ABI = 1\n"),
        (implementation, "IMPLEMENTATION = 1\n"),
        (dependency, "DOMAIN = 1\n"),
        (unrelated, "UNRELATED = 1\n"),
    ):
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(station_module, "_PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(station_module, "_SHARED_RUNTIME_SOURCES", ("shared.py",))
    monkeypatch.setattr(
        station_module, "_station_source_path", lambda station: implementation
    )

    class FingerprintedStation(Station):
        name = "localized"
        variant = "direct/v1"
        grain = "page"
        consumes = ("page_list",)
        produces = "page_image_framed"
        production_dependencies = ("domain.py",)

    baseline = FingerprintedStation().implementation_fingerprint
    unrelated.write_text("UNRELATED = 2\n", encoding="utf-8")
    after_unrelated_change = FingerprintedStation().implementation_fingerprint
    dependency.write_text("DOMAIN = 2\n", encoding="utf-8")
    after_dependency_change = FingerprintedStation().implementation_fingerprint
    implementation.write_text("IMPLEMENTATION = 2\n", encoding="utf-8")
    after_implementation_change = FingerprintedStation().implementation_fingerprint

    assert after_unrelated_change == baseline
    assert after_dependency_change != baseline
    assert after_implementation_change != after_dependency_change


def test_registration_rejects_missing_declared_production_dependency(
    isolated_registry: None,
) -> None:
    missing = station_variant(
        "missing-source", production_dependencies=("factory/does_not_exist.py",)
    )

    with pytest.raises(ValueError, match="production dependency does not exist"):
        registry.register(missing)
