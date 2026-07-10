"""emend: the final editorial pass — a smoothed READING beside the ink.

Consumes the reconstructed manuscript plus the seam variant pairs (the same
ink transcribed twice at overlapping captures) and produces an emended
reading per section with a full apparatus: every change records original,
emended, and reason. The diplomatic transcription and the manuscript's
verbatim sections are never edited — this layer sits beside them, exactly
like a critical edition's text sits above its apparatus.
"""

from __future__ import annotations

import json
from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway.client import ModelRequest, generate_json
from palimpsest.factory.workspace.io import read_json

EMEND_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "reading": {"type": "string"},
                },
                "required": ["heading", "reading"],
            },
        },
        "apparatus": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "original": {"type": "string"},
                    "emended": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["section", "original", "emended", "reason"],
            },
        },
    },
    "required": ["sections", "apparatus"],
}


class Emend(Station):
    name = "emend"
    version = "emend/v1"
    grain = "manuscript"
    consumes = ("manuscript", "page_assembled")
    produces = "emendations"
    uses_model = True

    def run(self, job: Job) -> StationResult:
        manuscript = read_json(job.path_of("manuscript"))
        variants = self._seam_variants(job)

        blocks = []
        for section in manuscript["sections"]:
            blocks.append(f"=== SECTION: {section['heading']} ===\n"
                          f"{section['original']}")
        if variants:
            blocks.append(
                "=== VARIANTS (same ink, two independent reads) ===\n"
                + "\n".join(
                    f"[{v['page_id']}] kept: {v['kept']}\n"
                    f"[{v['page_id']}] variant: {v['variant']}"
                    for v in variants))

        plan, response = generate_json(ModelRequest(
            model=job.config.model,
            prompt=job.config.prompt.text + "\n\n" + "\n\n".join(blocks),
            temperature=job.config.params.get("temperature", 0.1),
            max_output_tokens=job.config.params.get("max_output_tokens", 32768),
            json_output=True,
            json_schema=EMEND_SCHEMA,
        ))
        if len(plan["sections"]) != len(manuscript["sections"]):
            raise ValueError(
                f"Emendation returned {len(plan['sections'])} sections for a "
                f"manuscript with {len(manuscript['sections'])} — refusing")

        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "sections": [
                    {"heading": ours["heading"], "reading": theirs["reading"]}
                    for ours, theirs in zip(manuscript["sections"], plan["sections"])
                ],
                "apparatus": plan["apparatus"],
            },
            tokens_in=response.prompt_tokens,
            tokens_out=response.output_tokens,
            cost_usd=response.cost_usd,
        )

    def _seam_variants(self, job: Job) -> list[dict]:
        """The two-vote pairs: at each trimmed seam, the kept head columns of
        the previous capture and the dropped duplicate of this capture."""
        variants = []
        for page in job.pages:
            assembled = read_json(
                job.path_of("page_assembled", page["page_id"]))
            seam = (assembled.get("original") or {}).get("seam")
            if not seam or not seam.get("dropped_text"):
                continue
            lines = seam.get("lines", 0)
            kept_tail = "\n".join(
                _content_lines(self._previous_original(job, page))[-lines:]
            ) if lines else ""
            variants.append({
                "page_id": page["page_id"],
                "kept": kept_tail,
                "variant": seam["dropped_text"],
            })
        return variants

    def _previous_original(self, job: Job, page: dict) -> str:
        pages = sorted(job.pages, key=lambda p: p.get("order", 0))
        ids = [p["page_id"] for p in pages]
        index = ids.index(page["page_id"])
        if index == 0:
            return ""
        previous = read_json(job.path_of("page_assembled", ids[index - 1]))
        return previous["original"]["text"]


def _content_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


register(Emend())
