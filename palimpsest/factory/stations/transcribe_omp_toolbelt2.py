"""Tool-bearing agent-cell transcription, second iteration.

``omp_toolbelt2`` repairs the two failures measured on
transcribe/ancientdoc-development/v1 smoke (record
transcribe-toolbelt-ancientdoc-development-v1):

- Box-complete transcription lost wherever a volume's gold excludes
  commentary. The submission is now layered: ``primary`` full-size text and
  ``commentary`` half-width text are separate fields, verified separately
  against a deterministic width-split of the detected boxes.
- Crop-first reading degraded ordinary pages. The task now reads the full
  page first and reserves column crops for verification of flagged columns.

New evidence channel: every column is independently transcribed at staging
time by a second reader (gemini-3.5-flash, minimal thinking, count-anchored
ceiling — the identity validated in read-column-agreement-development-v1).
Where the agent and the second reader agree, measured precision on healthy
pages is 0.95-1.00, so ``verify_layers`` reports which columns the second
reader contradicts and the agent re-reads only those crops.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.gateway import GatewayError, ImageContent, ModelRequest
from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.usage import combine_cost, combine_count
from palimpsest.factory.stations.align_rfdetr import (
    _detected_columns,
    _parse_detections,
)
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import (
    MAX_TRANSCRIPTION_BYTES,
    _bounded_bytes,
    _extension_source_bytes,
    _json_object,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt import (
    _checkpoint_path,
    _detect,
)

TRANSCRIPTION_TIMEOUT_SECONDS = 900
_OVERLAY_MAX_SIDE = 1600
_CROP_PAD_FRACTION = 0.25
# Commentary is ~0.5x primary width; clusters closer than this are one layer.
_MAX_WIDTH_RATIO = 0.72
_MIN_LAYER_FRACTION = 0.15
_SECOND_READER_MODEL = "gemini-3.5-flash"
_SECOND_READER_THINKING = "minimal"
_ARTIFACT_NAME = "transcription.json"
_JOURNAL_NAME = ".transcription-submissions.jsonl"
_SEAL_NAME = ".transcription-submission-seal.json"
_MAX_ARTIFACT_BYTES = MAX_TRANSCRIPTION_BYTES * 6 + 64

_SECOND_READER_PROMPT = (
    "Transcribe all visible text in this image exactly as written. The image "
    "is ONE vertical column cropped from a premodern East Asian page, read "
    "top to bottom. A character detector estimates this column contains "
    "approximately {count} characters. If the strip holds two half-width "
    "sub-columns of small commentary text, read the right sub-column top to "
    "bottom first, then the left. Preserve the characters exactly as written. "
    "Do not add punctuation, do not modernize, do not translate, do not "
    "describe the image. If a character is illegible, write \u3014?\u3015 "
    "once and continue. Output one continuous string without line breaks. "
    'Respond as JSON: {{"transcription": "<the column text>"}}'
)
_SECOND_READER_SCHEMA = {
    "type": "object",
    "properties": {"transcription": {"type": "string"}},
    "required": ["transcription"],
    "additionalProperties": False,
}

_TASK = (
    "Transcribe the single page image staged in images/ into two layers. Follow "
    "AGENTS.md and the transcription extension's policy. Read the FULL PAGE image "
    "first and draft the complete transcription in natural reading order; the page "
    "is the sole authority. Detector evidence is staged under evidence/: "
    "geometry.json maps every column (index 0 is the rightmost, first-read column) "
    "with its expected primary and commentary character counts and an independent "
    "second reader's transcription of that column; overlay.jpg outlines primary "
    "boxes in green and commentary boxes in orange; evidence/columns/cNN.png is an "
    "enlarged crop of each column. After drafting, call verify_layers with your "
    "primary and commentary drafts, re-read the crops of only the columns it "
    "flags, then call submit_transcription exactly once with both final fields. "
    "The tool call is the only accepted output; do not put the transcription in "
    "your final prose."
)

_TOOLBELT2_EXTENSION = f'''import {{ appendFile, readFile, writeFile }} from "node:fs/promises";
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

export default function toolbelt2Extension(pi: ExtensionAPI) {{
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
      "Deterministically compare layered drafts against detector counts and " +
      "the independent second reader. Returns totals per layer and the " +
      "columns whose second-reader text is absent from your draft.",
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
        primary_boxes: number;
        commentary_boxes: number;
        columns: GeometryColumn[];
      }};
      const primary = nonSpace(params.primary);
      const commentary = nonSpace(params.commentary);
      const combined = primary + commentary;
      const disagreements = [] as Array<{{
        column: number;
        layer: string;
        second_reader_characters: number;
        note: string;
      }}>;
      let agreements = 0;
      let unavailable = 0;
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
          layer: column.layer,
          second_reader_characters: reading.length,
          note: "second reader read \\"" + head + "\\"; not found in your draft",
        }});
      }}
      const verdict = {{
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
        columns_agreeing_with_second_reader: agreements,
        columns_without_second_reader: unavailable,
        columns_to_recheck: disagreements.slice(0, 12),
        note:
          "Counts and the second reader are evidence, not authority. Where " +
          "you and the second reader agree the text is almost always right; " +
          "re-read the flagged columns' crops before changing anything.",
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
_TOOLBELT2_EXTENSION_BYTES = _TOOLBELT2_EXTENSION.encode("utf-8")


def _two_split(widths: list[float]) -> tuple[float, float, float] | None:
    """Optimal 1-D two-split of sorted widths, or None below eight boxes."""

    ordered = sorted(widths)
    count = len(ordered)
    if count < 8:
        return None
    prefix = [0.0]
    prefix_squares = [0.0]
    for width in ordered:
        prefix.append(prefix[-1] + width)
        prefix_squares.append(prefix_squares[-1] + width * width)

    def sse(start: int, stop: int) -> float:
        size = stop - start
        total = prefix[stop] - prefix[start]
        squares = prefix_squares[stop] - prefix_squares[start]
        return squares - total * total / size

    best: tuple[float, int] | None = None
    for split in range(2, count - 1):
        cost = sse(0, split) + sse(split, count)
        if best is None or cost < best[0]:
            best = (cost, split)
    split = best[1]
    small = ordered[:split]
    large = ordered[split:]
    return (
        (small[-1] + large[0]) / 2,
        statistics.fmean(small),
        statistics.fmean(large),
    )


def _second_reader_cap(expected_characters: int) -> int:
    return min(2560, max(1024, expected_characters * 8 + 64))


@dataclass(frozen=True)
class _SecondReaderResult:
    reading: str | None
    tokens: int | None
    cost_usd: float | None


def _read_second_opinion(
    crop_png: bytes, expected_characters: int
) -> _SecondReaderResult:
    try:
        value, response = generate_json(
            ModelRequest(
                model=_SECOND_READER_MODEL,
                prompt=_SECOND_READER_PROMPT.format(count=expected_characters),
                system=(
                    "You are an expert paleographer transcribing digitized "
                    "manuscript pages."
                ),
                images=(ImageContent(data=crop_png, mime="image/png"),),
                temperature=0.1,
                max_output_tokens=_second_reader_cap(expected_characters),
                media_resolution="high",
                json_output=True,
                json_schema=_SECOND_READER_SCHEMA,
                thinking_level=_SECOND_READER_THINKING,
                allow_empty=True,
            )
        )
    except GatewayError as error:
        return _SecondReaderResult(
            reading=None,
            tokens=combine_count(error.tokens_in, error.tokens_out),
            cost_usd=error.cost_usd,
        )
    reading = value.get("transcription")
    return _SecondReaderResult(
        reading=reading if isinstance(reading, str) else None,
        tokens=response.total_tokens,
        cost_usd=response.cost_usd,
    )


def _station_usage_with_second_reader(
    tokens: int | None,
    cost_usd: float | None,
    summary: dict[str, object],
    *,
    extra_cost_usd: float | None = 0.0,
) -> tuple[int | None, float | None]:
    reader_tokens = summary.get("second_reader_tokens")
    if reader_tokens is not None and (
        isinstance(reader_tokens, bool)
        or not isinstance(reader_tokens, int)
        or reader_tokens < 0
    ):
        raise RuntimeError("second-reader token usage is malformed")
    reader_cost = summary.get("second_reader_cost_usd")
    if reader_cost is not None and (
        isinstance(reader_cost, bool)
        or not isinstance(reader_cost, (int, float))
        or reader_cost < 0
    ):
        raise RuntimeError("second-reader cost usage is malformed")
    return (
        combine_count(tokens, reader_tokens),
        combine_cost(
            combine_cost(cost_usd, None if reader_cost is None else float(reader_cost)),
            extra_cost_usd,
        ),
    )


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
    columns = _detected_columns(detections)

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
    for index, column in enumerate(columns):
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
            raise RuntimeError(f"failed to encode column crop c{index:02d}")
        crop_bytes = crop_payload.tobytes()
        crop_name = f"c{index:02d}.png"
        (columns_root / crop_name).write_bytes(crop_bytes)

        primary_boxes = sum(1 for item in column if layer_of(item) == "primary")
        second_reader = _read_second_opinion(crop_bytes, len(column))
        second_reader_tokens = combine_count(second_reader_tokens, second_reader.tokens)
        second_reader_cost_usd = combine_cost(
            second_reader_cost_usd, second_reader.cost_usd
        )
        second_reading = second_reader.reading
        if second_reading is None:
            second_reader_failures += 1
        column_records.append(
            {
                "index": index,
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

    primary_total = sum(int(record["primary_boxes"]) for record in column_records)
    commentary_total = sum(int(record["commentary_boxes"]) for record in column_records)
    geometry = {
        "schema_version": 2,
        "image_size": [width, height],
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
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
        "columns": len(column_records),
        "second_reader_failures": second_reader_failures,
        "second_reader_tokens": second_reader_tokens,
        "second_reader_cost_usd": second_reader_cost_usd,
        "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
        "inference_seconds": inference.get("inference_seconds"),
    }


def _read_layered_submission(workspace: Path) -> tuple[str, str]:
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
    if set(artifact) != {"transcription", "commentary"}:
        raise agent_cell.AgentCellError(
            "transcription artifact must contain transcription and commentary"
        )
    primary = artifact["transcription"]
    commentary = artifact["commentary"]
    if not isinstance(primary, str) or not isinstance(commentary, str):
        raise agent_cell.AgentCellError("submitted layers must be strings")
    if not primary.strip():
        raise agent_cell.AgentCellError("submitted primary text must not be empty")

    journal_bytes = _bounded_bytes(
        journal_path,
        maximum=_MAX_ARTIFACT_BYTES * 2,
        label="transcription submission journal",
    )
    journal_lines = journal_bytes.splitlines()
    if not journal_lines:
        raise agent_cell.AgentCellError(
            "expected at least one timely submit_transcription call; observed none"
        )
    journal_entry = _json_object(
        journal_lines[0], label="transcription submission journal entry"
    )
    if journal_entry != {"primary": primary, "commentary": commentary}:
        raise agent_cell.AgentCellError(
            "transcription artifact does not match its structured submission"
        )

    seal_bytes = _bounded_bytes(
        seal_path, maximum=512, label="transcription submission seal"
    )
    seal = _json_object(seal_bytes, label="transcription submission seal")
    if set(seal) != {"submission_count", "artifact_sha256"}:
        raise agent_cell.AgentCellError("transcription submission seal is malformed")
    # First-wins: the wx artifact write and the seal digest make the first
    # accepted submission unambiguous; a rejected retry must not destroy it.
    if type(seal["submission_count"]) is not int or seal["submission_count"] < 1:
        raise agent_cell.AgentCellError(
            "transcription submission arrived after closure"
        )
    if seal["artifact_sha256"] != hashlib.sha256(artifact_bytes).hexdigest():
        raise agent_cell.AgentCellError(
            "transcription artifact changed after structured submission"
        )
    return primary.strip(), commentary.strip()


class OmpToolbelt2Transcribe(Transcribe):
    """Layered, page-first reader with geometry and second-reader verification."""

    variant = "omp_toolbelt2"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
        "factory/gateway/client.py",
        "factory/gateway/gemini.py",
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
        (extension_dir / "00-toolbelt2.ts").write_bytes(_TOOLBELT2_EXTENSION_BYTES)
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
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt2"
        )


register(OmpToolbelt2Transcribe())
