"""Tool-bearing agent-cell transcription: geometry evidence plus verification.

The ``omp_toolbelt`` variant extends the ``omp_extension`` cell with
host-owned, deterministic geometry evidence. Before the session starts, the
station runs the pinned production RF-DETR runtime once on the staged page
image, clusters the detections into right-to-left columns, and stages:

- ``evidence/geometry.json``: image size, box totals, and per-column bounding
  boxes with expected character counts;
- ``evidence/overlay.jpg``: the page with every detection outlined;
- ``evidence/columns/cNN.png``: one padded crop per detected column.

The host extension grants exactly three tools: ``read`` (restricted to
``images/`` and ``evidence/``), the deterministic ``verify_counts`` check of a
draft transcription against the detector's column counts, and the single-use
``submit_transcription``. The candidate extension remains pure policy; every
tool and all evidence are host-authored, so the proposer cannot influence
them.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations.align_rfdetr import (
    _detected_columns,
    _parse_detections,
)
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import (
    MAX_TRANSCRIPTION_BYTES,
    _extension_source_bytes,
    _read_submission,
)

_RUNTIME_PYTHON_ENV = "PALIMPSEST_RFDETR_PYTHON"
_OBJECT_ROOT_ENV = "PALIMPSEST_RFDETR_OBJECT_ROOT"
_DETECTOR_TIMEOUT_SECONDS = 900
TRANSCRIPTION_TIMEOUT_SECONDS = 900

# The production align pins from recipes/chinese_printed_book.yaml: the same
# frozen checkpoint and inference settings production trusts for geometry.
DETECTOR_OPTIONS = {
    "checkpoint_sha256": "cdc06d36dd2273e139571b3196d58c13dee11211ec847fadffa9fee3af46624d",
    "rfdetr_version": "1.8.3",
    "torch_version": "2.7.0+cu118",
    "torchvision_version": "0.22.0+cu118",
    "tile_size": 512,
    "overlap": 96,
    "threshold": 0.31,
    "nms_iou": 0.4,
}
_OVERLAY_MAX_SIDE = 1600
_CROP_PAD_FRACTION = 0.25

_TASK = (
    "Transcribe the single page image staged in images/. Follow AGENTS.md and the "
    "transcription extension's policy. Detector evidence is staged under evidence/: "
    "read evidence/geometry.json for the column map, evidence/overlay.jpg to see "
    "every detected character, and evidence/columns/cNN.png for a legible crop of "
    "each column (c00 is the rightmost, first-read column). Draft the complete "
    "diplomatic transcription, call verify_counts to compare it against the "
    "detector's per-column character counts, revise any flagged column from its "
    "crop, then call submit_transcription exactly once with the final text. The "
    "tool call is the only accepted output; do not put the transcription in your "
    "final prose."
)

_TOOLBELT_EXTENSION = f"""import {{ appendFile, readFile, writeFile }} from "node:fs/promises";
import {{ createHash }} from "node:crypto";
import {{ Buffer }} from "node:buffer";
import {{ join, resolve, sep }} from "node:path";
import type {{ ExtensionAPI }} from "@oh-my-pi/pi-coding-agent";

const MAX_TRANSCRIPTION_BYTES = {MAX_TRANSCRIPTION_BYTES};
const ARTIFACT_NAME = "transcription.json";
const JOURNAL_NAME = ".transcription-submissions.jsonl";
const SEAL_NAME = ".transcription-submission-seal.json";

interface GeometryColumn {{
  index: number;
  boxes: number;
}}

function nonSpaceLength(value: string): number {{
  let count = 0;
  for (const character of value) {{
    if (!/\\s/u.test(character)) count += 1;
  }}
  return count;
}}

