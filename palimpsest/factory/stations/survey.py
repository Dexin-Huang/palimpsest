"""survey: manuscript-level pass over all transcriptions that builds the
translation brief — the jig the page line's translate station clamps into
(lifted from survey.py)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator

from palimpsest.factory.usage import combine_cost, combine_count
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway import (
    GatewayError,
    ModelRequest,
    ModelResponse,
    generate_json,
)
from palimpsest.factory.workspace.io import read_json

TOKENS_PER_CHAR = 0.35  # rough estimate for mixed Latin/abbreviations
MAX_TOKENS_PER_CHUNK = 20_000


def _entry(*fields: str) -> dict:
    return {
        "type": "object",
        "properties": {field: {"type": "string"} for field in fields},
        "required": list(fields[:2]),
    }


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {"type": "array", "items": _entry("term", "translation", "note")},
        "sections": {"type": "array", "items": _entry("start_page", "description")},
        "abbreviations": {"type": "array", "items": _entry("abbrev", "expansion")},
        "entities": {"type": "array", "items": _entry("name", "translation")},
        "flags": {"type": "array", "items": _entry("page_id", "issue")},
        "style_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "terms",
        "sections",
        "abbreviations",
        "entities",
        "flags",
        "style_notes",
    ],
}


class Survey(Station):
    name = "survey"

    grain = "manuscript"
    consumes = ("page_transcription",)
    produces = "translation_brief"
    uses_model = True
    param_keys = frozenset({"temperature", "max_output_tokens"})
    option_keys = frozenset({"max_tokens_per_chunk"})
    production_dependencies = (
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
        "factory/gateway/protocol.py",
        "factory/usage.py",
    )

    def run(self, job: Job) -> StationResult:
        records = [
            read_json(job.path_of("page_transcription", page["page_id"]))
            for page in job.pages
        ]
        partials: list[dict] = []
        tokens_in = tokens_out = 0
        cost = 0.0
        for chunk in _chunk(
            records,
            int(job.config.options.get("max_tokens_per_chunk", MAX_TOKENS_PER_CHUNK)),
        ):
            chunk_text = "\n\n".join(
                f"[{r['page_id']}]\n{r['text']}" for r in chunk if r["text"].strip()
            )
            try:
                partial, response = generate_json(
                    ModelRequest(
                        model=job.config.model,
                        prompt=job.config.prompt.text + "\n\n" + chunk_text,
                        temperature=job.config.params.get("temperature", 0.1),
                        max_output_tokens=job.config.params.get(
                            "max_output_tokens", 32768
                        ),
                        json_output=True,
                        json_schema=BRIEF_SCHEMA,
                    )
                )
            except GatewayError as error:
                raise error.with_prior_usage(
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                ) from error
            partials.append(partial)
            tokens_in += response.prompt_tokens
            tokens_out += response.billable_output_tokens
            cost = combine_cost(cost, response.cost_usd)

        return StationResult(
            payload=_merge(partials, doc_id=job.doc_id, total_pages=len(records)),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )


def _chunk(records: list[dict], max_tokens: int) -> Iterator[list[dict]]:
    total_tokens = int(sum(len(r["text"]) * TOKENS_PER_CHAR for r in records))
    num_chunks = max(1, math.ceil(total_tokens / max_tokens))
    chunk_size = math.ceil(len(records) / num_chunks)
    for index in range(0, len(records), chunk_size):
        yield records[index : index + chunk_size]


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
        "document": {"doc_id": doc_id, "total_pages": total_pages},
        "glossary": _dedupe(partials, "terms", "term"),
        "outline": [s for p in partials for s in p.get("sections", [])],
        "abbreviations": _dedupe(partials, "abbreviations", "abbrev"),
        "entities": _dedupe(partials, "entities", "name"),
        "difficulty_flags": [f for p in partials for f in p.get("flags", [])],
        "style_notes": style_notes,
    }


_PAGE_IDS = {
    "type": "array",
    "items": {"type": "string"},
}
_ANCHORED_EVIDENCE = {
    "page_ids": _PAGE_IDS,
    "evidence": {"type": "string"},
}
MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "persons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "translation": {"type": "string"},
                    **_ANCHORED_EVIDENCE,
                },
                "required": ["name", "translation", "page_ids", "evidence"],
                "additionalProperties": False,
            },
        },
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "translation": {"type": "string"},
                    **_ANCHORED_EVIDENCE,
                },
                "required": ["name", "translation", "page_ids", "evidence"],
                "additionalProperties": False,
            },
        },
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "normalized": {"type": "string"},
                    **_ANCHORED_EVIDENCE,
                },
                "required": ["expression", "normalized", "page_ids", "evidence"],
                "additionalProperties": False,
            },
        },
        "terminology": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "translation": {"type": "string"},
                    "note": {"type": "string"},
                    **_ANCHORED_EVIDENCE,
                },
                "required": [
                    "term",
                    "translation",
                    "note",
                    "page_ids",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "uncertainties": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["page_id", "issue", "evidence"],
                "additionalProperties": False,
            },
        },
        "sections": {"type": "array", "items": _entry("start_page", "description")},
        "abbreviations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "abbrev": {"type": "string"},
                    "expansion": {"type": "string"},
                    **_ANCHORED_EVIDENCE,
                },
                "required": [
                    "abbrev",
                    "expansion",
                    "page_ids",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "style_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "persons",
        "places",
        "dates",
        "terminology",
        "uncertainties",
        "sections",
        "abbreviations",
        "style_notes",
    ],
    "additionalProperties": False,
}












def _anchored_transcription(record: dict) -> str:
    part = f" part={record['part']}/{record['parts']}" if "part" in record else ""
    return f"[page_id={record['page_id']}{part}]\n{record['text']}"






register(Survey())
