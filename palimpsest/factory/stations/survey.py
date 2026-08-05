"""survey: manuscript-level pass over all transcriptions that builds the
translation brief — the jig the page line's translate station clamps into
(lifted from survey.py)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor

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
REDUCED_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "translation": {"type": "string"},
                    "note": {"type": "string"},
                    "page_ids": _PAGE_IDS,
                    "evidence": {"type": "string"},
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
        "outline": {"type": "array", "items": _entry("start_page", "description")},
        "abbreviations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "abbrev": {"type": "string"},
                    "expansion": {"type": "string"},
                    "page_ids": _PAGE_IDS,
                    "evidence": {"type": "string"},
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
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "translation": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["person", "place", "other"],
                    },
                    "page_ids": _PAGE_IDS,
                    "evidence": {"type": "string"},
                },
                "required": [
                    "name",
                    "translation",
                    "kind",
                    "page_ids",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "difficulty_flags": {
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
        "style_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "glossary",
        "outline",
        "abbreviations",
        "entities",
        "difficulty_flags",
        "style_notes",
    ],
    "additionalProperties": False,
}


class OrderedMapReduceSurvey(Survey):
    """Parallel evidence extraction followed by one ordered reconciliation."""

    variant = "ordered-map-reduce/v1"
    option_keys = Survey.option_keys | frozenset({"map_workers"})

    def run(self, job: Job) -> StationResult:
        map_workers = _map_workers(job)
        max_tokens = _positive_chunk_limit(job)
        records = [
            read_json(job.path_of("page_transcription", page["page_id"]))
            for page in job.pages
        ]
        chunks = _bounded_chunks(records, max_tokens)
        futures: list[Future[tuple[dict, ModelResponse]]]
        with ThreadPoolExecutor(
            max_workers=min(map_workers, len(chunks)),
            thread_name_prefix="survey-map",
        ) as executor:
            futures = [
                executor.submit(
                    _map_chunk,
                    job,
                    chunk,
                    index=index,
                    total=len(chunks),
                )
                for index, chunk in enumerate(chunks)
            ]

            partials: list[dict] = []
            responses: list[ModelResponse] = []
            failures: list[GatewayError] = []
            for future in futures:
                try:
                    partial, response = future.result()
                except GatewayError as error:
                    failures.append(error)
                else:
                    partials.append(partial)
                    responses.append(response)

        tokens_in, tokens_out, cost = _response_usage(responses)
        if failures:
            for error in failures[1:]:
                tokens_in = combine_count(tokens_in, error.tokens_in)
                tokens_out = combine_count(tokens_out, error.tokens_out)
                cost = combine_cost(cost, error.cost_usd)
            raise failures[0].with_prior_usage(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            ) from failures[0]

        try:
            reduced, reducer_response = generate_json(
                ModelRequest(
                    model=job.config.model,
                    prompt=_reducer_prompt(job, partials),
                    temperature=job.config.params.get("temperature", 0.1),
                    max_output_tokens=job.config.params.get("max_output_tokens", 32768),
                    json_output=True,
                    json_schema=REDUCED_BRIEF_SCHEMA,
                )
            )
        except GatewayError as error:
            raise error.with_prior_usage(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            ) from error

        tokens_in, tokens_out, cost = _response_usage(
            [reducer_response],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
        )
        return StationResult(
            payload={
                "document": {
                    "doc_id": job.doc_id,
                    "total_pages": len(records),
                },
                **reduced,
            },
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )


def _map_workers(job: Job) -> int:
    if "map_workers" not in job.config.options:
        raise ValueError(
            "survey ordered-map-reduce requires an explicit positive map_workers option"
        )
    value = job.config.options["map_workers"]
    if type(value) is not int or value <= 0:
        raise ValueError("survey map_workers must be a positive integer")
    return value


def _positive_chunk_limit(job: Job) -> int:
    value = job.config.options.get("max_tokens_per_chunk", MAX_TOKENS_PER_CHUNK)
    if type(value) is not int or value <= 0:
        raise ValueError("survey max_tokens_per_chunk must be a positive integer")
    return value


def _bounded_chunks(records: list[dict], max_tokens: int) -> list[list[dict]]:
    max_chars = max(1, math.floor(max_tokens / TOKENS_PER_CHAR))
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for record in records:
        text = str(record.get("text", ""))
        segments = [
            text[start : start + max_chars] for start in range(0, len(text), max_chars)
        ] or [""]
        for index, segment in enumerate(segments, start=1):
            if current and current_chars + len(segment) > max_chars:
                chunks.append(current)
                current = []
                current_chars = 0
            anchored = {"page_id": str(record["page_id"]), "text": segment}
            if len(segments) > 1:
                anchored["part"] = index
                anchored["parts"] = len(segments)
            current.append(anchored)
            current_chars += len(segment)

    if current:
        chunks.append(current)
    return chunks or [[]]


def _map_chunk(
    job: Job,
    chunk: list[dict],
    *,
    index: int,
    total: int,
) -> tuple[dict, ModelResponse]:
    anchored_text = "\n\n".join(_anchored_transcription(record) for record in chunk)
    return generate_json(
        ModelRequest(
            model=job.config.model,
            prompt=(
                f"SURVEY_MAP_PASS chunk={index + 1}/{total}\n"
                "Extract structured person, place, date, terminology, and "
                "uncertainty evidence. Every item must cite the page anchor(s) "
                "that support it. The configured guidance below controls "
                "coverage, while the request JSON schema controls field names.\n\n"
                f"CONFIGURED_GUIDANCE:\n{job.config.prompt.text}\n\n"
                f"PAGE_ANCHORED_TRANSCRIPTIONS:\n{anchored_text}"
            ),
            temperature=job.config.params.get("temperature", 0.1),
            max_output_tokens=job.config.params.get("max_output_tokens", 32768),
            json_output=True,
            json_schema=MAP_SCHEMA,
        )
    )


def _anchored_transcription(record: dict) -> str:
    part = f" part={record['part']}/{record['parts']}" if "part" in record else ""
    return f"[page_id={record['page_id']}{part}]\n{record['text']}"


def _reducer_prompt(job: Job, partials: list[dict]) -> str:
    ordered_results = json.dumps(
        partials,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "SURVEY_REDUCE_PASS\n"
        "Reconcile the ordered map results into one translation brief. Merge "
        "duplicate or conflicting people, places, dates, terminology, "
        "abbreviations, and uncertainties without discarding page anchors or "
        "quoted evidence. Represent people and places as entities; represent "
        "dates and terminology in the glossary. Preserve manuscript order for "
        "the outline and first-supported ordering for all reconciled lists. "
        "Return exactly the request JSON schema.\n\n"
        f"CONFIGURED_GUIDANCE:\n{job.config.prompt.text}\n\n"
        f"ORDERED_MAP_RESULTS_JSON:\n{ordered_results}"
    )


def _response_usage(
    responses: list[ModelResponse],
    *,
    tokens_in: int | None = 0,
    tokens_out: int | None = 0,
    cost: float | None = 0.0,
) -> tuple[int | None, int | None, float | None]:
    for response in responses:
        tokens_in = combine_count(tokens_in, response.prompt_tokens)
        tokens_out = combine_count(tokens_out, response.billable_output_tokens)
        cost = combine_cost(cost, response.cost_usd)
    return tokens_in, tokens_out, cost


register(Survey())
register(OrderedMapReduceSurvey())