export default function toolbeltTranscriptionExtension(pi: ExtensionAPI) {{
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
    if (event.toolName === "verify_counts") {{
      return;
    }}
    if (event.toolName !== "read") {{
      return {{ block: true, reason: "toolbelt cells allow only read, verify_counts, and submit_transcription" }};
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
    name: "verify_counts",
    label: "Verify Counts",
    description:
      "Deterministically compare a draft transcription against the detector's " +
      "per-column character counts. Returns totals, the page ratio, and the " +
      "columns whose counts disagree most.",
    loadMode: "essential",
    approval: "none",
    strict: true,
    parameters: z.object({{
      transcription: z.string().min(1).max(MAX_TRANSCRIPTION_BYTES),
    }}).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {{
      const raw = await readFile(join(ctx.cwd, "evidence", "geometry.json"), "utf8");
      const geometry = JSON.parse(raw) as {{
        detected_boxes: number;
        columns: GeometryColumn[];
      }};
      const lines = params.transcription
        .split(/\\r?\\n/u)
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      const lineCounts = lines.map((line) => nonSpaceLength(line));
      const totalCharacters = nonSpaceLength(params.transcription);
      const shared = Math.min(lineCounts.length, geometry.columns.length);
      const aligned = [] as Array<{{
        column: number;
        expected: number;
        actual: number;
        delta: number;
      }}>;
      for (let index = 0; index < shared; index += 1) {{
        const expected = geometry.columns[index].boxes;
        const actual = lineCounts[index];
        aligned.push({{ column: index, expected, actual, delta: actual - expected }});
      }}
      const disagreements = aligned
        .filter((entry) => entry.delta !== 0)
        .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
        .slice(0, 10);
      const verdict = {{
        total_characters: totalCharacters,
        detected_boxes: geometry.detected_boxes,
        ratio: geometry.detected_boxes === 0
          ? null
          : totalCharacters / geometry.detected_boxes,
        transcription_lines: lineCounts.length,
        detected_columns: geometry.columns.length,
        extra_lines: Math.max(0, lineCounts.length - geometry.columns.length),
        missing_columns: Math.max(0, geometry.columns.length - lineCounts.length),
        worst_disagreements: disagreements,
        note:
          "Counts are geometry evidence, not authority: the detector can miss " +
          "or over-segment. Re-read the flagged column crops before changing text.",
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
    description: "Submit the complete diplomatic transcription exactly once.",
    loadMode: "essential",
    approval: "write",
    strict: true,
    parameters: z.object({{
      transcription: z.string().min(1).max(MAX_TRANSCRIPTION_BYTES),
    }}).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {{
      const artifactText = JSON.stringify({{ transcription: params.transcription }}) + "\\n";
      const artifactBytes = Buffer.from(artifactText, "utf8");

      if (sealed) throw new Error("transcription submission arrived after the turn closed");
      if (acceptedArtifact !== undefined) throw new Error("duplicate transcription submission");
      if (!params.transcription.trim()) throw new Error("transcription must not be empty");
      if (Buffer.byteLength(params.transcription, "utf8") > MAX_TRANSCRIPTION_BYTES) {{
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
    await pi.setActiveTools(["read", "verify_counts", "submit_transcription"]);
  }});

  pi.on("agent_end", async () => {{
    sealed = true;
  }});
}}
"""
_TOOLBELT_EXTENSION_BYTES = _TOOLBELT_EXTENSION.encode("utf-8")


def _runtime_python() -> Path:
    configured = os.getenv(_RUNTIME_PYTHON_ENV)
    return Path(configured) if configured else Path(sys.executable)


def _checkpoint_path(job: Job) -> Path:
    configured = os.getenv(_OBJECT_ROOT_ENV)
    root = (
        Path(configured) if configured else job.library_root / "evaluations" / "objects"
    )
    return root / str(DETECTOR_OPTIONS["checkpoint_sha256"])


def _detect(source: Path, checkpoint: Path) -> dict[str, object]:
    runtime = _runtime_python()
    if not runtime.is_file():
        raise FileNotFoundError(f"Missing isolated RF-DETR runtime: {runtime}")
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Missing RF-DETR checkpoint: {checkpoint}")
    script = Path(__file__).with_name("align_rfdetr_runtime.py")
    command = [
        str(runtime),
        str(script),
        str(source),
        str(checkpoint),
        str(DETECTOR_OPTIONS["checkpoint_sha256"]),
        str(DETECTOR_OPTIONS["rfdetr_version"]),
        str(DETECTOR_OPTIONS["torch_version"]),
        str(DETECTOR_OPTIONS["torchvision_version"]),
        "--tile-size",
        str(DETECTOR_OPTIONS["tile_size"]),
        "--overlap",
        str(DETECTOR_OPTIONS["overlap"]),
        "--threshold",
        str(DETECTOR_OPTIONS["threshold"]),
        "--nms-iou",
        str(DETECTOR_OPTIONS["nms_iou"]),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_DETECTOR_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"RF-DETR one-shot inference failed: {detail[-2000:]}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("RF-DETR one-shot inference produced no output")
    response = json.loads(lines[-1])
    if not isinstance(response, dict):
        raise RuntimeError("RF-DETR one-shot inference returned a non-object")
    return response


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

    evidence_root = workspace / "evidence"
    columns_root = evidence_root / "columns"
    columns_root.mkdir(parents=True, exist_ok=True)

    overlay = image.copy()
    for detection in detections:
        cell = detection.cell
        cv2.rectangle(overlay, (cell.x0, cell.y0), (cell.x1, cell.y1), (0, 200, 0), 2)
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
        crop_name = f"c{index:02d}.png"
        ok, crop_payload = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError(f"failed to encode column crop {crop_name}")
        (columns_root / crop_name).write_bytes(crop_payload.tobytes())
        column_records.append(
            {
                "index": index,
                "bbox": [left, top, right - left, bottom - top],
                "boxes": len(column),
                "median_box_width": round(
                    statistics.median(item.width for item in column), 1
                ),
                "crop": f"columns/{crop_name}",
            }
        )

    geometry = {
        "schema_version": 1,
        "image_size": [width, height],
        "detected_boxes": len(detections),
        "columns_right_to_left": True,
        "detector": {
            **DETECTOR_OPTIONS,
            "model_load_seconds": inference.get("model_load_seconds"),
            "inference_seconds": inference.get("inference_seconds"),
        },
        "columns": column_records,
    }
    geometry_bytes = (
        json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (evidence_root / "geometry.json").write_bytes(geometry_bytes)
    return {
        "detected_boxes": len(detections),
        "columns": len(column_records),
        "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
        "inference_seconds": inference.get("inference_seconds"),
    }


class OmpToolbeltTranscribe(Transcribe):
    """Run one OMP reader with detector evidence and a count-verification tool."""

    variant = "omp_toolbelt"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
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
        (extension_dir / "00-toolbelt.ts").write_bytes(_TOOLBELT_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        run = agent_cell.run(
            workspace,
            _TASK,
            model=job.config.model,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        transcription = _read_submission(workspace)
        page = job.page or {}
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": page.get("order", 0),
                "canvas_id": page.get("canvas_id", ""),
                "text": transcription,
                "requested_model": job.config.model,
                "model": job.config.model,
                "finish_reason": "submit_transcription",
                "toolbelt": geometry_summary,
            },
            tokens_in=run.tokens,
            cost_usd=run.cost_usd,
            process_stats=run.process_stats,
        )

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt"
        )


register(OmpToolbeltTranscribe())
