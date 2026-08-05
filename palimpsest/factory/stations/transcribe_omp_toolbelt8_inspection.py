"""Toolbelt8 regional transcription with open, bounded crop-local evidence."""

from __future__ import annotations

from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job
from palimpsest.factory.stations.transcribe_omp_toolbelt7 import (
    _INSPECTION_MANIFEST,
    _MAX_INSPECTION_CALLS,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt8 import (
    OmpToolbelt8RegionsTranscribe,
)

_OPEN_INSPECTION_EXTENSION = r"""import { createHash } from "node:crypto";
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
  classifier: null | { top_k: ClassifierChoice[]; margin: number };
}
const MAX_INSPECTION_CALLS = %MAX_INSPECTION_CALLS%;
const PRIVATE_MANIFEST = "%PRIVATE_MANIFEST%";

export default function openInspectionExtension(pi: ExtensionAPI) {
  const z = pi.zod;
  let inspectionCalls = 0;
  const inspected = new Set<string>();
  pi.registerTool({
    name: "inspect_glyph",
    label: "Inspect Glyph With Neighbors",
    description:
      "Inspect one detector cell with its immediate vertical neighbors. Returns " +
      "the independent second-reader glyph and classifier top five as explicitly " +
      "non-authoritative evidence. Requires 2-4 unique page-grounded alternatives.",
    loadMode: "essential",
    approval: "none",
    strict: true,
    parameters: z.object({
      column: z.number().int().min(0),
      position: z.number().int().min(0),
      candidates: z.array(z.string().min(1).max(2)).min(2).max(4),
    }).strict(),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const raw = await readFile(join(ctx.cwd, PRIVATE_MANIFEST), "utf8");
      const payload = JSON.parse(raw) as { glyphs: GlyphEvidence[] };
      const glyph = payload.glyphs.find(
        (item) => item.column === params.column && item.position === params.position,
      );
      if (glyph === undefined) {
        throw new Error(
          "no detector cell at column " + String(params.column) +
          ", position " + String(params.position) + "; call was not charged",
        );
      }
      const key = String(params.column) + ":" + String(params.position);
      if (inspected.has(key)) {
        throw new Error("detector cell already inspected; duplicate call was not charged");
      }
      if (new Set(params.candidates).size !== params.candidates.length) {
        throw new Error("candidate alternatives must be unique; call was not charged");
      }
      const topK = glyph.classifier?.top_k ?? [];
      const evidenceCharacters = new Set(topK.map((item) => item.character));
      if (glyph.second_reader_character !== null) {
        evidenceCharacters.add(glyph.second_reader_character);
      }
      if (!params.candidates.some((candidate) => evidenceCharacters.has(candidate))) {
        throw new Error(
          "none of the proposed alternatives matches the second reader or classifier " +
          "top five; recheck the coordinate and alternatives; call was not charged",
        );
      }
      if (inspectionCalls >= MAX_INSPECTION_CALLS) {
        throw new Error("inspect_glyph reached its per-page call limit");
      }

      const selected = await readFile(join(ctx.cwd, "evidence", glyph.crop));
      const neighbors = payload.glyphs
        .filter((item) => item.column === glyph.column && Math.abs(item.position - glyph.position) === 1)
        .sort((a, b) => a.position - b.position);
      const neighborBytes = await Promise.all(
        neighbors.map((item) => readFile(join(ctx.cwd, "evidence", item.crop))),
      );
      const choices = params.candidates.map((candidate) => {
        const classifierIndex = topK.findIndex((item) => item.character === candidate);
        const classifier = classifierIndex < 0 ? undefined : topK[classifierIndex];
        return {
          candidate,
          matches_second_reader: glyph.second_reader_character === candidate,
          classifier_probability: classifier?.probability ?? null,
          classifier_rank: classifierIndex < 0 ? null : classifierIndex + 1,
        };
      });
      const cropSha256 = createHash("sha256").update(selected).digest("hex");
      inspectionCalls += 1;
      inspected.add(key);
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
      const result = {
        column: glyph.column,
        position: glyph.position,
        layer: glyph.layer,
        bbox: glyph.bbox,
        second_reader_character: glyph.second_reader_character,
        classifier_top_k: topK,
        classifier_margin: glyph.classifier?.margin ?? null,
        candidates: choices,
        neighbors: neighbors.map((item) => ({
          position: item.position,
          bbox: item.bbox,
          crop: item.crop,
        })),
        evidence_policy:
          "Selected and neighboring crops are attached in position order. The " +
          "classifier and second reader are independent suggestions only. Luna is " +
          "the sole component allowed to retain or change transcription text after " +
          "checking the visible strokes.",
      };
      return {
        content: [
          { type: "text", text: JSON.stringify(result) },
          { type: "image", data: selected.toString("base64"), mimeType: "image/png" },
          ...neighborBytes.map((data) => ({
            type: "image" as const,
            data: data.toString("base64"),
            mimeType: "image/png" as const,
          })),
        ],
        details: {
          inspected: true,
          column: glyph.column,
          position: glyph.position,
          crop_sha256: cropSha256,
          neighbor_positions: neighbors.map((item) => item.position),
        },
      };
    },
  });
}
""".replace("%MAX_INSPECTION_CALLS%", str(_MAX_INSPECTION_CALLS)).replace(
    "%PRIVATE_MANIFEST%", _INSPECTION_MANIFEST
)
_OPEN_INSPECTION_EXTENSION_BYTES = _OPEN_INSPECTION_EXTENSION.encode("utf-8")


class OmpToolbelt8OpenInspectionTranscribe(OmpToolbelt8RegionsTranscribe):
    """Regional reader with explicit top-k and immediate-neighbor evidence."""

    variant = "omp_toolbelt8_regions_open_inspection"
    production_dependencies = (
        *OmpToolbelt8RegionsTranscribe.production_dependencies,
        "factory/stations/transcribe_omp_toolbelt8.py",
    )

    def inspection_extension_bytes(self) -> bytes:
        return _OPEN_INSPECTION_EXTENSION_BYTES

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root)
            / "runs"
            / "transcribe_omp_toolbelt8_regions_open_inspection"
        )


register(OmpToolbelt8OpenInspectionTranscribe())
