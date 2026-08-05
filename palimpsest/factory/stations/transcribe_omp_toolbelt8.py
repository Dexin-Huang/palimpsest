"""Luna-first semantic-region transcription with contextual Gemini readings.

The variant preserves the ``transcribe`` socket.  RF-DETR supplies cell anchors,
Luna maps every anchored cell into an ordered semantic region graph, Python
validates the graph and creates contextual crops, Gemini reads those regions,
and the same Luna session resumes to submit one text per validated region.
Diplomatic text is rendered deterministically from the sealed region order.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.gateway.protocol import GatewayError, ImageContent, ModelRequest
from palimpsest.factory.stations.align_rfdetr import (
    _detected_columns,
    _parse_detections,
)
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import _extension_source_bytes
from palimpsest.factory.stations.transcribe_omp_toolbelt import (
    _checkpoint_path,
    _detect,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt2 import (
    _MAX_WIDTH_RATIO,
    _MIN_LAYER_FRACTION,
    _two_split,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt3 import (
    _CROP_PAD_FRACTION,
    _OVERLAY_MAX_SIDE,
    TRANSCRIPTION_TIMEOUT_SECONDS,
    _split_registers,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt5 import (
    _adjudicate_glyphs,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt7 import (
    _INSPECTION_EXTENSION_BYTES,
    _TOOLBELT7_POLICY_EXTENSION_BYTES,
    _artifact_paths,
    _inspection_usage,
    _stage_inspection,
)
from palimpsest.factory.usage import combine_cost, combine_count

_LAYOUT_TIMEOUT_SECONDS = 360
_LAYOUT_REVIEW_TIMEOUT_SECONDS = 240
_REGION_READER_MODEL = "gemini-3.5-flash"
_REGION_READER_THINKING = "high"
_REGION_READER_MEDIA_RESOLUTION = "high"
_MAX_REGIONS = 20
_MAX_REGION_SPANS = 64
_MAX_REGION_TEXT_BYTES = 128 * 1024
_REGION_ID = re.compile(r"^r\d{3}$")
_REGION_MAP_NAME = "region-map.json"
_REGION_READINGS_NAME = "region-readings.json"
_REGIONAL_SUBMISSION_NAME = "regional-transcription.json"

_ROLES = frozenset(
    {
        "title",
        "running_header",
        "folio_marker",
        "main_body",
        "interlinear_commentary",
        "marginalia",
        "alternate_title",
        "translator_attribution",
        "catalogue_group_label",
        "colophon",
        "seal_or_nontext",
    }
)
_ATTACHMENT_ROLES = frozenset(
    {
        "interlinear_commentary",
        "marginalia",
        "alternate_title",
        "translator_attribution",
        "catalogue_group_label",
    }
)
_LAYERS = frozenset({"primary", "commentary", "nontext"})

_LAYOUT_TASK = """Map the large semantic pieces of the single page before any second reader is consulted.

Read the full image in images/ and evidence/overlay.jpg first. evidence/geometry.json lists every detector column and every top-to-bottom cell bbox. Call submit_region_map exactly once. Partition every detector cell into at most 20 semantic regions. A span is a contiguous half-open cell range within one column: start <= position < end. Every cell must belong to exactly one span across the whole map.

Use roles precisely: title, running_header, folio_marker, main_body, interlinear_commentary, marginalia, alternate_title, translator_attribution, catalogue_group_label, colophon, or seal_or_nontext. Set layer primary for source text, commentary for small annotations, and nontext only for visible marks that are not writing. Put regions in natural diplomatic reading order. Attach every commentary, marginal note, alternate title, translator attribution, and catalogue group label to its owning region. Do not transcribe the page in this phase. The page image is the sole authority."""

_LAYOUT_REVIEW_TASK = """Review the validated semantic map against evidence/region-overlay.jpg and the full page. Correct any missed title, folio marker, marginal note, alternate title, catalogue label, translator attribution, wrong attachment, or wrong reading order. Preserve an exact one-time partition of every detector cell and stay at or below 20 regions. Call submit_region_map exactly once with the final map. Do not transcribe yet."""

_ASSEMBLY_TASK = """Now transcribe the page from its validated semantic map.

Read evidence/region-map.json, evidence/region-readings.json, evidence/region-overlay.jpg, the full page, and the contextual crops under evidence/regions/. Gemini's region readings are independent evidence, never authority. Preserve exact written forms; do not modernize or normalize Han variants. Return one text for every mapped region ID, including an empty string for seal_or_nontext regions with no writing. Keep each title, folio marker, catalogue label, and translator attribution in its mapped region so deterministic rendering cannot drop or detach it.

