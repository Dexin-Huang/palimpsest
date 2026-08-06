"""read/omp_instrumented: the instrumented rig behind the book line's read socket.

The variant reads the line's clean study image with the certified rig
mechanism - base and shadow reads on the bound draft engine, content-addressed
RF-DETR count and glyph-classifier witnesses, a quiet gate, and a foreman
audit on magnified crops - and emits the reviewed ``page_transcription``
contract directly: draft reads become ``candidate_readings`` and the foreman
verdict becomes the ``adjudication_*`` fields.

Sensor artifacts must be computed on the same ``page_image_clean`` geometry
this variant reads; pins are recipe options. Blank pages are gated before any
model work by segment routing or by empty pinned detections (the page listed
in the pinned RF-DETR file with zero boxes). Measured lineage: exodia
experiments 22-34 (raw-image lane) and the factory bench lane.
"""

from __future__ import annotations

import hashlib
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
    _stage_detail_tiles,
    _stage_draft,
)
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import doc_dir


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

        page_image = job.path_of("page_image_clean")
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        run_root = doc_dir(job.doc_id, job.library_root) / "runs" / "read_omp_instrumented"

        # Resolve instrument objects before any paid model session. The pins
        # must describe the clean-image geometry this variant reads.
        detections_by_case = instrumented_sensors.load_jsonl_keyed(
            self._load_object(job, sensor_digests["detections_sha256"], "detections"),
            "characters",
        )
        verdicts_by_case = instrumented_sensors.load_jsonl_keyed(
            self._load_object(
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
        if characters == []:
            # Pinned RF-DETR evidence: the page exists in the detections file
            # with zero boxes. That is a positive blank verdict, unlike a
            # missing page (characters is None), which degrades to reading.
            return StationResult(
                payload=self._payload(job, "blank", _Reading("", [], "not_needed"))
            )

        base_text, base_run = _stage_draft(
            run_root / f"{page_key}-base",
            image=page_image,
            model=binding["model"],
        )
        tokens = base_run.tokens
        cost: float | None = base_run.cost_usd
        candidates = [
            _draft_candidate("base", binding["model"], base_text),
        ]
        alternates: list[str] = []
        try:
            shadow_text, shadow_run = _stage_draft(
                run_root / f"{page_key}-shadow",
                image=page_image,
                model=binding["model"],
            )
        except agent_cell.AgentCellError:
            # The shadow read is tremor evidence, never the result; a failed
            # read degrades to count and classifier sensors alone.
            cost = None
        else:
            candidates.append(_draft_candidate("shadow", binding["model"], shadow_text))
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
            reading = _Reading(
                base_text.strip(),
                candidates,
                "not_needed",
                adjudication_reasoning=_flag_summary(flags, "quiet page: base adopted"),
                unresolved=_unresolved(base_text, characters, verdict_columns),
            )
            return StationResult(
                payload=self._payload(job, "full_page", reading),
                tokens_in=tokens,
                cost_usd=cost,
                process_stats=base_run.process_stats,
            )

        workspace = agent_cell.stage_workspace(
            run_root / page_key,
            skill=job.config.prompt.text,
            evidence={},
            images=[page_image],
        )
        _stage_detail_tiles(workspace, image=page_image)
        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True)
        (extension_dir / "00-submit-transcription.ts").write_bytes(
            _SUBMISSION_EXTENSION_BYTES
        )
        (extension_dir / "transcription.ts").write_bytes(source_bytes)
        context_lines = [
            "# Page provenance",
            f"- Document: {job.doc_id}",
            f"- Page id: {job.page_id}.",
            "- Premodern Chinese manuscript or xylograph page from the factory",
            "  line; the study image is deframed, dewatermarked, and flattened.",
        ]
        instrumented_sensors.write_dossier(
            workspace, context_lines, base_text, sensors, page_image
        )

        run = agent_cell.run(
            workspace,
            _FOREMAN_TASK,
            model=job.config.model,
            timeout_s=_DRAFT_SCHOLAR_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        transcription, _layers = _read_submission(workspace)
        changed = sum(
            1
            for base_line, final_line in zip(
                base_text.splitlines(), transcription.splitlines()
            )
            if base_line != final_line
        )
        reading = _Reading(
            transcription,
            candidates,
            "completed",
            adjudication_requested_model=job.config.model,
            adjudication_model=job.config.model,
            adjudication_reasoning=_flag_summary(
                flags, f"foreman audited flagged spans; changed_lines={changed}"
            ),
            unresolved=_unresolved(transcription, characters, verdict_columns),
        )
        return StationResult(
            payload=self._payload(job, "full_page", reading),
            tokens_in=tokens + run.tokens,
            cost_usd=None
            if cost is None or run.cost_usd is None
            else cost + run.cost_usd,
            process_stats=run.process_stats,
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

    # Shared with the transcribe-family instrumented variant by contract: the
    # content hash makes any byte source equally trustworthy.
    def _load_object(self, job: Job, sha256_hex: str, label: str):
        from palimpsest.factory.stations.transcribe_omp import OmpInstrumentedTranscribe

        return OmpInstrumentedTranscribe._load_object(self, job, sha256_hex, label)


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
