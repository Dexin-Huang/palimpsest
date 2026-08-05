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
from palimpsest.factory.core.artifact import content_fingerprint, payload_fingerprint
from palimpsest.factory.core.contracts import contract, validate_payload
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
    variant: str | None = None
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
    process_stats: dict[str, int] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "CellOutcome":
        return cls(**json.loads(text))


def execute_cell(spec: CellSpec) -> CellOutcome:
    station = (
        registry.get(spec.station)
        if spec.variant is None
        else registry.get(spec.station, spec.variant)
    )
    if station.grain == "page" and spec.page_id is None:
        raise ValueError(f"Page station {station.name!r} requires a page_id")
    if station.grain == "manuscript" and spec.page_id is not None:
        raise ValueError(
            f"Manuscript station {station.name!r} does not accept a page_id"
        )
    if station.uses_model and not (spec.model and spec.prompt_name):
        raise ValueError(
            f"Model station {station.name!r} requires both model and prompt_name"
        )
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

    page_list = read_json(page_list_path(spec.doc_id, library_root))
    validate_payload("page_list", page_list, expected_doc_id=spec.doc_id)
    pages = tuple(sorted(page_list["pages"], key=lambda p: p.get("order", 0)))
    page = None
    if spec.page_id is not None:
        try:
            page = next(p for p in pages if p["page_id"] == spec.page_id)
        except StopIteration:
            raise ValueError(
                f"Page {spec.page_id!r} is not present in {spec.doc_id!r}'s page list"
            ) from None

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
    output_path = _persist_output(spec, station, job, prompt, result)
    return CellOutcome(
        output_path=str(output_path),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        process_stats=result.process_stats,
    )


def _persist_output(spec: CellSpec, station, job: Job, prompt, result) -> Path:
    output_path = station.output_path(job)
    output_contract = contract(station.produces)
    if output_contract.format == "json":
        if result.payload is None:
            raise ValueError(
                f"Station {station.name!r} must return a JSON payload for "
                f"{station.produces!r}"
            )
        validate_payload(station.produces, result.payload)
        payload = dict(result.payload)
        stamp = _provenance(spec, station, prompt, result)
        stamp["output_fingerprint"] = payload_fingerprint(payload)
        payload["provenance"] = stamp
        atomic_write_json(output_path, payload)
        return output_path

    if result.payload is not None:
        raise ValueError(
            f"Station {station.name!r} must write its {output_contract.format} "
            "artifact and return payload=None"
        )
    if not output_path.is_file():
        raise FileNotFoundError(
            f"Station {station.name!r} did not write its output: {output_path}"
        )
    stamp = _provenance(spec, station, prompt, result)
    stamp["output_fingerprint"] = content_fingerprint(output_path)
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".provenance.json"),
        stamp,
    )
    return output_path


def _provenance(spec: CellSpec, station, prompt, result) -> dict:
    stamp = {
        "station": station.name,
        "station_variant": station.variant,
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