Call verify_regions with the complete region list. Re-read every reported mismatch. Use inspect_glyph only for a genuinely disputed detector cell when you already have 2-4 literal alternatives grounded in the page. Decide from visible strokes first. Then call submit_transcription exactly once with the complete final region list. The tool call is the only accepted output; do not place transcription in final prose."""

_REGION_READER_SCHEMA = {
    "type": "object",
    "properties": {
        "spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "span_index": {"type": "integer"},
                    "text": {"type": "string"},
                    "uncertain_positions": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["span_index", "text", "uncertain_positions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["spans"],
    "additionalProperties": False,
}


def _toolbelt8_policy_extension() -> bytes:
    source = _TOOLBELT7_POLICY_EXTENSION_BYTES.decode("utf-8")
    source = source.replace('"submit_transcription"', '"submit_layered_transcription"')
    old = (
        'if (event.toolName === "verify_layers" || '
        'event.toolName === "inspect_glyph") {'
    )
    new = (
        'if (event.toolName === "verify_layers" || '
        'event.toolName === "inspect_glyph" || '
        'event.toolName === "submit_region_map" || '
        'event.toolName === "verify_regions" || '
        'event.toolName === "submit_transcription") {'
    )
    if source.count(old) != 1:
        raise RuntimeError("toolbelt7 policy allowlist changed")
    return source.replace(old, new).encode("utf-8")


_TOOLBELT8_POLICY_EXTENSION_BYTES = _toolbelt8_policy_extension()

_REGION_EXTENSION = (
    r"""import { access, appendFile, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const REGION_READINGS = "evidence/%REGION_READINGS%";
const REGION_MAP = "evidence/%REGION_MAP%";
const MAX_REGIONS = %MAX_REGIONS%;
const MAX_REGION_TEXT_BYTES = %MAX_REGION_TEXT_BYTES%;

function nonSpace(value: string): string {
  return value.normalize("NFC").replace(/\s/gu, "");
}

function repetitionRate(value: string): number {
  const lines = value.normalize("NFC").replace(/\r\n?/gu, "\n").split("\n")
    .map((line) => line.trim()).filter((line) => line.length > 0);
  if (lines.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const line of lines) counts.set(line, (counts.get(line) ?? 0) + 1);
  let repeated = 0;
  for (const count of counts.values()) if (count > 2) repeated += count;
  return repeated / lines.length;
}

async function assemblyPhase(cwd: string): Promise<boolean> {
  try {
    await access(join(cwd, REGION_READINGS));
    return true;
  } catch {
    return false;
  }
}

export default function regionalTranscriptionExtension(pi: ExtensionAPI) {
  const z = pi.zod;
  const span = z.object({
    column: z.number().int().min(0),
    start: z.number().int().min(0),
    end: z.number().int().min(1),
  }).strict();
  const region = z.object({
    id: z.string().regex(/^r\d{3}$/u),
    role: z.enum([
      "title", "running_header", "folio_marker", "main_body",
      "interlinear_commentary", "marginalia", "alternate_title",
      "translator_attribution", "catalogue_group_label", "colophon",
      "seal_or_nontext",
    ]),
    layer: z.enum(["primary", "commentary", "nontext"]),
    reading_order: z.number().int().min(0),
    parent_id: z.string().regex(/^r\d{3}$/u).nullable(),
    attaches_to: z.string().regex(/^r\d{3}$/u).nullable(),
    spans: z.array(span).min(1).max(64),
  }).strict();
  const regionText = z.object({
    region_id: z.string().regex(/^r\d{3}$/u),
    text: z.string().max(MAX_REGION_TEXT_BYTES),
  }).strict();

  pi.on("tool_call", async (event, ctx) => {
    const assembly = await assemblyPhase(ctx.cwd);
    const layoutAllowed = event.toolName === "read" || event.toolName === "submit_region_map";
    const assemblyAllowed = ["read", "verify_regions", "inspect_glyph", "submit_transcription"]
      .includes(event.toolName);
    if ((!assembly && !layoutAllowed) || (assembly && !assemblyAllowed)) {
      return { block: true, reason: assembly
        ? "assembly phase allows read, verify_regions, inspect_glyph, and submit_transcription"
        : "layout phase allows only read and submit_region_map" };
    }
  });

  pi.registerTool({
    name: "submit_region_map",
    label: "Submit Region Map",
    description: "Submit a complete, ordered semantic partition of every detector cell.",
    loadMode: "essential",
    approval: "write",
    strict: true,
    parameters: z.object({ regions: z.array(region).min(1).max(MAX_REGIONS) }).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const payload = JSON.stringify({ schema_version: 1, regions: params.regions }) + "\n";
      await writeFile(join(ctx.cwd, "out", "%REGION_MAP%"), payload, "utf8");
      await appendFile(join(ctx.cwd, "out", ".region-map-submissions.jsonl"), payload, "utf8");
      return { content: [{ type: "text", text: "Region map recorded for deterministic validation." }], details: { submitted: true } };
    },
  });

  async function verifyRegionTexts(cwd: string, rows: Array<{ region_id: string; text: string }>) {
    const map = JSON.parse(await readFile(join(cwd, REGION_MAP), "utf8")) as {
      regions: Array<{ id: string; role: string; layer: string; reading_order: number; glyph_count: number }>;
    };
    const readings = JSON.parse(await readFile(join(cwd, REGION_READINGS), "utf8")) as {
      regions: Array<{ region_id: string; reading: string | null }>;
    };
    const expected = new Set(map.regions.map((item) => item.id));
    const counts = new Map<string, number>();
    for (const row of rows) counts.set(row.region_id, (counts.get(row.region_id) ?? 0) + 1);
    const missing = [...expected].filter((id) => !counts.has(id));
    const unknown = [...counts.keys()].filter((id) => !expected.has(id));
    const duplicates = [...counts.entries()].filter(([, count]) => count !== 1).map(([id]) => id);
    const byId = new Map(rows.map((row) => [row.region_id, row.text]));
    const emptyWriting = map.regions.filter((item) => item.layer !== "nontext" && item.glyph_count > 0 && !nonSpace(byId.get(item.id) ?? "")).map((item) => item.id);
    const ordered = [...map.regions].sort((a, b) => a.reading_order - b.reading_order);
    const primary = ordered.filter((item) => item.layer === "primary").map((item) => byId.get(item.id) ?? "").join("\n");
    const commentary = ordered.filter((item) => item.layer === "commentary").map((item) => byId.get(item.id) ?? "").join("\n");
    const readerMissing = readings.regions.filter((item) => item.reading && !nonSpace(primary + commentary).includes(nonSpace(item.reading))).map((item) => item.region_id);
    return {
      valid: missing.length === 0 && unknown.length === 0 && duplicates.length === 0 && emptyWriting.length === 0,
      missing_region_ids: missing,
      unknown_region_ids: unknown,
      duplicate_region_ids: duplicates,
      empty_writing_region_ids: emptyWriting,
      gemini_region_readings_absent_from_draft: readerMissing,
      primary_repetition_rate: repetitionRate(primary),
      commentary_repetition_rate: repetitionRate(commentary),
      note: "Gemini mismatches require crop review, not automatic replacement. Repetition must remain below the suite 0.1 hard limit.",
    };
  }

  pi.registerTool({
    name: "verify_regions",
    label: "Verify Regions",
    description: "Verify complete region coverage, Gemini disagreements, and repetition before submission.",
    loadMode: "essential",
    approval: "none",
    strict: true,
    parameters: z.object({ regions: z.array(regionText).min(1).max(MAX_REGIONS) }).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const verdict = await verifyRegionTexts(ctx.cwd, params.regions);
      return { content: [{ type: "text", text: JSON.stringify(verdict) }], details: { checked: true, valid: verdict.valid } };
    },
  });

  let submitted = false;
  pi.registerTool({
    name: "submit_transcription",
    label: "Submit Regional Transcription",
    description: "Submit exactly one transcription text for every validated semantic region.",
    loadMode: "essential",
    approval: "write",
    strict: true,
    parameters: z.object({ regions: z.array(regionText).min(1).max(MAX_REGIONS) }).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      if (submitted) throw new Error("duplicate regional transcription submission");
      const verdict = await verifyRegionTexts(ctx.cwd, params.regions);
      if (!verdict.valid) throw new Error("regional transcription is incomplete: " + JSON.stringify(verdict));
      const payload = JSON.stringify({ schema_version: 1, regions: params.regions }) + "\n";
      if (Buffer.byteLength(payload, "utf8") > MAX_REGION_TEXT_BYTES) throw new Error("regional transcription exceeds byte limit");
      await writeFile(join(ctx.cwd, "out", "%REGIONAL_SUBMISSION%"), payload, { flag: "wx" });
      await appendFile(join(ctx.cwd, "out", ".regional-transcription-submissions.jsonl"), payload, "utf8");
      submitted = true;
      return { content: [{ type: "text", text: "Regional transcription accepted." }], details: { submitted: true } };
    },
  });

  pi.on("session_start", async (_event, ctx) => {
    const assembly = await assemblyPhase(ctx.cwd);
    await pi.setActiveTools(assembly
      ? ["read", "verify_regions", "inspect_glyph", "submit_transcription"]
      : ["read", "submit_region_map"]);
  });
}
""".replace("%REGION_READINGS%", _REGION_READINGS_NAME)
    .replace("%REGION_MAP%", _REGION_MAP_NAME)
    .replace("%REGIONAL_SUBMISSION%", _REGIONAL_SUBMISSION_NAME)
    .replace("%MAX_REGIONS%", str(_MAX_REGIONS))
    .replace("%MAX_REGION_TEXT_BYTES%", str(_MAX_REGION_TEXT_BYTES))
)
_REGION_EXTENSION_BYTES = _REGION_EXTENSION.encode("utf-8")


@dataclass(slots=True)
class _Usage:
    tokens_in: int | None = 0
    tokens_out: int | None = 0
    cost_usd: float | None = 0.0

    def add_response(self, response: Any) -> None:
        self.tokens_in = combine_count(self.tokens_in, response.prompt_tokens)
        self.tokens_out = combine_count(
            self.tokens_out, response.billable_output_tokens
        )
        self.cost_usd = combine_cost(self.cost_usd, response.cost_usd)

    def add_error(self, error: GatewayError) -> None:
        self.tokens_in = combine_count(self.tokens_in, error.tokens_in)
        self.tokens_out = combine_count(self.tokens_out, error.tokens_out)
        self.cost_usd = combine_cost(self.cost_usd, error.cost_usd)


def _stage_detector_geometry(
    workspace: Path, staged_image: Path, checkpoint: Path
) -> tuple[dict[str, object], list[dict[str, object]]]:
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

    split = _two_split([float(item.width) for item in detections])
    threshold = None
    two_layer = False
    if split is not None:
        threshold, small_center, large_center = split
        small_fraction = sum(
            1 for item in detections if float(item.width) < threshold
        ) / max(len(detections), 1)
        two_layer = (
            small_center / large_center <= _MAX_WIDTH_RATIO
            and _MIN_LAYER_FRACTION <= small_fraction <= 1 - _MIN_LAYER_FRACTION
        )

    def layer_of(detection: Any) -> str:
        if two_layer and threshold is not None and float(detection.width) < threshold:
            return "commentary"
        return "primary"

    evidence_root = workspace / "evidence"
    columns_root = evidence_root / "columns"
    columns_root.mkdir(parents=True, exist_ok=True)
    overlay = image.copy()
    column_records: list[dict[str, object]] = []
    adjudication_columns: list[dict[str, object]] = []
    global_index = 0
    registers = _split_registers(detections)
    for register_index, register_detections in enumerate(registers):
        for column_index, column in enumerate(_detected_columns(register_detections)):
            ordered = sorted(column, key=lambda item: item.cell.y0 + item.cell.y1)
            left = min(item.cell.x0 for item in ordered)
            top = min(item.cell.y0 for item in ordered)
            right = max(item.cell.x1 for item in ordered)
            bottom = max(item.cell.y1 for item in ordered)
            pad = max(8, round((right - left) * _CROP_PAD_FRACTION))
            crop = image[
                max(0, top - pad) : min(height, bottom + pad),
                max(0, left - pad) : min(width, right + pad),
            ]
            ok, crop_payload = cv2.imencode(".png", crop)
            if not ok:
                raise RuntimeError(
                    f"failed to encode detector column r{register_index}c{column_index:02d}"
                )
            crop_name = f"r{register_index}c{column_index:02d}.png"
            (columns_root / crop_name).write_bytes(crop_payload.tobytes())
            cells = [
                [item.cell.x0, item.cell.y0, item.cell.x1, item.cell.y1]
                for item in ordered
            ]
            primary_boxes = sum(1 for item in ordered if layer_of(item) == "primary")
            layer = "primary" if primary_boxes * 2 >= len(ordered) else "commentary"
            for position, item in enumerate(ordered):
                color = (0, 200, 0) if layer_of(item) == "primary" else (0, 140, 255)
                cv2.rectangle(
                    overlay,
                    (item.cell.x0, item.cell.y0),
                    (item.cell.x1, item.cell.y1),
                    color,
                    2,
                )
                if position == 0:
                    cv2.putText(
                        overlay,
                        f"c{global_index}",
                        (item.cell.x0, max(18, item.cell.y0 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 0, 0),
                        2,
                        cv2.LINE_AA,
                    )
            column_records.append(
                {
                    "index": global_index,
                    "register": register_index,
                    "bbox": [left, top, right - left, bottom - top],
                    "boxes": len(ordered),
                    "primary_boxes": primary_boxes,
                    "commentary_boxes": len(ordered) - primary_boxes,
                    "layer": layer,
                    "crop": f"columns/{crop_name}",
                    "cells": cells,
                    "second_reader": None,
                }
            )
            adjudication_columns.append(
                {"layer": layer, "second_reader": None, "boxes": cells}
            )
            global_index += 1

    scale = _OVERLAY_MAX_SIDE / max(overlay.shape[:2])
    if scale < 1.0:
        overlay = cv2.resize(
            overlay,
            (round(overlay.shape[1] * scale), round(overlay.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, overlay_payload = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("failed to encode detector overlay")
    (evidence_root / "overlay.jpg").write_bytes(overlay_payload.tobytes())

    primary_total = sum(int(record["primary_boxes"]) for record in column_records)
    commentary_total = sum(int(record["commentary_boxes"]) for record in column_records)
    geometry = {
        "schema_version": 4,
        "image_size": [width, height],
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
        "registers": len(registers),
        "columns_right_to_left": True,
        "second_reader": None,
        "columns": column_records,
    }
    geometry_bytes = (
        json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (evidence_root / "geometry.json").write_bytes(geometry_bytes)
    return (
        {
            "detected_boxes": len(detections),
            "primary_boxes": primary_total,
            "commentary_boxes": commentary_total,
            "two_layer": two_layer,
            "registers": len(registers),
            "columns": len(column_records),
            "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
            "inference_seconds": inference.get("inference_seconds"),
        },
        adjudication_columns,
    )


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise agent_cell.AgentCellError(f"missing {label}: {path}") from error
    except json.JSONDecodeError as error:
        raise agent_cell.AgentCellError(f"malformed {label}: {path}") from error
    if not isinstance(value, dict):
        raise agent_cell.AgentCellError(f"{label} must be a JSON object")
    return value


def _validate_region_map(
    raw: dict[str, object], columns: list[dict[str, object]]
) -> dict[str, object]:
    if set(raw) != {"schema_version", "regions"} or raw.get("schema_version") != 1:
        raise agent_cell.AgentCellError("region map has an invalid envelope")
    regions = raw.get("regions")
    if not isinstance(regions, list) or not 1 <= len(regions) <= _MAX_REGIONS:
        raise agent_cell.AgentCellError(
            f"region map must contain 1-{_MAX_REGIONS} regions"
        )
    expected_cells = {
        (column_index, position)
        for column_index, column in enumerate(columns)
        for position in range(len(column["boxes"]))
    }
    claimed: dict[tuple[int, int], str] = {}
    normalized: list[dict[str, object]] = []
    ids: set[str] = set()
    orders: set[int] = set()
    for value in regions:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "role",
            "layer",
            "reading_order",
            "parent_id",
            "attaches_to",
            "spans",
        }:
            raise agent_cell.AgentCellError("region map contains a malformed region")
        region_id = value["id"]
        role = value["role"]
        layer = value["layer"]
        order = value["reading_order"]
        if not isinstance(region_id, str) or not _REGION_ID.fullmatch(region_id):
            raise agent_cell.AgentCellError("region IDs must match rNNN")
        if region_id in ids:
            raise agent_cell.AgentCellError(f"duplicate region ID: {region_id}")
        if role not in _ROLES or layer not in _LAYERS:
            raise agent_cell.AgentCellError(f"invalid role or layer for {region_id}")
        if role == "seal_or_nontext" and layer != "nontext":
            raise agent_cell.AgentCellError("seal_or_nontext regions must use nontext")
        if role != "seal_or_nontext" and layer == "nontext":
            raise agent_cell.AgentCellError("only seal_or_nontext may use nontext")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise agent_cell.AgentCellError(f"invalid reading order for {region_id}")
        if order in orders:
            raise agent_cell.AgentCellError(f"duplicate reading order: {order}")
        parent = value["parent_id"]
        attaches = value["attaches_to"]
        if parent is not None and not isinstance(parent, str):
            raise agent_cell.AgentCellError(f"invalid parent for {region_id}")
        if attaches is not None and not isinstance(attaches, str):
            raise agent_cell.AgentCellError(f"invalid attachment for {region_id}")
        if role in _ATTACHMENT_ROLES and attaches is None:
            raise agent_cell.AgentCellError(f"{role} region {region_id} must attach")
        spans = value["spans"]
        if not isinstance(spans, list) or not 1 <= len(spans) <= _MAX_REGION_SPANS:
            raise agent_cell.AgentCellError(f"invalid spans for {region_id}")
        region_cells: list[list[int]] = []
        normalized_spans: list[dict[str, int]] = []
        for span in spans:
            if not isinstance(span, dict) or set(span) != {"column", "start", "end"}:
                raise agent_cell.AgentCellError(f"malformed span in {region_id}")
            column_index, start, end = span["column"], span["start"], span["end"]
            if any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in (column_index, start, end)
            ):
                raise agent_cell.AgentCellError(f"non-integer span in {region_id}")
            if not 0 <= column_index < len(columns):
                raise agent_cell.AgentCellError(f"unknown column in {region_id}")
            boxes = columns[column_index]["boxes"]
            if not isinstance(boxes, list) or not 0 <= start < end <= len(boxes):
                raise agent_cell.AgentCellError(f"out-of-range span in {region_id}")
            for position in range(start, end):
                key = (column_index, position)
                if key in claimed:
                    raise agent_cell.AgentCellError(
                        f"cell c{column_index}p{position} claimed by {claimed[key]} and {region_id}"
                    )
                claimed[key] = region_id
                region_cells.append(boxes[position])
            normalized_spans.append(
                {"column": column_index, "start": start, "end": end}
            )
        left = min(box[0] for box in region_cells)
        top = min(box[1] for box in region_cells)
        right = max(box[2] for box in region_cells)
        bottom = max(box[3] for box in region_cells)
        normalized.append(
            {
                **value,
                "spans": normalized_spans,
                "bbox": [left, top, right - left, bottom - top],
                "glyph_count": len(region_cells),
            }
        )
        ids.add(region_id)
        orders.add(order)
    if orders != set(range(len(normalized))):
        raise agent_cell.AgentCellError("reading orders must be contiguous from zero")
    missing = expected_cells - set(claimed)
    if missing:
        preview = ", ".join(f"c{c}p{p}" for c, p in sorted(missing)[:8])
        raise agent_cell.AgentCellError(f"region map leaves cells unclaimed: {preview}")

    by_id = {str(region["id"]): region for region in normalized}
    edges: dict[str, list[str]] = {region_id: [] for region_id in by_id}
    for region_id, region in by_id.items():
        for field in ("parent_id", "attaches_to"):
            target = region[field]
            if target is None:
                continue
            if target not in by_id:
                raise agent_cell.AgentCellError(
                    f"region {region_id} references unknown {field} {target}"
                )
            if target == region_id:
                raise agent_cell.AgentCellError(f"region {region_id} references itself")
            edges[region_id].append(str(target))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(region_id: str) -> None:
        if region_id in visiting:
            raise agent_cell.AgentCellError("region attachment graph contains a cycle")
        if region_id in visited:
            return
        visiting.add(region_id)
        for target in edges[region_id]:
            visit(target)
        visiting.remove(region_id)
        visited.add(region_id)

    for region_id in edges:
        visit(region_id)
    return {
        "schema_version": 1,
        "regions": sorted(normalized, key=lambda item: int(item["reading_order"])),
    }


def _region_overlay(
    workspace: Path, image: np.ndarray, region_map: dict[str, object]
) -> bytes:
    overlay = image.copy()
    colors = {
        "primary": (0, 190, 0),
        "commentary": (0, 140, 255),
        "nontext": (160, 160, 160),
    }
    regions = region_map["regions"]
    assert isinstance(regions, list)
    for region in regions:
        assert isinstance(region, dict)
        x, y, width, height = region["bbox"]
        color = colors[str(region["layer"])]
        cv2.rectangle(overlay, (x, y), (x + width, y + height), color, 4)
        cv2.putText(
            overlay,
            f"{region['reading_order']}:{region['id']}:{region['role']}",
            (x, max(24, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            color,
            2,
            cv2.LINE_AA,
        )
    scale = min(1.0, 1800 / max(overlay.shape[:2]))
    if scale < 1.0:
        overlay = cv2.resize(
            overlay,
            (round(overlay.shape[1] * scale), round(overlay.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, payload = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("failed to encode region overlay")
    encoded = payload.tobytes()
    (workspace / "evidence" / "region-overlay.jpg").write_bytes(encoded)
    return encoded


def _write_region_map(workspace: Path, region_map: dict[str, object]) -> bytes:
    encoded = (
        json.dumps(region_map, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (workspace / "evidence" / _REGION_MAP_NAME).write_bytes(encoded)
    return encoded


def _stage_region_crops(
    workspace: Path, image: np.ndarray, region_map: dict[str, object]
) -> None:
    height, width = image.shape[:2]
    root = workspace / "evidence" / "regions"
    root.mkdir(parents=True, exist_ok=True)
    regions = region_map["regions"]
    assert isinstance(regions, list)
    for region in regions:
        assert isinstance(region, dict)
        x, y, box_width, box_height = region["bbox"]
        pad = max(16, round(max(box_width, box_height) * 0.1))
        crop = image[
            max(0, y - pad) : min(height, y + box_height + pad),
            max(0, x - pad) : min(width, x + box_width + pad),
        ]
        ok, payload = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError(f"failed to encode region crop {region['id']}")
        name = f"{region['id']}.png"
        (root / name).write_bytes(payload.tobytes())
        region["crop"] = f"regions/{name}"


def _region_reader_prompt(region: dict[str, object]) -> str:
    spans = region["spans"]
    assert isinstance(spans, list)
    span_lines = [
        f"{index}: column {span['column']}, cells {span['start']}..{span['end'] - 1}, expected {span['end'] - span['start']} glyphs"
        for index, span in enumerate(spans)
        if isinstance(span, dict)
    ]
    return f"""The first image is one contextual crop from a premodern Chinese page. The second image is the full-page region overlay.

