"""Tool-bearing transcription, seventh iteration: agent-directed glyph inspection.

``omp_toolbelt7`` keeps v6.1's guarded reading-order reconciliation and the
post-submit blind adjudicator. It adds a bounded agent-facing inspection tool:
every detector cell has a provenance-preserving crop, its column/position, the
aligned second-reader character when alignment is exact, and optional local
classifier top-k evidence. The agent chooses which ambiguous cells to inspect;
no classifier output changes transcription text by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import _extension_source_bytes
from palimpsest.factory.stations.transcribe_omp_toolbelt import (
    _OBJECT_ROOT_ENV,
    _checkpoint_path,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt2 import (
    _read_layered_submission,
    _station_usage_with_second_reader,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt3 import (
    _TOOLBELT3_EXTENSION_BYTES,
    TRANSCRIPTION_TIMEOUT_SECONDS,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt5 import (
    _adjudicate_glyphs,
    _adjudication_crop,
    _stage_geometry,
    _squeeze,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt6 import _reorder_layers

_IMAGE_SIZE = 64
_IMAGENET_MEAN = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
_IMAGENET_STD = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)
_CLASSIFIER_ONNX_SHA256 = (
    "edd81d3fcc089488fc79571701bdf2fdeb23bed50850bd8d911f9dc71c361f75"
)
_INSPECTION_JOURNAL = ".glyph-inspections.jsonl"
_MAX_INSPECTION_JOURNAL_BYTES = 64 * 1024
_INSPECTION_MANIFEST = ".glyph-inspection-private.json"
_MAX_INSPECTION_CALLS = 8
_SHA256_LENGTH = 64
_CLASSIFIER_CLASSES_SHA256 = (
    "a53da8698ee4b191d1f41860750b8d0efd216b421c0a72b169488aed1301a5a3"
)

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
    "commentary drafts. Select at most eight highest-impact character disputes from "
    "the flagged columns. Call inspect_glyph only when you already have at least two "
    "literal alternatives grounded in the page, using the disputed cell's zero-based "
    "geometry column and top-to-bottom position. The tool returns that crop inline "
    "and scores only your supplied alternatives; it does not propose characters. "
    "Decide from the visible strokes first. Second-reader and local-classifier signals "
    "are independent evidence, never authority. Do not inspect clean glyphs or sweep "
    "the page. Fix every repetition loop and re-read every flagged column, then call "
    "submit_transcription exactly "
    "once with both final fields. The tool call is the only accepted output; do not "
    "put the transcription in your final prose."
)


def _toolbelt7_policy_extension() -> bytes:
    source = _TOOLBELT3_EXTENSION_BYTES.decode("utf-8")
    replacements = {
        'if (event.toolName === "verify_layers") {': (
            'if (event.toolName === "verify_layers" || '
            'event.toolName === "inspect_glyph") {'
        ),
        "toolbelt cells allow only read, verify_layers, and submit_transcription": (
            "toolbelt cells allow only read, verify_layers, inspect_glyph, and "
            "submit_transcription"
        ),
        'await pi.setActiveTools(["read", "verify_layers", "submit_transcription"]);': (
            'await pi.setActiveTools(["read", "verify_layers", "inspect_glyph", '
            '"submit_transcription"]);'
        ),
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise RuntimeError(f"toolbelt3 extension contract changed: {old}")
        source = source.replace(old, new)
    return source.encode("utf-8")


_TOOLBELT7_POLICY_EXTENSION_BYTES = _toolbelt7_policy_extension()

_INSPECTION_EXTENSION = r"""import { createHash } from "node:crypto";
import { appendFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

interface ClassifierChoice {
  character: string;
  probability: number;
  logit: number;
}

interface GlyphEvidence {
  column: number;
  position: number;
  layer: string;
  bbox: [number, number, number, number];
  crop: string;
  second_reader_character: string | null;
  classifier: null | {
    top_k: ClassifierChoice[];
    margin: number;
  };
}
const MAX_INSPECTION_CALLS = %MAX_INSPECTION_CALLS%;
const PRIVATE_MANIFEST = "%PRIVATE_MANIFEST%";


export default function inspectionExtension(pi: ExtensionAPI) {
  const z = pi.zod;
  let inspectionCalls = 0;
  pi.registerTool({
    name: "inspect_glyph",
    label: "Inspect Glyph",
    description:
      "Inspect one detector cell selected by zero-based geometry column and " +
      "top-to-bottom position. Returns crop-local evidence only for literal " +
      "alternatives already supplied by the agent; it never proposes characters. " +
      "The selected crop is returned inline.",
    loadMode: "essential",
    approval: "none",
    strict: true,
    parameters: z.object({
      column: z.number().int().min(0),
      position: z.number().int().min(0),
      candidates: z.array(z.string().min(1).max(2)).min(2).max(4),
    }).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      if (inspectionCalls >= MAX_INSPECTION_CALLS) {
        throw new Error("inspect_glyph reached its per-page call limit");
      }
      inspectionCalls += 1;
      const raw = await readFile(join(ctx.cwd, PRIVATE_MANIFEST), "utf8");
      const payload = JSON.parse(raw) as { glyphs: GlyphEvidence[] };
      const glyph = payload.glyphs.find(
        (item) => item.column === params.column && item.position === params.position,
      );
      if (glyph === undefined) {
        throw new Error(
          "no detector cell at column " + String(params.column) +
          ", position " + String(params.position),
        );
      }
      const crop = await readFile(join(ctx.cwd, "evidence", glyph.crop));
      const cropSha256 = createHash("sha256").update(crop).digest("hex");
      const choices = params.candidates.map((candidate) => {
        const topK = glyph.classifier?.top_k ?? [];
        const classifierIndex = topK.findIndex(
          (item) => item.character === candidate,
        );
        const classifier = classifierIndex < 0 ? undefined : topK[classifierIndex];
        return {
          candidate,
          matches_second_reader: glyph.second_reader_character === candidate,
          classifier_probability: classifier?.probability ?? null,
          classifier_rank: classifierIndex < 0 ? null : classifierIndex + 1,
        };
      });
      const result = {
        column: glyph.column,
        position: glyph.position,
        layer: glyph.layer,
        bbox: glyph.bbox,
        crop: glyph.crop,
        second_reader_character: glyph.second_reader_character,
        candidates: choices,
        note:
          "The selected crop is attached to this tool result. Decide from its visible " +
          "strokes. The reader and classifier are independent suggestions, not " +
          "permission to change a character without crop-local evidence.",
      };
      await appendFile(
        join(ctx.cwd, "out", ".glyph-inspections.jsonl"),
        JSON.stringify({
          column: glyph.column,
          position: glyph.position,
          candidates: params.candidates,
          crop_sha256: cropSha256,
        }) + "\n",
        "utf8",
      );
      return {
        content: [
          { type: "text", text: JSON.stringify(result) },
          { type: "image", data: crop.toString("base64"), mimeType: "image/png" },
        ],
        details: {
          inspected: true,
          column: glyph.column,
          position: glyph.position,
          crop_sha256: cropSha256,
        },
      };
    },
  });
}
""".replace("%MAX_INSPECTION_CALLS%", str(_MAX_INSPECTION_CALLS)).replace(
    "%PRIVATE_MANIFEST%", _INSPECTION_MANIFEST
)
_INSPECTION_EXTENSION_BYTES = _INSPECTION_EXTENSION.encode("utf-8")


def _artifact_paths(job: Job) -> tuple[Path, Path]:
    onnx_sha = _CLASSIFIER_ONNX_SHA256
    classes_sha = _CLASSIFIER_CLASSES_SHA256
    configured = os.getenv(_OBJECT_ROOT_ENV)
    root = (
        Path(configured) if configured else job.library_root / "evaluations" / "objects"
    )
    onnx_path = root / onnx_sha
    classes_path = root / classes_sha
    if not onnx_path.is_file() or not classes_path.is_file():
        raise FileNotFoundError("glyph classifier objects are not materialized")
    return onnx_path, classes_path


def _classifier_top_k(
    inputs: list[np.ndarray], onnx_path: Path, classes_path: Path
) -> list[dict[str, object]]:
    classes = json.loads(classes_path.read_text(encoding="utf-8"))
    if (
        not isinstance(classes, list)
        or not classes
        or not all(isinstance(character, str) and character for character in classes)
    ):
        raise ValueError(
            "glyph classifier classes object must be a non-empty string list"
        )
    network = cv2.dnn.readNetFromONNX(str(onnx_path))
    results: list[dict[str, object]] = []
    for start in range(0, len(inputs), 256):
        batch = np.stack(inputs[start : start + 256])
        network.setInput(batch)
        logits = np.asarray(network.forward(), dtype=np.float32)
        if logits.ndim == 1:
            logits = logits[None, :]
        if logits.shape[1] != len(classes):
            raise ValueError("glyph classifier output width does not match classes")
        shifted = logits - logits.max(axis=1, keepdims=True)
        probabilities = np.exp(shifted)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        top_indices = np.argpartition(logits, -5, axis=1)[:, -5:]
        for row_index, indices in enumerate(top_indices):
            ordered = indices[np.argsort(logits[row_index, indices])[::-1]]
            top_k = [
                {
                    "character": classes[int(index)],
                    "probability": round(float(probabilities[row_index, index]), 6),
                    "logit": round(float(logits[row_index, index]), 6),
                }
                for index in ordered
            ]
            results.append(
                {
                    "top_k": top_k,
                    "margin": round(top_k[0]["logit"] - top_k[1]["logit"], 6),
                }
            )
    return results


def _inspection_usage(workspace: Path) -> dict[str, object]:
    journal = workspace / "out" / _INSPECTION_JOURNAL
    try:
        payload = journal.read_bytes()
    except FileNotFoundError:
        return {"calls": 0, "unique_glyphs": 0, "inline_crop_reads": 0}
    if len(payload) > _MAX_INSPECTION_JOURNAL_BYTES:
        raise agent_cell.AgentCellError(
            "glyph inspection journal exceeds its byte limit"
        )
    rows = []
    for line in payload.splitlines():
        value = json.loads(line)
        if (
            not isinstance(value, dict)
            or set(value) != {"column", "position", "candidates", "crop_sha256"}
            or not isinstance(value["column"], int)
            or not isinstance(value["position"], int)
            or not isinstance(value["candidates"], list)
            or not isinstance(value["crop_sha256"], str)
            or len(value["crop_sha256"]) != _SHA256_LENGTH
            or any(
                character not in "0123456789abcdef"
                for character in value["crop_sha256"]
            )
        ):
            raise agent_cell.AgentCellError("glyph inspection journal is malformed")
        rows.append(value)
    if len(rows) > _MAX_INSPECTION_CALLS:
        raise agent_cell.AgentCellError(
            "glyph inspection journal exceeds its call limit"
        )
    unique = {(row["column"], row["position"]) for row in rows}
    return {
        "calls": len(rows),
        "unique_glyphs": len(unique),
        "inline_crop_reads": len(rows),
    }


def _stage_inspection(
    workspace: Path,
    image: np.ndarray,
    columns: list[dict[str, object]],
    artifacts: tuple[Path, Path] | None,
) -> dict[str, object]:
    glyph_root = workspace / "evidence" / "glyphs"
    glyph_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    classifier_inputs: list[np.ndarray] = []
    for column_index, column in enumerate(columns):
        boxes = column["boxes"]
        if not isinstance(boxes, list):
            raise ValueError("adjudication column boxes must be a list")
        reader, _ = _squeeze(str(column.get("second_reader") or ""))
        reader_aligned = len(reader) == len(boxes)
        for position, box in enumerate(boxes):
            crop = _adjudication_crop(image, box)
            if crop is None:
                raise RuntimeError(
                    f"cannot stage glyph crop for column {column_index}, position {position}"
                )
            name = f"c{column_index:03d}p{position:03d}.png"
            (glyph_root / name).write_bytes(crop)
            if artifacts is not None:
                decoded = cv2.imdecode(
                    np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if decoded is None:
                    raise RuntimeError(f"cannot decode staged glyph crop: {name}")
                resized = cv2.resize(
                    decoded, (_IMAGE_SIZE, _IMAGE_SIZE), interpolation=cv2.INTER_AREA
                )
                rgb = (
                    cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                )
                normalized = (rgb - _IMAGENET_MEAN) / _IMAGENET_STD
                classifier_inputs.append(np.transpose(normalized, (2, 0, 1)))
            records.append(
                {
                    "column": column_index,
                    "position": position,
                    "layer": column["layer"],
                    "bbox": box,
                    "crop": f"glyphs/{name}",
                    "second_reader_character": reader[position]
                    if reader_aligned
                    else None,
                    "classifier": None,
                }
            )
    if artifacts is not None:
        predictions = _classifier_top_k(classifier_inputs, artifacts[0], artifacts[1])
        for record, prediction in zip(records, predictions, strict=True):
            record["classifier"] = prediction
    payload = {
        "schema_version": 1,
        "classifier": (
            None
            if artifacts is None
            else {
                "onnx_sha256": artifacts[0].name,
                "classes_sha256": artifacts[1].name,
            }
        ),
        "glyphs": records,
    }
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (workspace / _INSPECTION_MANIFEST).write_bytes(encoded)
    return {
        "glyphs": len(records),
        "classifier_enabled": artifacts is not None,
        "inspection_sha256": hashlib.sha256(encoded).hexdigest(),
    }


class OmpToolbelt7Transcribe(Transcribe):
    """v6.1 ordering and adjudication with agent-directed glyph inspection."""

    variant = "omp_toolbelt7"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/transcribe_omp_toolbelt2.py",
        "factory/stations/transcribe_omp_toolbelt3.py",
        "factory/stations/transcribe_omp_toolbelt5.py",
        "factory/stations/transcribe_omp_toolbelt6.py",
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
        geometry_summary, adjudication_columns = _stage_geometry(
            workspace, staged_images[0], _checkpoint_path(job)
        )
        image = cv2.imdecode(
            np.frombuffer(staged_images[0].read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise RuntimeError("cannot decode staged page image for inspection")
        inspection = _stage_inspection(
            workspace, image, adjudication_columns, _artifact_paths(job)
        )

        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / "00-toolbelt7-policy.ts").write_bytes(
            _TOOLBELT7_POLICY_EXTENSION_BYTES
        )
        (extension_dir / "01-inspection.ts").write_bytes(_INSPECTION_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        run = agent_cell.run(
            workspace,
            _TASK,
            model=job.config.model,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        inspection["usage"] = _inspection_usage(workspace)
        primary, commentary = _read_layered_submission(workspace)
        primary, commentary, reorder = _reorder_layers(
            adjudication_columns,
            primary,
            commentary,
            bool(geometry_summary["two_layer"]),
        )
        primary, adjudication = _adjudicate_glyphs(
            image, adjudication_columns, primary, page_key
        )
        page = job.page or {}
        tokens, cost_usd = _station_usage_with_second_reader(
            run.tokens,
            run.cost_usd,
            geometry_summary,
            extra_cost_usd=float(adjudication["cost_usd"]),
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
                "toolbelt": {
                    **geometry_summary,
                    "inspection": inspection,
                    "reorder": reorder,
                    "adjudication": adjudication,
                },
            },
            tokens_in=tokens,
            cost_usd=cost_usd,
            process_stats=run.process_stats,
        )

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt7"
        )


register(OmpToolbelt7Transcribe())
