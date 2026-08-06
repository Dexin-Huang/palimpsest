"""read/omp_instrumented: the instrumented rig behind the book line's read socket.

The variant reads the line's clean study image with the certified rig
mechanism - base and shadow reads on the bound draft engine, content-addressed
RF-DETR count and glyph-classifier witnesses, a quiet gate, and a foreman
audit on magnified crops - and emits the reviewed ``page_transcription``
contract directly: draft reads become ``candidate_readings`` and the foreman
verdict becomes the ``adjudication_*`` fields.

The rig orchestration (``_run_instrumented``) lives here and is shared with
the bench-only transcribe socket's OmpInstrumentedTranscribe; each variant
maps the rig's outcome onto its own payload contract.

Sensor artifacts must be computed on the same ``page_image_clean`` geometry
this variant reads; pins are recipe options. Blank pages are gated before any
model work by segment routing or by empty pinned detections (the page listed
in the pinned RF-DETR file with zero boxes). Measured lineage: exodia
the instrumented-rig campaign and the factory bench lane; see palimpsest-research.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations import instrumented_sensors
from palimpsest.factory.stations.read import Read, _Reading
from palimpsest.factory.stations.transcribe_omp import (
    _DRAFT_SCHOLAR_TIMEOUT_SECONDS,
    _FOREMAN_TASK,
    _SUBMISSION_EXTENSION_BYTES,
    _instrumented_options,
    _read_submission,
    _run_identity,
    _stage_detail_tiles,
    _stage_draft,
)
from palimpsest.factory.workspace.io import read_json


@dataclass
class _InstrumentedOutcome:
    """Everything the shared rig observed, for the variant payloads.

    ``quiet`` selects the base-adopted outcome (no foreman session); ``blank``
    marks a positive blank verdict from pinned detections. ``transcription``,
    ``layers``, and ``changed`` are set only after a foreman session.
    """

    tokens: int = 0
    cost: float | None = None
    process_stats: dict[str, int] | None = None
    quiet: bool = False
    blank: bool = False
    base_text: str = ""
    shadow_text: str | None = None
    sensors: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    characters: list[dict] | None = None
    verdict_columns: list[dict] | None = None
    transcription: str | None = None
    layers: list[dict[str, str]] | None = None
    changed: int | None = None


def _run_instrumented(
    job: Job,
    *,
    image: Path,
    run_name: str,
    context_lines: list[str],
    binding: dict[str, str],
    sensor_digests: dict[str, str],
    quiet_max: int,
    extension_source: bytes,
    blank: bool = False,
) -> _InstrumentedOutcome:
    """Run the instrumented rig shared by the omp_instrumented variants.

    Resolves the content-addressed sensor objects (they must describe the
    geometry of ``image``), stages base and shadow reads on the bound draft
    engine, applies the quiet gate, and otherwise runs the foreman audit cell
    on the staged workspace with the variant's context lines. Token, cost,
    and process-stat accounting follows the measured rig contract; payload
    assembly stays in the variants because their output contracts differ.
    """
    page_key, run_root = _run_identity(job, run_name)

    # Resolve instrument objects before any paid model session.
    detections_by_case = instrumented_sensors.load_jsonl_keyed(
        instrumented_sensors.load_object(
            job, sensor_digests["detections_sha256"], "detections"
        ),
        "characters",
    )
    verdicts_by_case = instrumented_sensors.load_jsonl_keyed(
        instrumented_sensors.load_object(
            job, sensor_digests["classifier_verdicts_sha256"], "classifier verdicts"
        ),
        "columns",
    )
    case_keys = (f"{job.doc_id}__{job.page_id}", str(job.doc_id))
    characters = next(
        (detections_by_case[key] for key in case_keys if key in detections_by_case),
        None,
    )
    verdict_columns = next(
        (verdicts_by_case[key] for key in case_keys if key in verdicts_by_case),
        None,
    )
    if characters == [] and blank:
        # Pinned RF-DETR evidence: the page exists in the detections file
        # with zero boxes. That is a positive blank verdict, unlike a
        # missing page (characters is None), which degrades to reading.
        return _InstrumentedOutcome(blank=True)

    base_text, base_run = _stage_draft(
        run_root / f"{page_key}-base",
        image=image,
        model=binding["model"],
    )
    tokens = base_run.tokens
    cost: float | None = base_run.cost_usd
    alternates: list[str] = []
    shadow_text: str | None = None
    try:
        shadow_text, shadow_run = _stage_draft(
            run_root / f"{page_key}-shadow",
            image=image,
            model=binding["model"],
        )
    except agent_cell.AgentCellError:
        # The shadow read is tremor evidence, never the result; a failed
        # read degrades to count and classifier sensors alone.
        cost = None
    else:
        if shadow_text != base_text:
            alternates.append(shadow_text)
        tokens += shadow_run.tokens
        if cost is not None:
            cost = (
                None
                if shadow_run.cost_usd is None
                else cost + shadow_run.cost_usd
            )

    sensors, flags = instrumented_sensors.compute_sensors(
        base_text, characters, alternates, verdict_columns
    )
    if instrumented_sensors.is_quiet(flags, quiet_max):
        return _InstrumentedOutcome(
            tokens=tokens,
            cost=cost,
            process_stats=base_run.process_stats,
            quiet=True,
            base_text=base_text,
            shadow_text=shadow_text,
            sensors=sensors,
            flags=flags,
            characters=characters,
            verdict_columns=verdict_columns,
        )

    workspace = agent_cell.stage_workspace(
        run_root / page_key,
        skill=job.config.prompt.text,
        evidence={},
        images=[image],
    )
    _stage_detail_tiles(workspace, image=image)
    extension_dir = workspace / ".omp" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "00-submit-transcription.ts").write_bytes(
        _SUBMISSION_EXTENSION_BYTES
    )
    (extension_dir / "transcription.ts").write_bytes(extension_source)
    instrumented_sensors.write_dossier(
        workspace, context_lines, base_text, sensors, image
    )

    run = agent_cell.run(
        workspace,
        _FOREMAN_TASK,
        model=job.config.model,
        timeout_s=_DRAFT_SCHOLAR_TIMEOUT_SECONDS,
        executor="omp",
        tool_names=("read",),
    )
    transcription, layers = _read_submission(workspace)
    changed = sum(
        1
        for base_line, final_line in zip(
            base_text.splitlines(), transcription.splitlines()
        )
        if base_line != final_line
    )
    return _InstrumentedOutcome(
        tokens=tokens + run.tokens,
        cost=None
        if cost is None or run.cost_usd is None
        else cost + run.cost_usd,
        process_stats=run.process_stats,
        base_text=base_text,
        shadow_text=shadow_text,
        sensors=sensors,
        flags=flags,
        characters=characters,
        verdict_columns=verdict_columns,
        transcription=transcription,
        layers=layers,
        changed=changed,
    )


class OmpInstrumentedRead(Read):
    """The instrumented rig on the read socket: sensors, quiet gate, foreman."""

    variant = "omp_instrumented"
    param_keys = frozenset()
    option_keys = frozenset(
        {"extension_source", "tool_bindings", "sensors", "quiet_max_disagreements"}
    )
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/read.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/instrumented_sensors.py",
        "factory/recognized_text.py",
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _instrumented_options(options)

    def run(self, job: Job) -> StationResult:
        source_bytes, binding, sensor_digests, quiet_max = _instrumented_options(
            job.config.options
        )
        plan = read_json(job.path_of("page_regions"))
        if plan["route"] == "blank":
            return StationResult(
                payload=self._payload(job, "blank", _Reading("", [], "not_needed"))
            )

        outcome = _run_instrumented(
            job,
            image=job.path_of("page_image_clean"),
            run_name="read_omp_instrumented",
            context_lines=[
                "# Page provenance",
                f"- Document: {job.doc_id}",
                f"- Page id: {job.page_id}.",
                "- Premodern Chinese manuscript or xylograph page from the factory",
                "  line; the study image is deframed, dewatermarked, and flattened.",
            ],
            binding=binding,
            sensor_digests=sensor_digests,
            quiet_max=quiet_max,
            extension_source=source_bytes,
            blank=True,
        )
        if outcome.blank:
            return StationResult(
                payload=self._payload(job, "blank", _Reading("", [], "not_needed"))
            )
        candidates = [_draft_candidate("base", binding["model"], outcome.base_text)]
        if outcome.shadow_text is not None:
            candidates.append(
                _draft_candidate("shadow", binding["model"], outcome.shadow_text)
            )
        if outcome.quiet:
            reading = _Reading(
                outcome.base_text.strip(),
                candidates,
                "not_needed",
                adjudication_reasoning=_flag_summary(
                    outcome.flags, "quiet page: base adopted"
                ),
                unresolved=_unresolved(
                    outcome.base_text, outcome.characters, outcome.verdict_columns
                ),
            )
            return StationResult(
                payload=self._payload(job, "full_page", reading),
                tokens_in=outcome.tokens,
                cost_usd=outcome.cost,
                process_stats=outcome.process_stats,
            )
        reading = _Reading(
            outcome.transcription,
            candidates,
            "completed",
            adjudication_requested_model=job.config.model,
            adjudication_model=job.config.model,
            adjudication_reasoning=_flag_summary(
                outcome.flags,
                f"foreman audited flagged spans; changed_lines={outcome.changed}",
            ),
            unresolved=_unresolved(
                outcome.transcription, outcome.characters, outcome.verdict_columns
            ),
        )
        return StationResult(
            payload=self._payload(job, "full_page", reading),
            tokens_in=outcome.tokens,
            cost_usd=outcome.cost,
            process_stats=outcome.process_stats,
        )

    def _payload(self, job: Job, route: str, reading: _Reading) -> dict[str, Any]:
        page = job.page or {}
        return {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": page.get("order", 0),
            "canvas_id": page.get("canvas_id", ""),
            "text": reading.text,
            "route": route,
            "regions": [],
            **reading.audit(),
        }


def _draft_candidate(role: str, model: str, text: str) -> dict[str, Any]:
    return {
        "role": role,
        "requested_model": model,
        "model": model,
        "text": text,
    }


def _flag_summary(flags: Mapping[str, int], prefix: str) -> str:
    counts = ", ".join(f"{name}={value}" for name, value in sorted(flags.items()))
    return f"{prefix}; sensor flags: {counts}"


def _unresolved(
    text: str, characters: list[dict] | None, verdict_columns: list[dict] | None
) -> list[str]:
    """Residual instrument complaints against the final text.

    Tremor is excluded on purpose: after a foreman edit every changed line
    "disagrees" with its draft, which is resolution, not an open question.
    """
    _, residual = instrumented_sensors.compute_sensors(
        text, characters, [], verdict_columns
    )
    return [
        f"{name}={value}"
        for name, value in sorted(residual.items())
        if isinstance(value, int) and value > 0
    ]


register(OmpInstrumentedRead())
