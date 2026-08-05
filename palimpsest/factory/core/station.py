"""The station contract (FACTORY.md §2.2).

A station is one hermetic processing step: it reads its declared inputs,
produces exactly one output artifact, and reports usage. It never touches
the ledger, never talks to siblings, and keeps no state between executions.
The conductor owns scheduling, freshness, and ledger logging; the cell runtime
owns output validation, provenance stamping, and atomic writes.

Two artifact modes, signalled by ``StationResult.payload``:
- JSON stations return the artifact content as a dict; the cell runtime embeds
  the provenance stamp and performs the atomic write.
- File stations (images) write their output file themselves (atomically) and
  return ``payload=None``; the cell runtime writes a
  ``<output>.provenance.json`` sidecar so binary artifacts stay auditable.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from functools import cached_property

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.core.contracts import contract
from palimpsest.factory.workspace.layout import artifact_path


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_SHARED_RUNTIME_SOURCES = (
    "factory/core/artifact.py",
    "factory/core/cell.py",
    "factory/core/contracts.py",
    "factory/core/registry.py",
    "factory/core/station.py",
    "factory/prompt_store.py",
    "factory/workspace/io.py",
    "factory/workspace/layout.py",
)
_NON_PRODUCTION_SOURCE_PREFIXES = (("factory", "evaluation"),)


def _package_source_path(source: str, *, purpose: str) -> Path:
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{purpose} must be a non-empty package-relative path")
    relative = Path(source)
    if relative.is_absolute():
        raise ValueError(f"{purpose} must be package-relative: {source!r}")
    path = (_PACKAGE_ROOT / relative).resolve()
    try:
        normalized = path.relative_to(_PACKAGE_ROOT)
    except ValueError:
        raise ValueError(
            f"{purpose} is outside the installed palimpsest package: {source!r}"
        ) from None
    if not path.is_file():
        raise ValueError(f"{purpose} does not exist: {source!r}")
    if path.suffix != ".py":
        raise ValueError(f"{purpose} must name a Python source file: {source!r}")
    if any(
        normalized.parts[: len(prefix)] == prefix
        for prefix in _NON_PRODUCTION_SOURCE_PREFIXES
    ):
        raise ValueError(f"{purpose} cannot include evaluation source: {source!r}")
    return path


def _station_source_path(station: Station) -> Path:
    source = inspect.getsourcefile(type(station))
    if source is None:
        raise ValueError(
            f"Cannot locate source for station "
            f"{type(station).__module__}.{type(station).__qualname__}"
        )
    path = Path(source).resolve()
    try:
        normalized = path.relative_to(_PACKAGE_ROOT)
    except ValueError:
        # Test doubles are deliberately excluded from production identity.
        # Their qualified class name still distinguishes them while the base
        # station ABI supplies the only production source component.
        if path.name.startswith("test_") or "tests" in path.parts:
            return Path(__file__).resolve()
        raise ValueError(
            f"Station {station.name!r} implementation source is outside the "
            "installed palimpsest package"
        ) from None
    if any(
        normalized.parts[: len(prefix)] == prefix
        for prefix in _NON_PRODUCTION_SOURCE_PREFIXES
    ):
        raise ValueError(
            f"Station {station.name!r} implementation cannot live in evaluation source"
        )
    return path


@dataclass(frozen=True)
class StationConfig:
    """A recipe slot, resolved: model + loaded prompt + params + options."""

    model: str | None = None
    prompt: Prompt | None = None
    params: Mapping[str, Any] = field(default_factory=dict)  # generation params
    options: Mapping[str, Any] = field(default_factory=dict)  # station-specific


@dataclass(frozen=True)
class Job:
    """One executable cell: (doc × station) or (doc × page × station)."""

    doc_id: str
    pages: tuple[dict, ...]  # full ordered page_list entries
    page: dict | None  # this cell's page; None for manuscript grain
    library_root: Path
    config: StationConfig

    @property
    def page_id(self) -> str | None:
        return self.page["page_id"] if self.page is not None else None

    def path_of(self, kind: str, page_id: str | None = None) -> Path:
        return artifact_path(
            self.doc_id,
            kind,
            page_id if page_id is not None else self.page_id,
            self.library_root,
        )


@dataclass
class StationResult:
    payload: dict | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    process_stats: dict[str, int] | None = None


class Station:
    """Base class: subclasses set the class attributes and implement run().

    ``consumes``/``optional_consumes``/``produces`` are artifact kinds from
    ``core.contracts``. The default ``input_paths`` resolves each required
    input and every optional input present for this job. Stations with wider
    inputs (e.g. translate's neighbor-context) override it — whatever they
    read MUST be listed, since the conductor fingerprints exactly these files
    for staleness.
    """

    name: str
    variant: str = "default"
    grain: Literal["page", "manuscript"]
    consumes: tuple[str, ...]
    optional_consumes: tuple[str, ...] = ()
    produces: str
    uses_model: bool = False
    param_keys: frozenset[str] = frozenset()
    option_keys: frozenset[str] = frozenset()
    # Package-relative Python paths whose behavior is part of this variant.
    # The concrete station module and shared cell runtime are included
    # automatically and must not be repeated here.
    production_dependencies: tuple[str, ...] = ()

    @property
    def socket(self) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        """The artifact socket shared by every variant of a logical station."""
        return (
            self.grain,
            self.consumes,
            self.optional_consumes,
            self.produces,
        )

    def validate_production_dependencies(self) -> tuple[Path, ...]:
        """Resolve and validate explicitly declared production source files."""
        paths: list[Path] = []
        seen: set[str] = set()
        for source in self.production_dependencies:
            path = _package_source_path(
                source,
                purpose=f"Station {self.name!r} production dependency",
            )
            normalized = os.path.normcase(str(path))
            if normalized in seen:
                raise ValueError(
                    f"Station {self.name!r} declares duplicate production "
                    f"dependency {source!r}"
                )
            seen.add(normalized)
            paths.append(path)
        return tuple(paths)

    @cached_property
    def production_source_paths(self) -> tuple[Path, ...]:
        """Ordered source closure used to identify this implementation."""
        paths = [
            *(
                _package_source_path(source, purpose="Shared runtime source")
                for source in _SHARED_RUNTIME_SOURCES
            ),
            _station_source_path(self),
        ]
        seen = {os.path.normcase(str(path)) for path in paths}
        unique_paths = list(dict.fromkeys(paths))
        for path in self.validate_production_dependencies():
            normalized = os.path.normcase(str(path))
            if normalized in seen:
                relative = path.relative_to(_PACKAGE_ROOT).as_posix()
                raise ValueError(
                    f"Station {self.name!r} source closure contains duplicate "
                    f"path {relative!r}"
                )
            seen.add(normalized)
            unique_paths.append(path)
        return tuple(sorted(unique_paths, key=lambda path: path.as_posix()))

    @cached_property
    def implementation_fingerprint(self) -> str:
        """Localized source identity recorded in provenance and freshness state."""
        digest = hashlib.sha256()
        digest.update(b"palimpsest-station-implementation-v1\0")
        digest.update(self.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(self.variant.encode("utf-8"))
        digest.update(b"\0")
        qualified_name = f"{type(self).__module__}.{type(self).__qualname__}"
        digest.update(qualified_name.encode("utf-8"))
        digest.update(b"\0")
        for path in self.production_source_paths:
            relative = path.relative_to(_PACKAGE_ROOT).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()[:16]

    def input_paths(self, job: Job) -> list[Path]:
        required = self._paths_for(job, self.consumes)
        optional = [
            path
            for path in self._paths_for(job, self.optional_consumes)
            if path.is_file()
        ]
        return [*required, *optional]

    def _paths_for(self, job: Job, kinds: tuple[str, ...]) -> list[Path]:
        if self.grain == "page":
            return [job.path_of(kind) for kind in kinds]
        paths = []
        for kind in kinds:
            if contract(kind).grain == "page":
                paths.extend(job.path_of(kind, page["page_id"]) for page in job.pages)
            else:
                paths.append(job.path_of(kind))
        return paths

    def output_path(self, job: Job) -> Path:
        return job.path_of(self.produces)

    def signature_extras(self, job: Job) -> tuple[str, ...]:
        """Non-file inputs that must participate in the input fingerprint
        (e.g. acquire's source URL)."""
        return ()

    def run(self, job: Job) -> StationResult:
        raise NotImplementedError
