"""Tool-bearing agent-cell transcription, third iteration.

``omp_toolbelt3`` targets the residue measured on real gold in
transcribe-toolbelt2-mthv2-development-v1:

- Case-level repetition survived on two dense Gaoli commentary pages inside
  a passing aggregate. ``verify_layers`` now mirrors the suite's
  ``repetition_rate`` exactly (fraction of nonblank lines in 3+-copy loops)
  per layer, so the agent sees the gate value before submitting.
- The count anchors did not bind two-register layouts. Detections now split
  into horizontal registers (page-wide y-gaps wider than 1.8x the median
  box height) before right-to-left column clustering, and every column
  carries its register.
- The detector-failure lesson from the page adjudicator: when detected
  coverage cannot explain the drafted text (combined ratio outside
  [0.6, 1.6] or fewer than 40 boxes), ``verify_layers`` declares the
  detector untrusted, suppresses per-column flags, and tells the agent to
  trust the page image.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.usage import combine_cost, combine_count
from palimpsest.factory.stations.align_rfdetr import (
    _detected_columns,
    _parse_detections,
)
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import (
    MAX_TRANSCRIPTION_BYTES,
    _extension_source_bytes,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt import (
    _checkpoint_path,
    _detect,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt2 import (
    _MAX_WIDTH_RATIO,
    _MIN_LAYER_FRACTION,
    _SECOND_READER_MODEL,
    _SECOND_READER_THINKING,
    _read_layered_submission,
    _read_second_opinion,
    _station_usage_with_second_reader,
    _two_split,
)

TRANSCRIPTION_TIMEOUT_SECONDS = 900
_OVERLAY_MAX_SIDE = 1600
_CROP_PAD_FRACTION = 0.25
_REGISTER_GAP_FACTOR = 1.8
_ARTIFACT_NAME = "transcription.json"
_JOURNAL_NAME = ".transcription-submissions.jsonl"
_SEAL_NAME = ".transcription-submission-seal.json"
_MAX_ARTIFACT_BYTES = MAX_TRANSCRIPTION_BYTES * 6 + 64

_TASK = (
    "Transcribe the single page image staged in images/ into two layers. Follow "
    "AGENTS.md and the transcription extension's policy. Read the FULL PAGE image "
    "first and draft the complete transcription in natural reading order; the page "
    "is the sole authority. Detector evidence is staged under evidence/: "
    "geometry.json maps every register and column in reading order with expected "
    "primary and commentary character counts and an independent second reader's "
    "transcription per column; overlay.jpg outlines primary boxes in green and "
    "commentary boxes in orange; evidence/columns/rRcNN.png is an enlarged crop of "
    "each column. After drafting, call verify_layers with your primary and "
    "commentary drafts. Fix every repetition loop it reports by re-reading the "
    "page region: repeated identical lines are almost always a drafting error. "
    "If it declares the detector untrusted, ignore counts and column flags and "
    "work purely from the page. Re-read the crops of flagged columns, then call "
    "submit_transcription exactly once with both final fields. The tool call is "
    "the only accepted output; do not put the transcription in your final prose."
)

_TOOLBELT3_EXTENSION = f'''import {{ appendFile, readFile, writeFile }} from "node:fs/promises";
import {{ createHash }} from "node:crypto";
import {{ Buffer }} from "node:buffer";
import {{ join, resolve, sep }} from "node:path";
import type {{ ExtensionAPI }} from "@oh-my-pi/pi-coding-agent";

const MAX_TRANSCRIPTION_BYTES = {MAX_TRANSCRIPTION_BYTES};
const ARTIFACT_NAME = "{_ARTIFACT_NAME}";
const JOURNAL_NAME = "{_JOURNAL_NAME}";
const SEAL_NAME = "{_SEAL_NAME}";

interface GeometryColumn {{
  index: number;
  register: number;
  layer: string;
  boxes: number;
  primary_boxes: number;
  commentary_boxes: number;
  second_reader: string | null;
}}

function nonSpace(value: string): string {{
  let out = "";
  for (const character of value) {{
    if (!/\\s/u.test(character)) out += character;
  }}
  return out;
}}

function repetitionReport(value: string): {{
  rate: number;
  loops: Array<{{ line: string; count: number }}>;
}} {{
  const lines = value
    .normalize("NFC")
    .replace(/\\r\\n?/gu, "\\n")
    .split("\\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length === 0) return {{ rate: 0, loops: [] }};
  const counts = new Map<string, number>();
  for (const line of lines) counts.set(line, (counts.get(line) ?? 0) + 1);
  let repeated = 0;
  const loops: Array<{{ line: string; count: number }}> = [];
  for (const [line, count] of counts) {{
    if (count > 2) {{
      repeated += count;
      loops.push({{
        line: line.length > 12 ? line.slice(0, 12) + "…" : line,
        count,
      }});
    }}
  }}
  loops.sort((a, b) => b.count - a.count);
  return {{ rate: repeated / lines.length, loops: loops.slice(0, 5) }};
}}

export default function toolbelt3Extension(pi: ExtensionAPI) {{
  const z = pi.zod;
  let submissionCount = 0;
  let acceptedArtifact: Buffer | undefined;
  let sealed = false;

  pi.on("tool_call", async (event, ctx) => {{
    if (event.toolName === "submit_transcription") {{
      submissionCount += 1;
      const journalEntry = JSON.stringify(event.input) + "\\n";
      await appendFile(join(ctx.cwd, "out", JOURNAL_NAME), journalEntry, "utf8");
      return;
    }}
    if (event.toolName === "verify_layers") {{
      return;
    }}
    if (event.toolName !== "read") {{
      return {{ block: true, reason: "toolbelt cells allow only read, verify_layers, and submit_transcription" }};
    }}
    const requested = (event.input as {{ path?: unknown }}).path;
    if (typeof requested !== "string") {{
      return {{ block: true, reason: "read requires a staged file path" }};
    }}
    const imageRoot = resolve(ctx.cwd, "images");
    const evidenceRoot = resolve(ctx.cwd, "evidence");
    const target = resolve(ctx.cwd, requested);
    const insideImages = target === imageRoot || target.startsWith(imageRoot + sep);
    const insideEvidence = target === evidenceRoot || target.startsWith(evidenceRoot + sep);
    if (!insideImages && !insideEvidence) {{
      return {{ block: true, reason: "read is restricted to staged images and detector evidence" }};
    }}
  }});

  pi.registerTool({{
    name: "verify_layers",
    label: "Verify Layers",
    description:
      "Deterministically compare layered drafts against detector counts, the " +
      "independent second reader, and the suite's repetition gate. Reports " +
      "repetition loops per layer, layer totals, detector trust, and the " +
      "columns whose second reading is absent from your draft.",
    loadMode: "essential",
    approval: "none",
    strict: true,
    parameters: z.object({{
      primary: z.string().min(1).max(MAX_TRANSCRIPTION_BYTES),
      commentary: z.string().max(MAX_TRANSCRIPTION_BYTES),
    }}).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {{
      const raw = await readFile(join(ctx.cwd, "evidence", "geometry.json"), "utf8");
      const geometry = JSON.parse(raw) as {{
        detected_boxes: number;
        primary_boxes: number;
        commentary_boxes: number;
        registers: number;
        columns: GeometryColumn[];
      }};
      const primary = nonSpace(params.primary);
      const commentary = nonSpace(params.commentary);
      const combined = primary + commentary;
      const combinedRatio = geometry.detected_boxes === 0
        ? null
        : combined.length / geometry.detected_boxes;
      const detectorTrusted =
        geometry.detected_boxes >= 40 &&
        combinedRatio !== null &&
        combinedRatio >= 0.6 &&
        combinedRatio <= 1.6;

      const primaryRepetition = repetitionReport(params.primary);
      const commentaryRepetition = repetitionReport(params.commentary);

      const disagreements = [] as Array<{{
        column: number;
        register: number;
        layer: string;
        second_reader_characters: number;
        note: string;
      }}>;
      let agreements = 0;
      let unavailable = 0;
      if (detectorTrusted) {{
        for (const column of geometry.columns) {{
          if (column.second_reader === null) {{
            unavailable += 1;
            continue;
          }}
          const reading = nonSpace(column.second_reader);
          if (reading.length === 0) {{
            unavailable += 1;
            continue;
          }}
          if (combined.includes(reading)) {{
            agreements += 1;
            continue;
          }}
          const head = reading.length > 12 ? reading.slice(0, 12) + "…" : reading;
          disagreements.push({{
            column: column.index,
            register: column.register,
            layer: column.layer,
            second_reader_characters: reading.length,
            note: "second reader read \\"" + head + "\\"; not found in your draft",
          }});
        }}
      }}
      const verdict = {{
        detector_trusted: detectorTrusted,
        detector_note: detectorTrusted
          ? "detector coverage is plausible; counts and column flags are usable evidence"
          : "detector coverage cannot explain the draft (boxes " +
            String(geometry.detected_boxes) + ", ratio " +
            String(combinedRatio === null ? "unknown" : combinedRatio.toFixed(2)) +
            "); trust the page image and ignore counts and column flags",
        repetition: {{
          gate: "suite hard limit fails at 0.1; repeated identical lines in 3+ copies count",
          primary_rate: primaryRepetition.rate,
          primary_loops: primaryRepetition.loops,
          commentary_rate: commentaryRepetition.rate,
          commentary_loops: commentaryRepetition.loops,
        }},
        primary_characters: primary.length,
        primary_boxes: geometry.primary_boxes,
        primary_ratio: geometry.primary_boxes === 0
          ? null
          : primary.length / geometry.primary_boxes,
        commentary_characters: commentary.length,
        commentary_boxes: geometry.commentary_boxes,
        commentary_ratio: geometry.commentary_boxes === 0
          ? null
          : commentary.length / geometry.commentary_boxes,
        registers: geometry.registers,
        columns_agreeing_with_second_reader: agreements,
        columns_without_second_reader: unavailable,
        columns_to_recheck: disagreements.slice(0, 12),
        note:
          "Counts and the second reader are evidence, not authority. Where you " +
          "and the second reader agree the text is almost always right; re-read " +
          "flagged columns' crops before changing anything, and eliminate every " +
          "repetition loop unless the page truly repeats the line three times.",
      }};
      return {{
        content: [{{ type: "text", text: JSON.stringify(verdict) }}],
        details: {{ checked: true }},
      }};
    }},
  }});

  pi.registerTool({{
    name: "submit_transcription",
    label: "Submit Transcription",
    description:
      "Submit the final layered diplomatic transcription exactly once: the " +
      "primary full-size text and the half-width commentary text.",
    loadMode: "essential",
    approval: "write",
    strict: true,
    parameters: z.object({{
      primary: z.string().min(1).max(MAX_TRANSCRIPTION_BYTES),
      commentary: z.string().max(MAX_TRANSCRIPTION_BYTES),
    }}).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {{
      const artifactText = JSON.stringify({{
        transcription: params.primary,
        commentary: params.commentary,
      }}) + "\\n";
      const artifactBytes = Buffer.from(artifactText, "utf8");

      if (sealed) throw new Error("transcription submission arrived after the turn closed");
      if (acceptedArtifact !== undefined) throw new Error("duplicate transcription submission");
      if (!params.primary.trim()) throw new Error("primary transcription must not be empty");
      if (Buffer.byteLength(artifactText, "utf8") > {_MAX_ARTIFACT_BYTES}) {{
        throw new Error("transcription exceeds the byte limit");
      }}

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
    await pi.setActiveTools(["read", "verify_layers", "submit_transcription"]);
  }});

  pi.on("agent_end", async () => {{
    sealed = true;
  }});
}}
'''
_TOOLBELT3_EXTENSION_BYTES = _TOOLBELT3_EXTENSION.encode("utf-8")


def _split_registers(detections):
    """Split detections into horizontal bands at page-wide y-gaps."""

    if not detections:
        return []
    median_height = statistics.median(
        item.cell.y1 - item.cell.y0 for item in detections
    )
    ordered = sorted(detections, key=lambda item: item.cell.y0)
    registers = [[ordered[0]]]
    register_bottom = ordered[0].cell.y1
    for detection in ordered[1:]:
        if detection.cell.y0 - register_bottom > _REGISTER_GAP_FACTOR * median_height:
            registers.append([detection])
        else:
            registers[-1].append(detection)
        register_bottom = max(register_bottom, detection.cell.y1)
    return registers


def _stage_geometry(
    workspace: Path, staged_image: Path, checkpoint: Path
) -> dict[str, object]:
    image = cv2.imdecode(
        np.frombuffer(staged_image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        raise RuntimeError(f"cannot decode staged page image: {staged_image}")
    height, width = image.shape[:2]

    inference = _detect(staged_image, checkpoint)
    if inference.get("image_size") != [width, height]:
        raise RuntimeError("RF-DETR inference image size does not match the page")
    detections = _parse_detections(
        inference.get("boxes"), image_width=width, image_height=height
    )

    split = _two_split([float(d.width) for d in detections])
    threshold = None
    two_layer = False
    if split is not None:
        threshold, small_center, large_center = split
        small_fraction = sum(1 for d in detections if float(d.width) < threshold) / max(
            len(detections), 1
        )
        two_layer = (
            small_center / large_center <= _MAX_WIDTH_RATIO
            and _MIN_LAYER_FRACTION <= small_fraction <= 1 - _MIN_LAYER_FRACTION
        )

    def layer_of(detection) -> str:
        if two_layer and float(detection.width) < threshold:
            return "commentary"
        return "primary"

    evidence_root = workspace / "evidence"
    columns_root = evidence_root / "columns"
    columns_root.mkdir(parents=True, exist_ok=True)

    overlay = image.copy()
    for detection in detections:
        cell = detection.cell
        color = (0, 200, 0) if layer_of(detection) == "primary" else (0, 140, 255)
        cv2.rectangle(overlay, (cell.x0, cell.y0), (cell.x1, cell.y1), color, 2)
    scale = _OVERLAY_MAX_SIDE / max(overlay.shape[:2])
    if scale < 1.0:
        overlay = cv2.resize(
            overlay,
            (round(overlay.shape[1] * scale), round(overlay.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, payload = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("failed to encode detection overlay")
    (evidence_root / "overlay.jpg").write_bytes(payload.tobytes())

    column_records: list[dict[str, object]] = []
    second_reader_failures = 0
    second_reader_tokens: int | None = 0
    second_reader_cost_usd: float | None = 0.0
    global_index = 0
    registers = _split_registers(detections)
    for register_index, register_detections in enumerate(registers):
        for column_index, column in enumerate(_detected_columns(register_detections)):
            left = min(item.cell.x0 for item in column)
            top = min(item.cell.y0 for item in column)
            right = max(item.cell.x1 for item in column)
            bottom = max(item.cell.y1 for item in column)
            pad = max(8, round((right - left) * _CROP_PAD_FRACTION))
            crop = image[
                max(0, top - pad) : min(height, bottom + pad),
                max(0, left - pad) : min(width, right + pad),
            ]
            ok, crop_payload = cv2.imencode(".png", crop)
            if not ok:
                raise RuntimeError(
                    f"failed to encode column crop r{register_index}c{column_index:02d}"
                )
            crop_bytes = crop_payload.tobytes()
            crop_name = f"r{register_index}c{column_index:02d}.png"
            (columns_root / crop_name).write_bytes(crop_bytes)

            primary_boxes = sum(1 for item in column if layer_of(item) == "primary")
            second_reader = _read_second_opinion(crop_bytes, len(column))
            second_reader_tokens = combine_count(
                second_reader_tokens, second_reader.tokens
            )
            second_reader_cost_usd = combine_cost(
                second_reader_cost_usd, second_reader.cost_usd
            )
            second_reading = second_reader.reading
            if second_reading is None:
                second_reader_failures += 1
            column_records.append(
                {
                    "index": global_index,
                    "register": register_index,
                    "bbox": [left, top, right - left, bottom - top],
                    "boxes": len(column),
                    "primary_boxes": primary_boxes,
                    "commentary_boxes": len(column) - primary_boxes,
                    "layer": "primary"
                    if primary_boxes * 2 >= len(column)
                    else "commentary",
                    "crop": f"columns/{crop_name}",
                    "second_reader": second_reading,
                }
            )
            global_index += 1

    primary_total = sum(int(record["primary_boxes"]) for record in column_records)
    commentary_total = sum(int(record["commentary_boxes"]) for record in column_records)
    geometry = {
        "schema_version": 3,
        "image_size": [width, height],
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
        "registers": len(registers),
        "columns_right_to_left": True,
        "second_reader": {
            "model": _SECOND_READER_MODEL,
            "thinking_level": _SECOND_READER_THINKING,
            "failures": second_reader_failures,
        },
        "columns": column_records,
    }
    geometry_bytes = (
        json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (evidence_root / "geometry.json").write_bytes(geometry_bytes)
    return {
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
        "registers": len(registers),
        "columns": len(column_records),
        "second_reader_failures": second_reader_failures,
        "second_reader_tokens": second_reader_tokens,
        "second_reader_cost_usd": second_reader_cost_usd,
        "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
        "inference_seconds": inference.get("inference_seconds"),
    }


class OmpToolbelt3Transcribe(Transcribe):
    """Layered page-first reader with registers, repetition, and trust checks."""

    variant = "omp_toolbelt3"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/transcribe_omp_toolbelt2.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
    )

    def validate_options(self, options) -> None:
        _extension_source_bytes(options)

    def run(self, job: Job) -> StationResult:
        source_bytes = _extension_source_bytes(job.config.options)
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        workspace = agent_cell.stage_workspace(
            self._workspace_root(job) / page_key,
            skill=job.config.prompt.text,
            evidence={},
            images=[job.path_of("page_image")],
        )
        staged_images = sorted((workspace / "images").glob("*"))
        if len(staged_images) != 1:
            raise RuntimeError("toolbelt cell expects exactly one staged page image")
        geometry_summary = _stage_geometry(
            workspace, staged_images[0], _checkpoint_path(job)
        )

        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / "00-toolbelt3.ts").write_bytes(_TOOLBELT3_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        run = agent_cell.run(
            workspace,
            _TASK,
            model=job.config.model,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        primary, commentary = _read_layered_submission(workspace)
        page = job.page or {}
        tokens, cost_usd = _station_usage_with_second_reader(
            run.tokens, run.cost_usd, geometry_summary
        )
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": page.get("order", 0),
                "canvas_id": page.get("canvas_id", ""),
                "text": primary,
                "commentary": commentary,
                "requested_model": job.config.model,
                "model": job.config.model,
                "finish_reason": "submit_transcription",
                "toolbelt": geometry_summary,
            },
            tokens_in=tokens,
            cost_usd=cost_usd,
            process_stats=run.process_stats,
        )

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt3"
        )


register(OmpToolbelt3Transcribe())
