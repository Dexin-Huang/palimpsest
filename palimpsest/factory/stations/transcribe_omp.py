"""Agent-cell transcription through a candidate-owned OMP extension."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Never

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations import instrumented_sensors
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.workspace.layout import doc_dir

MAX_EXTENSION_BYTES = 128 * 1024
MAX_TRANSCRIPTION_BYTES = 256 * 1024
TRANSCRIPTION_TIMEOUT_SECONDS = 1800
# The tooled scholar does roughly twice the reading work of the bare policy:
# its own full pass plus draft adjudication plus verification. Three bare
# baseline sessions reached the former 1200 s wall during the concurrent
# ten-page corpus run. Give the bare pass bounded headroom and keep the
# measured two-pass ratio for the tooled scholar.
_DRAFT_SCHOLAR_TIMEOUT_SECONDS = 2 * TRANSCRIPTION_TIMEOUT_SECONDS
_ARTIFACT_NAME = "transcription.json"
_JOURNAL_NAME = ".transcription-submissions.jsonl"
_SEAL_NAME = ".transcription-submission-seal.json"
_MAX_ARTIFACT_BYTES = MAX_TRANSCRIPTION_BYTES * 6 + 64
_LAYER_KINDS = ("primary", "commentary", "marginalia", "seal", "colophon")
_DRAFT_NAMES = (
    "draft-1.txt",
    "draft-2.txt",
    "draft-3.txt",
)
_DETAIL_ROWS = 2
_DETAIL_COLUMNS = 3
_DETAIL_OVERLAP_DIVISOR = 25
_DETAIL_JPEG_QUALITY = 95
# Staged draft calls repeatedly reached both the former 600 s and 1200 s walls.
_DRAFT_TIMEOUT_SECONDS = _DRAFT_SCHOLAR_TIMEOUT_SECONDS
_DRAFT_SKILL = (
    "You produce one diplomatic transcription of the staged page image. Preserve "
    "physical reading order and visible line breaks. Do not summarize, translate, "
    "normalize, or omit uncertain glyphs. Call submit_transcription exactly once."
)
_DRAFT_TASK = (
    "Read images/ to find the single staged page image, inspect that image with "
    "read, and submit the complete transcription exactly once. The tool call is "
    "the only accepted output; do not put the transcription in final prose."
)
_TASK = (
    "Transcribe the single page image staged in images/. Follow AGENTS.md and "
    "the transcription extension's policy. Inspect the full image and every "
    "overlapping tile under images/details/ with read. Use the full page for "
    "reading order and the tiles for stroke evidence, then call "
    "submit_transcription exactly once with the complete diplomatic transcription. "
    "Transcribe every textual layer on the page. When the page carries more than "
    "one layer, also pass layers: an ordered array of {kind, text} entries with "
    "kinds from primary, commentary, marginalia, seal, and colophon, whose texts "
    "joined by single newlines equal the transcription. "
    "The tool call is the only accepted output; do not put the transcription in your "
    "final prose."
)
_DRAFT_TASK_SUFFIX = (
    " Independent machine drafts are staged under tools/ as draft-N.txt "
    "files. Read every draft and align its lines to the visible columns. Treat "
    "shared column sequence and character agreement as the default hypothesis. "
    "Where drafts disagree, inspect the page image and retain the majority reading "
    "unless the image decisively contradicts it."
)


def _submission_extension(*, accept_layers: bool) -> str:
    accept_layers_literal = "true" if accept_layers else "false"
    return f'''import {{ appendFile, writeFile }} from "node:fs/promises";
import {{ createHash }} from "node:crypto";
import {{ Buffer }} from "node:buffer";
import {{ join, resolve, sep }} from "node:path";
import type {{ ExtensionAPI }} from "@oh-my-pi/pi-coding-agent";

const MAX_TRANSCRIPTION_BYTES = {MAX_TRANSCRIPTION_BYTES};
const ARTIFACT_NAME = "{_ARTIFACT_NAME}";
const JOURNAL_NAME = "{_JOURNAL_NAME}";
const SEAL_NAME = "{_SEAL_NAME}";
const ACCEPT_LAYERS = {accept_layers_literal};

export default function submitTranscriptionExtension(pi: ExtensionAPI) {{
  const z = pi.zod;
  let submissionCount = 0;
  let acceptedArtifact: Buffer | undefined;

  pi.on("tool_call", async (event, ctx) => {{
    if (event.toolName === "submit_transcription") {{
      return;
    }}
    if (event.toolName !== "read") {{
      return {{ block: true, reason: "transcription cells allow only image read and submit_transcription" }};
    }}
    const requested = (event.input as {{ path?: unknown }}).path;
    if (typeof requested !== "string") {{
      return {{ block: true, reason: "read requires a staged image path" }};
    }}
    const imageRoot = resolve(ctx.cwd, "images");
    const toolsRoot = resolve(ctx.cwd, "tools");
    const target = resolve(ctx.cwd, requested);
    const within = (root: string) => target === root || target.startsWith(root + sep);
    if (!within(imageRoot) && !within(toolsRoot)) {{
      return {{ block: true, reason: "read is restricted to staged page images and tool artifacts" }};
    }}
  }});

  pi.registerTool({{
    name: "submit_transcription",
    label: "Submit Transcription",
    description: "Submit the complete diplomatic transcription exactly once.",
    loadMode: "essential",
    approval: "write",
    strict: true,
    parameters: z.object({{
      transcription: z.string().min(1).max(MAX_TRANSCRIPTION_BYTES),
      ...(ACCEPT_LAYERS ? {{
      layers: z.array(z.object({{
        kind: z.enum(["primary", "commentary", "marginalia", "seal", "colophon"]),
        text: z.string().min(1),
      }}).strict()).min(1).optional(),
      }} : {{}}),
    }}).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {{
      if (acceptedArtifact !== undefined) {{
        return {{
          content: [{{
            type: "text",
            text: "Transcription already accepted. End the session now.",
          }}],
          details: {{ submitted: true, alreadySubmitted: true }},
        }};
      }}
      if (!params.transcription.trim()) throw new Error("transcription must not be empty");
      if (Buffer.byteLength(params.transcription, "utf8") > MAX_TRANSCRIPTION_BYTES) {{
        throw new Error("transcription exceeds the byte limit");
      }}
      const layers = params.layers;
      if (layers !== undefined) {{
        if (!layers.some((layer) => layer.kind === "primary")) {{
          throw new Error("layered submissions require at least one primary layer");
        }}
        const assembled = layers.map((layer) => layer.text).join("\\n");
        if (assembled !== params.transcription) {{
          throw new Error("transcription must equal the layer texts joined by single newlines");
        }}
      }}
      const artifactText = JSON.stringify(
        layers === undefined
          ? {{ transcription: params.transcription }}
          : {{ transcription: params.transcription, layers }},
      ) + "\\n";
      const artifactBytes = Buffer.from(artifactText, "utf8");

      submissionCount += 1;
      await appendFile(join(ctx.cwd, "out", JOURNAL_NAME), artifactText, "utf8");
      await writeFile(join(ctx.cwd, "out", ARTIFACT_NAME), artifactBytes, {{ flag: "wx" }});
      acceptedArtifact = artifactBytes;
      const artifactSha256 = createHash("sha256").update(artifactBytes).digest("hex");
      const seal = JSON.stringify({{
        submission_count: submissionCount,
        artifact_sha256: artifactSha256,
      }}) + "\\n";
      await writeFile(join(ctx.cwd, "out", SEAL_NAME), seal, {{ flag: "wx" }});
      return {{
        content: [{{ type: "text", text: "Transcription accepted." }}],
        details: {{ submitted: true }},
      }};
    }},
  }});

  pi.on("session_start", async () => {{
    await pi.setActiveTools(["read", "submit_transcription"]);
  }});

}}
'''
_SUBMISSION_EXTENSION = _submission_extension(accept_layers=True)
_SUBMISSION_EXTENSION_BYTES = _SUBMISSION_EXTENSION.encode("utf-8")
_DRAFT_SUBMISSION_EXTENSION_BYTES = _submission_extension(
    accept_layers=False
).encode("utf-8")


class OmpExtensionTranscribe(Transcribe):
    """Run one OMP reader with only image-read and structured-submit tools."""

    variant = "omp_extension"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source", "tool_bindings"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _candidate_options(options)

    def run(self, job: Job) -> StationResult:
        source_bytes, tool_bindings = _candidate_options(job.config.options)
        page_image = job.path_of("page_image")
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        run_root = (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_extension"
        )
        workspace = agent_cell.stage_workspace(
            run_root / page_key,
            skill=job.config.prompt.text,
            evidence={},
            images=[page_image],
        )
        detail_tiles = _stage_detail_tiles(workspace, image=page_image)
        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True)
        (extension_dir / "00-submit-transcription.ts").write_bytes(
            _SUBMISSION_EXTENSION_BYTES
        )
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        task = _TASK
        scholar_timeout_s = TRANSCRIPTION_TIMEOUT_SECONDS
        draft_tokens = 0
        draft_cost: float | None = 0.0
        draft_binding = next(
            (binding for binding in tool_bindings if binding["kind"] == "draft_model"),
            None,
        )
        if draft_binding is not None:
            staged_drafts: list[tuple[str, str]] = []
            for draft_index, draft_name in enumerate(_DRAFT_NAMES, start=1):
                try:
                    draft_text, draft_run = _stage_draft(
                        run_root / f"{page_key}-draft-{draft_index}",
                        image=page_image,
                        model=draft_binding["model"],
                    )
                except agent_cell.AgentCellError:
                    # Drafts are staged evidence, never the result. A failed,
                    # empty, or timed-out read degrades to the remaining reads;
                    # if all fail, the scholar receives the draft-less task.
                    draft_cost = None
                    continue
                staged_drafts.append((draft_name, draft_text))
                draft_tokens += draft_run.tokens
                if draft_cost is not None:
                    if draft_run.cost_usd is None:
                        draft_cost = None
                    else:
                        draft_cost += draft_run.cost_usd

            if staged_drafts:
                tools_dir = workspace / "tools"
                tools_dir.mkdir()
                for draft_name, draft_text in staged_drafts:
                    (tools_dir / draft_name).write_text(
                        draft_text, encoding="utf-8", newline="\n"
                    )
                task = _TASK + _DRAFT_TASK_SUFFIX
                scholar_timeout_s = _DRAFT_SCHOLAR_TIMEOUT_SECONDS

        run = agent_cell.run(
            workspace,
            task,
            model=job.config.model,
            timeout_s=scholar_timeout_s,
            executor="omp",
            tool_names=("read",),
        )
        transcription, layers = _read_submission(workspace)
        page = job.page or {}
        payload: dict[str, Any] = {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": page.get("order", 0),
            "canvas_id": page.get("canvas_id", ""),
            "text": transcription,
            "requested_model": job.config.model,
            "model": job.config.model,
            "finish_reason": "submit_transcription",
        }
        if layers is not None:
            payload["layers"] = layers
        # Draft spend is real spend; draft behavior stays out of process_stats
        # because asi measures the scholar session, not its staged evidence.
        return StationResult(
            payload=payload,
            tokens_in=run.tokens + draft_tokens,
            cost_usd=None
            if run.cost_usd is None or draft_cost is None
            else run.cost_usd + draft_cost,
            process_stats=run.process_stats,
        )


def _stage_detail_tiles(workspace: Path, *, image: Path) -> tuple[Path, ...]:
    """Stage overlapping detail tiles for high-resolution visual verification."""

    decoded = cv2.imdecode(
        np.frombuffer(image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if decoded is None:
        raise ValueError(f"Cannot decode page image: {image}")
    height, width = decoded.shape[:2]
    overlap_y = max(1, height // _DETAIL_OVERLAP_DIVISOR)
    overlap_x = max(1, width // _DETAIL_OVERLAP_DIVISOR)
    detail_root = workspace / "images" / "details"
    detail_root.mkdir()
    staged: list[Path] = []
    for row in range(_DETAIL_ROWS):
        base_y0 = row * height // _DETAIL_ROWS
        base_y1 = (row + 1) * height // _DETAIL_ROWS
        y0 = max(0, base_y0 - (overlap_y if row > 0 else 0))
        y1 = min(
            height, base_y1 + (overlap_y if row + 1 < _DETAIL_ROWS else 0)
        )
        for column in range(_DETAIL_COLUMNS):
            base_x0 = column * width // _DETAIL_COLUMNS
            base_x1 = (column + 1) * width // _DETAIL_COLUMNS
            x0 = max(0, base_x0 - (overlap_x if column > 0 else 0))
            x1 = min(
                width,
                base_x1 + (overlap_x if column + 1 < _DETAIL_COLUMNS else 0),
            )
            encoded_ok, encoded = cv2.imencode(
                ".jpg",
                decoded[y0:y1, x0:x1],
                [cv2.IMWRITE_JPEG_QUALITY, _DETAIL_JPEG_QUALITY],
            )
            if not encoded_ok:
                raise RuntimeError(
                    f"Cannot encode page detail tile row={row + 1}, column={column + 1}"
                )
            tile = detail_root / f"detail-r{row + 1}-c{column + 1}.jpg"
            tile.write_bytes(encoded.tobytes())
            staged.append(tile)
    return tuple(staged)




def _stage_draft(
    root: Path,
    *,
    image: Path,
    model: str,
) -> tuple[str, agent_cell.AgentRun]:
    """Run the Exodia-bound draft model; its output is evidence, not a result."""

    draft_workspace = agent_cell.stage_workspace(
        root,
        skill=_DRAFT_SKILL,
        evidence={},
        images=[image],
    )
    extension_dir = draft_workspace / ".omp" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "00-submit-transcription.ts").write_bytes(
        _DRAFT_SUBMISSION_EXTENSION_BYTES
    )
    draft_run = agent_cell.run(
        draft_workspace,
        _DRAFT_TASK,
        model=model,
        timeout_s=_DRAFT_TIMEOUT_SECONDS,
        executor="omp",
        tool_names=("read",),
    )
    draft_text, _ = _read_submission(draft_workspace)
    return draft_text, draft_run


def _encoded_extension_source(source: Any) -> bytes:
    if not isinstance(source, str) or not source.strip():
        raise TypeError("extension_source must be a non-empty string")
    try:
        encoded = source.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("extension_source must be valid UTF-8") from error
    if len(encoded) > MAX_EXTENSION_BYTES:
        raise ValueError(
            f"extension_source exceeds the {MAX_EXTENSION_BYTES}-byte limit"
        )
    return encoded


def _extension_source_bytes(options: Mapping[str, Any]) -> bytes:
    """Exact single-option contract kept for the toolbelt station variants."""

    if set(options) != {"extension_source"}:
        missing = sorted({"extension_source"} - set(options))
        unknown = sorted(set(options) - {"extension_source"})
        raise ValueError(
            f"Expected only extension_source; missing={missing}, unknown={unknown}"
        )
    return _encoded_extension_source(options["extension_source"])


def _candidate_options(
    options: Mapping[str, Any],
) -> tuple[bytes, tuple[dict[str, str], ...]]:
    allowed = {"extension_source", "tool_bindings"}
    missing = sorted({"extension_source"} - set(options))
    unknown = sorted(set(options) - allowed)
    if missing or unknown:
        raise ValueError(
            "Expected extension_source with optional tool_bindings; "
            f"missing={missing}, unknown={unknown}"
        )
    encoded = _encoded_extension_source(options["extension_source"])
    bindings_value = options.get("tool_bindings")
    if bindings_value is None:
        return encoded, ()
    if (
        isinstance(bindings_value, (str, bytes))
        or not isinstance(bindings_value, (list, tuple))
        or not bindings_value
    ):
        raise TypeError("tool_bindings must be a non-empty list")
    previous: str | None = None
    collected: list[dict[str, str]] = []
    kinds: set[str] = set()
    for index, binding_value in enumerate(bindings_value):
        field = f"tool_bindings[{index}]"
        if not isinstance(binding_value, Mapping) or set(binding_value) != {
            "id",
            "kind",
            "model",
        }:
            raise ValueError(f"{field} must contain only id, kind, and model")
        binding: dict[str, str] = {}
        for key in ("id", "kind", "model"):
            value = binding_value[key]
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"{field}.{key} must be a non-empty string")
            binding[key] = value
        if previous is not None and previous >= binding["id"]:
            raise ValueError("tool_bindings must be sorted and unique by id")
        if binding["kind"] != "draft_model":
            raise ValueError(
                f"tool kind {binding['kind']!r} is not stageable by the transcribe station"
            )
        if binding["kind"] in kinds:
            raise ValueError(
                f"tool_bindings repeats stageable kind {binding['kind']!r}"
            )
        previous = binding["id"]
        kinds.add(binding["kind"])
        collected.append(binding)
    return encoded, tuple(collected)


def _read_submission(
    workspace: Path,
) -> tuple[str, list[dict[str, str]] | None]:
    out = workspace / "out"
    artifact_path = out / _ARTIFACT_NAME
    journal_path = out / _JOURNAL_NAME
    seal_path = out / _SEAL_NAME
    if not artifact_path.is_file():
        raise agent_cell.AgentCellError(
            f"agent did not submit {_ARTIFACT_NAME} through submit_transcription"
        )
    artifact_bytes = _bounded_bytes(
        artifact_path, maximum=_MAX_ARTIFACT_BYTES, label="transcription artifact"
    )
    artifact = _json_object(artifact_bytes, label="transcription artifact")
    if not {"transcription"} <= set(artifact) <= {"transcription", "layers"}:
        raise agent_cell.AgentCellError(
            "transcription artifact must contain transcription and optional layers"
        )
    text = artifact["transcription"]
    if not isinstance(text, str):
        raise agent_cell.AgentCellError("submitted transcription must be a string")
    if not text.strip():
        raise agent_cell.AgentCellError("submitted transcription must not be empty")
    try:
        text_bytes = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise agent_cell.AgentCellError(
            "submitted transcription must contain valid Unicode"
        ) from error
    if len(text_bytes) > MAX_TRANSCRIPTION_BYTES:
        raise agent_cell.AgentCellError(
            f"submitted transcription exceeds {MAX_TRANSCRIPTION_BYTES} bytes"
        )
    layers_value = artifact.get("layers")
    layers: list[dict[str, str]] | None = None
    if layers_value is not None:
        if not isinstance(layers_value, list) or not layers_value:
            raise agent_cell.AgentCellError(
                "submitted layers must be a non-empty array"
            )
        layers = []
        for entry in layers_value:
            if not isinstance(entry, dict) or set(entry) != {"kind", "text"}:
                raise agent_cell.AgentCellError(
                    "each layer must contain exactly kind and text"
                )
            kind = entry["kind"]
            layer_text = entry["text"]
            if kind not in _LAYER_KINDS:
                raise agent_cell.AgentCellError(f"unknown layer kind {kind!r}")
            if not isinstance(layer_text, str) or not layer_text.strip():
                raise agent_cell.AgentCellError("layer text must be a non-empty string")
            layers.append({"kind": kind, "text": layer_text})
        if all(layer["kind"] != "primary" for layer in layers):
            raise agent_cell.AgentCellError(
                "layered submission requires a primary layer"
            )
        if "\n".join(layer["text"] for layer in layers) != text:
            raise agent_cell.AgentCellError(
                "layers do not assemble into the submitted transcription"
            )

    journal_bytes = _bounded_bytes(
        journal_path,
        maximum=_MAX_ARTIFACT_BYTES * 2,
        label="transcription submission journal",
    )
    journal_lines = journal_bytes.splitlines()
    if len(journal_lines) != 1:
        raise agent_cell.AgentCellError(
            "expected exactly one submit_transcription call; "
            f"observed {len(journal_lines)}"
        )
    journal_entry = _json_object(
        journal_lines[0], label="transcription submission journal entry"
    )
    if journal_entry != artifact:
        raise agent_cell.AgentCellError(
            "transcription artifact does not match its structured submission"
        )

    seal_bytes = _bounded_bytes(
        seal_path, maximum=512, label="transcription submission seal"
    )
    seal = _json_object(seal_bytes, label="transcription submission seal")
    if set(seal) != {"submission_count", "artifact_sha256"}:
        raise agent_cell.AgentCellError("transcription submission seal is malformed")
    if type(seal["submission_count"]) is not int or seal["submission_count"] != 1:
        raise agent_cell.AgentCellError(
            "transcription submission count must be exactly one"
        )
    if seal["artifact_sha256"] != hashlib.sha256(artifact_bytes).hexdigest():
        raise agent_cell.AgentCellError(
            "transcription artifact changed after structured submission"
        )
    return text.strip(), layers


def _bounded_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
    except OSError as error:
        raise agent_cell.AgentCellError(
            f"agent did not produce {label}: {path}"
        ) from error
    if len(data) > maximum:
        raise agent_cell.AgentCellError(f"{label} exceeds {maximum} bytes")
    return data


def _json_object(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise agent_cell.AgentCellError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise agent_cell.AgentCellError(f"{label} must be a JSON object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Never:
    raise ValueError(f"non-standard JSON constant: {value}")


_SHA256_HEX = 64
_FOREMAN_TASK = (
    "Audit and correct the staged base transcription. Read tools/base.txt, "
    "tools/context.md, and tools/sensors.json. For every flagged line, inspect "
    "its magnified crop under tools/crops/ (and the page image and tiles under "
    "images/) and decide each disagreement_span explicitly: confirm the base "
    "or adopt the printed alternative. Then call submit_transcription exactly "
    "once with the complete final transcription. The tool call is the only "
    "accepted output; do not put the transcription in final prose."
)
_SENSOR_ARTIFACT_KEYS = frozenset(
    {"detections_sha256", "classifier_verdicts_sha256"}
)


def _instrumented_options(
    options: Mapping[str, Any],
) -> tuple[bytes, dict[str, str], dict[str, str], int]:
    allowed = {"extension_source", "tool_bindings", "sensors", "quiet_max_disagreements"}
    required = {"extension_source", "tool_bindings", "sensors"}
    missing = sorted(required - set(options))
    unknown = sorted(set(options) - allowed)
    if missing or unknown:
        raise ValueError(
            "Expected extension_source, tool_bindings, sensors, and optional "
            f"quiet_max_disagreements; missing={missing}, unknown={unknown}"
        )
    encoded = _encoded_extension_source(options["extension_source"])

    bindings_value = options["tool_bindings"]
    if (
        isinstance(bindings_value, (str, bytes))
        or not isinstance(bindings_value, (list, tuple))
        or len(bindings_value) != 1
    ):
        raise ValueError("tool_bindings must contain exactly one base engine binding")
    binding_value = bindings_value[0]
    if not isinstance(binding_value, Mapping) or set(binding_value) != {
        "id",
        "kind",
        "model",
    }:
        raise ValueError("tool_bindings[0] must contain only id, kind, and model")
    binding: dict[str, str] = {}
    for key in ("id", "kind", "model"):
        value = binding_value[key]
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"tool_bindings[0].{key} must be a non-empty string")
        binding[key] = value
    if binding["kind"] != "draft_model":
        raise ValueError("the instrumented base engine binding must be a draft_model")

    sensors_value = options["sensors"]
    if not isinstance(sensors_value, Mapping) or set(sensors_value) != _SENSOR_ARTIFACT_KEYS:
        raise ValueError(
            "sensors must contain exactly detections_sha256 and classifier_verdicts_sha256"
        )
    sensors: dict[str, str] = {}
    for key in sorted(_SENSOR_ARTIFACT_KEYS):
        value = sensors_value[key]
        if (
            not isinstance(value, str)
            or len(value) != _SHA256_HEX
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError(f"sensors.{key} must be a lowercase sha256 hex digest")
        sensors[key] = value

    quiet_value = options.get("quiet_max_disagreements", 5)
    if isinstance(quiet_value, bool) or not isinstance(quiet_value, int):
        raise TypeError("quiet_max_disagreements must be an integer")
    return encoded, binding, sensors, quiet_value


class OmpInstrumentedTranscribe(Transcribe):
    """The instrumented rig: base read, sensors, quiet gate, foreman audit.

    The candidate model is the escalation foreman; the base and shadow reads
    run on the bound draft_model engine. Sensor artifacts are content-addressed
    objects verified before use. Measured lineage: exodia experiments 22-34.
    """

    variant = "omp_instrumented"
    param_keys = frozenset()
    option_keys = frozenset(
        {"extension_source", "tool_bindings", "sensors", "quiet_max_disagreements"}
    )
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/instrumented_sensors.py",
        "factory/recognized_text.py",
    )

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _instrumented_options(options)

    def _load_object(self, job: Job, sha256_hex: str, label: str) -> Path:
        path = job.library_root / "evaluations" / "objects" / sha256_hex
        if not path.is_file():
            raise FileNotFoundError(f"{label} object is missing: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sha256_hex:
            raise ValueError(f"{label} object content drifted: {digest}")
        return path

    def run(self, job: Job) -> StationResult:
        source_bytes, binding, sensor_digests, quiet_max = _instrumented_options(
            job.config.options
        )
        page_image = job.path_of("page_image")
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        run_root = (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_instrumented"
        )

        base_text, base_run = _stage_draft(
            run_root / f"{page_key}-base",
            image=page_image,
            model=binding["model"],
        )
        tokens = base_run.tokens
        cost: float | None = base_run.cost_usd
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
            if shadow_text != base_text:
                alternates.append(shadow_text)
            tokens += shadow_run.tokens
            if cost is not None:
                cost = (
                    None
                    if shadow_run.cost_usd is None
                    else cost + shadow_run.cost_usd
                )

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
        sensors, flags = instrumented_sensors.compute_sensors(
            base_text, characters, alternates, verdict_columns
        )

        page = job.page or {}
        payload: dict[str, Any] = {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": page.get("order", 0),
            "canvas_id": page.get("canvas_id", ""),
            "requested_model": job.config.model,
            "model": job.config.model,
            "base_model": binding["model"],
            "sensor_flags": flags,
        }
        if instrumented_sensors.is_quiet(flags, quiet_max):
            payload["text"] = base_text.strip()
            payload["finish_reason"] = "quiet_page_base_adopted"
            payload["changed_lines"] = 0
            return StationResult(
                payload=payload,
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
            "- Dataset: historical Chinese xylograph corpus.",
            f"- Document: {job.doc_id}",
            f"- Page id: {job.page_id}.",
            "- Genre: Buddhist canon print (Tripitaka family). Formulaic litany and",
            "  repeated parallel phrases are normal. The final printed column usually",
            "  carries the scripture title, scroll number, sheet number, and a",
            "  collation cipher.",
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
        transcription, layers = _read_submission(workspace)
        changed = sum(
            1
            for base_line, final_line in zip(
                base_text.splitlines(), transcription.splitlines()
            )
            if base_line != final_line
        )
        payload["text"] = transcription
        payload["finish_reason"] = "submit_transcription"
        payload["changed_lines"] = changed
        if layers is not None:
            payload["layers"] = layers
        return StationResult(
            payload=payload,
            tokens_in=tokens + run.tokens,
            cost_usd=None
            if cost is None or run.cost_usd is None
            else cost + run.cost_usd,
            process_stats=run.process_stats,
        )


register(OmpExtensionTranscribe())
register(OmpInstrumentedTranscribe())
