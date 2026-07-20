"""One cell, fully specified and self-contained.

A ``CellSpec`` is everything a worker needs to execute one
(doc × page × station) cell — and nothing else. It is JSON-serializable on
purpose: the same spec runs in the conductor's thread, in a fresh
subprocess, or (later) inside a spawned agent, with identical results.
This is the fleet contract: the worker's whole world is the spec.

``execute_cell`` is the single implementation of the cell body: build the
job, load and VERIFY the prompt (a worker refuses to run with a prompt whose
hash differs from what the conductor fingerprinted), run the station,
validate the output contract, stamp provenance, write atomically. The
ledger stays with the conductor — workers never touch shared state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from palimpsest.factory import prompt_store
from palimpsest.factory.core import registry
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.workspace.io import atomic_write_json, read_json, utc_now
from palimpsest.factory.workspace.layout import page_list_path


@dataclass(frozen=True)
class CellSpec:
    doc_id: str
    station: str
    page_id: str | None
    library_root: str
    config_fingerprint: str
    input_fingerprint: str
    model: str | None = None
    prompt_name: str | None = None
    prompt_sha256: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "CellSpec":
        return cls(**json.loads(text))


@dataclass
class CellOutcome:
    output_path: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "CellOutcome":
        return cls(**json.loads(text))


def execute_cell(spec: CellSpec) -> CellOutcome:
    station = registry.get(spec.station)
    library_root = Path(spec.library_root)

    prompt = None
    if spec.prompt_name:
        prompt = prompt_store.load(spec.prompt_name)
        if spec.prompt_sha256 and prompt.sha256 != spec.prompt_sha256:
            raise ValueError(
                f"Prompt {spec.prompt_name!r} hash mismatch: the spec was "
                f"fingerprinted against {spec.prompt_sha256[:12]}… but the "
                f"store now has {prompt.sha256[:12]}… — refusing to run"
            )

    pages = tuple(
        sorted(
            read_json(page_list_path(spec.doc_id, library_root))["pages"],
            key=lambda p: p.get("order", 0),
        )
    )
    page = None
    if spec.page_id is not None:
        page = next(p for p in pages if p["page_id"] == spec.page_id)

    job = Job(
        doc_id=spec.doc_id,
        pages=pages,
        page=page,
        library_root=library_root,
        config=StationConfig(
            model=spec.model,
            prompt=prompt,
            params=dict(spec.params),
            options=dict(spec.options),
        ),
    )

    result = station.run(job)
    output_path = station.output_path(job)
    if result.payload is not None:
        validate_payload(station.produces, result.payload)
        payload = dict(result.payload)
        payload["provenance"] = _provenance(spec, station, prompt, result)
        atomic_write_json(output_path, payload)
    else:
        atomic_write_json(
            output_path.with_suffix(output_path.suffix + ".provenance.json"),
            _provenance(spec, station, prompt, result),
        )
    return CellOutcome(
        output_path=str(output_path),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )


def _provenance(spec: CellSpec, station, prompt, result) -> dict:
    stamp = {
        "station": station.name,
        "station_fingerprint": station.implementation_fingerprint,
        "config_fingerprint": spec.config_fingerprint,
        "input_fingerprint": spec.input_fingerprint,
        "created_at": utc_now(),
    }
    if spec.model:
        stamp["model"] = spec.model
    if prompt is not None:
        stamp["prompt_name"] = prompt.name
        stamp["prompt_sha256"] = prompt.sha256
    if spec.params or spec.options:
        stamp["params"] = {**dict(spec.params), **dict(spec.options)}
    if result.tokens_in is not None:
        stamp["tokens_in"] = result.tokens_in
        stamp["tokens_out"] = result.tokens_out
    if result.cost_usd is not None:
        stamp["cost_usd"] = result.cost_usd
    return stamp
