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
from functools import cache

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.core.contracts import contract
from palimpsest.factory.workspace.layout import artifact_path


@cache
def _factory_implementation_digest() -> bytes:
    """Hash executable factory source so code drift cannot masquerade as fresh."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.digest()


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

    @property
    def implementation_fingerprint(self) -> str:
        """Source-derived identity recorded in provenance and freshness state."""
        station = f"{type(self).__module__}.{type(self).__qualname__}".encode("utf-8")
        return hashlib.sha256(_factory_implementation_digest() + station).hexdigest()[
            :16
        ]

    grain: Literal["page", "manuscript"]
    consumes: tuple[str, ...]
    optional_consumes: tuple[str, ...] = ()
    produces: str
    uses_model: bool = False
    param_keys: frozenset[str] = frozenset()
    option_keys: frozenset[str] = frozenset()

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