Region ID: {region["id"]}
Role: {region["role"]}
Layer: {region["layer"]}
Parent: {region["parent_id"]}
Attached to: {region["attaches_to"]}
Spans in requested reading order:
{chr(10).join(span_lines)}

Transcribe each requested span independently and exactly from the visible strokes. Preserve historic and uncommon written forms. Do not modernize, normalize, infer a common title or translator, or repair from historical knowledge. Return one item for every span_index in exact numeric order. Text may differ from the expected detector count when the detector missed or split a glyph; record crop-relative uncertain character positions instead of inventing a character."""


def _read_regions(
    workspace: Path,
    region_map: dict[str, object],
    overlay_bytes: bytes,
    usage: _Usage,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    regions = region_map["regions"]
    assert isinstance(regions, list)
    for region in regions:
        assert isinstance(region, dict)
        crop_path = workspace / "evidence" / str(region["crop"])
        crop_bytes = crop_path.read_bytes()
        expected = int(region["glyph_count"])
        try:
            value, response = generate_json(
                ModelRequest(
                    model=_REGION_READER_MODEL,
                    prompt=_region_reader_prompt(region),
                    system=(
                        "You are an expert paleographer. Read exact visible glyph forms "
                        "from the crop. Layout metadata is context, not authority."
                    ),
                    images=(
                        ImageContent(crop_bytes, mime="image/png"),
                        ImageContent(overlay_bytes, mime="image/jpeg"),
                    ),
                    temperature=0.1,
                    max_output_tokens=min(4096, max(1024, expected * 8 + 256)),
                    media_resolution=_REGION_READER_MEDIA_RESOLUTION,
                    json_output=True,
                    json_schema=_REGION_READER_SCHEMA,
                    thinking_level=_REGION_READER_THINKING,
                )
            )
            usage.add_response(response)
        except GatewayError as error:
            usage.add_error(error)
            results.append(
                {
                    "region_id": region["id"],
                    "reading": None,
                    "spans": [],
                    "error": str(error),
                }
            )
            continue
        spans = value.get("spans") if isinstance(value, dict) else None
        expected_spans = region["spans"]
        if not isinstance(spans, list) or not isinstance(expected_spans, list):
            results.append(
                {
                    "region_id": region["id"],
                    "reading": None,
                    "spans": [],
                    "error": "invalid region-reader payload",
                }
            )
            continue
        valid = len(spans) == len(expected_spans)
        normalized_spans: list[dict[str, object]] = []
        for index, span in enumerate(spans):
            if (
                not isinstance(span, dict)
                or span.get("span_index") != index
                or not isinstance(span.get("text"), str)
                or not isinstance(span.get("uncertain_positions"), list)
                or any(
                    isinstance(position, bool)
                    or not isinstance(position, int)
                    or position < 0
                    for position in span.get("uncertain_positions", [])
                )
            ):
                valid = False
                break
            normalized_spans.append(
                {
                    "span_index": index,
                    "text": span["text"],
                    "uncertain_positions": span["uncertain_positions"],
                }
            )
        results.append(
            {
                "region_id": region["id"],
                "reading": "\n".join(str(span["text"]) for span in normalized_spans)
                if valid
                else None,
                "spans": normalized_spans if valid else [],
                "error": None if valid else "invalid region-reader span sequence",
            }
        )
    return {
        "schema_version": 1,
        "model": _REGION_READER_MODEL,
        "thinking_level": _REGION_READER_THINKING,
        "media_resolution": _REGION_READER_MEDIA_RESOLUTION,
        "regions": results,
    }


def _apply_region_readings_to_columns(
    region_map: dict[str, object],
    readings: dict[str, object],
    columns: list[dict[str, object]],
) -> None:
    text_by_region = {
        str(row["region_id"]): row
        for row in readings["regions"]
        if isinstance(row, dict)
    }
    characters: dict[tuple[int, int], str] = {}
    regions = region_map["regions"]
    assert isinstance(regions, list)
    for region in regions:
        assert isinstance(region, dict)
        reading = text_by_region.get(str(region["id"]))
        if not reading:
            continue
        span_readings = reading.get("spans")
        spans = region["spans"]
        if not isinstance(span_readings, list) or not isinstance(spans, list):
            continue
        for span, span_reading in zip(spans, span_readings, strict=False):
            if not isinstance(span, dict) or not isinstance(span_reading, dict):
                continue
            text = "".join(
                character
                for character in str(span_reading.get("text", ""))
                if not character.isspace()
            )
            start, end, column = span["start"], span["end"], span["column"]
            if len(text) != end - start:
                continue
            for offset, character in enumerate(text):
                characters[(column, start + offset)] = character
    for column_index, column in enumerate(columns):
        boxes = column["boxes"]
        assert isinstance(boxes, list)
        if all(
            (column_index, position) in characters for position in range(len(boxes))
        ):
            column["second_reader"] = "".join(
                characters[(column_index, position)] for position in range(len(boxes))
            )


def _read_regional_submission(
    workspace: Path, region_map: dict[str, object]
) -> tuple[str, str, list[dict[str, str]]]:
    payload = _read_json(
        workspace / "out" / _REGIONAL_SUBMISSION_NAME,
        "regional transcription submission",
    )
    if (
        set(payload) != {"schema_version", "regions"}
        or payload.get("schema_version") != 1
    ):
        raise agent_cell.AgentCellError("regional transcription has invalid envelope")
    rows = payload.get("regions")
    if not isinstance(rows, list):
        raise agent_cell.AgentCellError("regional transcription rows must be a list")
    expected = region_map["regions"]
    assert isinstance(expected, list)
    by_id: dict[str, str] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"region_id", "text"}
            or not isinstance(row["region_id"], str)
            or not isinstance(row["text"], str)
            or row["region_id"] in by_id
        ):
            raise agent_cell.AgentCellError(
                "regional transcription contains malformed rows"
            )
        by_id[row["region_id"]] = row["text"]
    expected_ids = {
        str(region["id"]) for region in expected if isinstance(region, dict)
    }
    if set(by_id) != expected_ids:
        raise agent_cell.AgentCellError(
            "regional transcription does not cover the exact map"
        )
    primary: list[str] = []
    commentary: list[str] = []
    ordered_rows: list[dict[str, str]] = []
    for region in expected:
        assert isinstance(region, dict)
        region_id = str(region["id"])
        text = by_id[region_id]
        if (
            region["layer"] != "nontext"
            and int(region["glyph_count"]) > 0
            and not text.strip()
        ):
            raise agent_cell.AgentCellError(f"writing region {region_id} is empty")
        ordered_rows.append({"region_id": region_id, "text": text})
        if text.strip() and region["layer"] == "primary":
            primary.append(text)
        elif text.strip() and region["layer"] == "commentary":
            commentary.append(text)
    rendered_primary = "\n".join(primary)
    rendered_commentary = "\n".join(commentary)
    if not rendered_primary.strip():
        raise agent_cell.AgentCellError(
            "regional transcription rendered empty primary text"
        )
    if (
        len(rendered_primary.encode("utf-8")) + len(rendered_commentary.encode("utf-8"))
        > _MAX_REGION_TEXT_BYTES
    ):
        raise agent_cell.AgentCellError(
            "rendered regional transcription exceeds byte limit"
        )
    return rendered_primary, rendered_commentary, ordered_rows


def _sum_process_stats(*runs: agent_cell.AgentRun) -> dict[str, int]:
    keys = {key for run in runs for key in (run.process_stats or {})}
    return {
        key: sum((run.process_stats or {}).get(key, 0) for run in runs) for key in keys
    }


class OmpToolbelt8RegionsTranscribe(Transcribe):
    """Luna layout graph, contextual Gemini reads, and regional assembly."""

    variant = "omp_toolbelt8_regions"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/usage.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/transcribe_omp_toolbelt2.py",
        "factory/stations/transcribe_omp_toolbelt3.py",
        "factory/stations/transcribe_omp_toolbelt5.py",
        "factory/stations/transcribe_omp_toolbelt7.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
        "factory/gateway/client.py",
        "factory/gateway/gemini.py",
        "factory/gateway/protocol.py",
    )

    def validate_options(self, options: Any) -> None:
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
            raise RuntimeError("toolbelt8 cell expects exactly one staged page image")
        geometry_summary, columns = _stage_detector_geometry(
            workspace, staged_images[0], _checkpoint_path(job)
        )
        image = cv2.imdecode(
            np.frombuffer(staged_images[0].read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise RuntimeError("cannot decode staged page image")

        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / "00-toolbelt8-policy.ts").write_bytes(
            _TOOLBELT8_POLICY_EXTENSION_BYTES
        )
        (extension_dir / "01-inspection.ts").write_bytes(
            self.inspection_extension_bytes()
        )
        (extension_dir / "02-regions.ts").write_bytes(_REGION_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        layout_run = agent_cell.run(
            workspace,
            _LAYOUT_TASK,
            model=job.config.model,
            timeout_s=_LAYOUT_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        initial = _read_json(workspace / "out" / _REGION_MAP_NAME, "initial region map")
        initial_map = _validate_region_map(initial, columns)
        _write_region_map(workspace, initial_map)
        _region_overlay(workspace, image, initial_map)

        review_run = agent_cell.resume(
            workspace,
            layout_run.session_id,
            _LAYOUT_REVIEW_TASK,
            timeout_s=_LAYOUT_REVIEW_TIMEOUT_SECONDS,
            executor="omp",
        )
        reviewed = _read_json(
            workspace / "out" / _REGION_MAP_NAME, "reviewed region map"
        )
        region_map = _validate_region_map(reviewed, columns)
        _stage_region_crops(workspace, image, region_map)
        region_map_bytes = _write_region_map(workspace, region_map)
        overlay_bytes = _region_overlay(workspace, image, region_map)

        gateway_usage = _Usage()
        region_readings = _read_regions(
            workspace, region_map, overlay_bytes, gateway_usage
        )
        _apply_region_readings_to_columns(region_map, region_readings, columns)
        readings_bytes = (
            json.dumps(region_readings, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        (workspace / "evidence" / _REGION_READINGS_NAME).write_bytes(readings_bytes)
        inspection = _stage_inspection(
            workspace, image, columns, self.classifier_artifacts(job)
        )

        assembly_run = agent_cell.resume(
            workspace,
            layout_run.session_id,
            _ASSEMBLY_TASK,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
        )
        inspection["usage"] = _inspection_usage(workspace)
        primary, commentary, regional_rows = _read_regional_submission(
            workspace, region_map
        )
        primary, adjudication = _adjudicate_glyphs(image, columns, primary, page_key)
        page = job.page or {}
        agent_tokens = layout_run.tokens + review_run.tokens + assembly_run.tokens
        agent_cost = combine_cost(
            combine_cost(layout_run.cost_usd, review_run.cost_usd),
            assembly_run.cost_usd,
        )
        total_cost = combine_cost(agent_cost, gateway_usage.cost_usd)
        total_cost = combine_cost(total_cost, float(adjudication["cost_usd"]))
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
                    "region_map_sha256": hashlib.sha256(region_map_bytes).hexdigest(),
                    "region_readings_sha256": hashlib.sha256(
                        readings_bytes
                    ).hexdigest(),
                    "regions": len(region_map["regions"]),
                    "region_reader": {
                        "model": _REGION_READER_MODEL,
                        "thinking_level": _REGION_READER_THINKING,
                        "failures": sum(
                            1
                            for row in region_readings["regions"]
                            if isinstance(row, dict) and row["error"] is not None
                        ),
                    },
                    "inspection": inspection,
                    "adjudication": adjudication,
                    "regional_submission": regional_rows,
                },
            },
            tokens_in=combine_count(agent_tokens, gateway_usage.tokens_in),
            tokens_out=gateway_usage.tokens_out,
            cost_usd=total_cost,
            process_stats=_sum_process_stats(layout_run, review_run, assembly_run),
        )

    def inspection_extension_bytes(self) -> bytes:
        return _INSPECTION_EXTENSION_BYTES

    def classifier_artifacts(self, job: Job) -> tuple[Path, Path] | None:
        return _artifact_paths(job)

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root)
            / "runs"
            / "transcribe_omp_toolbelt8_regions"
        )


register(OmpToolbelt8RegionsTranscribe())
