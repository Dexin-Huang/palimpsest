"""survey: manuscript-level pass over all transcriptions that builds the
translation brief — the jig the page line's translate station clamps into
(lifted from survey.py)."""

from __future__ import annotations

import json
import math

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway.client import ModelRequest, generate, strip_json_fences
from palimpsest.factory.workspace.io import read_json

TOKENS_PER_CHAR = 0.35  # rough estimate for mixed Latin/abbreviations
MAX_TOKENS_PER_CHUNK = 20_000


class Survey(Station):
    name = "survey"
    version = "survey/v1"
    grain = "manuscript"
    consumes = ("page_transcription",)
    produces = "translation_brief"
    uses_model = True

    def run(self, job: Job) -> StationResult:
        records = [
            read_json(job.path_of("page_transcription", page["page_id"]))
            for page in job.pages
        ]
        partials: list[dict] = []
        tokens_in = tokens_out = 0
        cost = 0.0
        for chunk in _chunk(records, int(job.config.options.get(
                "max_tokens_per_chunk", MAX_TOKENS_PER_CHUNK))):
            chunk_text = "\n\n".join(
                f"[{r['page_id']}]\n{r['text']}" for r in chunk if r["text"].strip()
            )
            response = generate(ModelRequest(
                model=job.config.model,
                prompt=job.config.prompt.text + "\n\n" + chunk_text,
                temperature=job.config.params.get("temperature", 0.1),
                max_output_tokens=job.config.params.get("max_output_tokens", 32768),
                json_output=True,
            ))
            partials.append(json.loads(strip_json_fences(response.text)))
            tokens_in += response.prompt_tokens
            tokens_out += response.output_tokens
            cost += response.cost_usd or 0.0

        return StationResult(
            payload=_merge(partials, doc_id=job.doc_id, total_pages=len(records)),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )


def _chunk(records: list[dict], max_tokens: int) -> list[list[dict]]:
    total_tokens = int(sum(len(r["text"]) * TOKENS_PER_CHAR for r in records))
    num_chunks = max(1, math.ceil(total_tokens / max_tokens))
    chunk_size = math.ceil(len(records) / num_chunks)
    return [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]


def _dedupe(partials: list[dict], list_key: str, id_key: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for partial in partials:
        for entry in partial.get(list_key, []):
            key = str(entry.get(id_key, "")).lower().strip()
            if key and key not in seen:
                seen[key] = entry
    return list(seen.values())


def _merge(partials: list[dict], *, doc_id: str, total_pages: int) -> dict:
    style_notes: list[str] = []
    for partial in partials:
        for note in partial.get("style_notes", []):
            if note not in style_notes:
                style_notes.append(note)
    return {
        "version": 1,
        "document": {"doc_id": doc_id, "total_pages": total_pages},
        "glossary": _dedupe(partials, "terms", "term"),
        "outline": [s for p in partials for s in p.get("sections", [])],
        "abbreviations": _dedupe(partials, "abbreviations", "abbrev"),
        "entities": _dedupe(partials, "entities", "name"),
        "difficulty_flags": [f for p in partials for f in p.get("flags", [])],
        "style_notes": style_notes,
    }


register(Survey())
