"""Survey pass: parallel agents scan manuscript chunks to build a translation brief.

Shared by both Approach A (deterministic batch) and Approach B (multi-agent).
Produces a translation_brief.json that freezes terminology before translation begins.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.model_io import load_prompt, response_text, strip_json_fences
from palimpsest.models.enriched import TranscriptionRecord

TOKENS_PER_CHAR = 0.35  # rough estimate for mixed Latin/abbreviations
MAX_TOKENS_PER_CHUNK = 20_000
DEFAULT_SURVEY_PROMPT = "survey_brief"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_records(input_path: Path) -> list[TranscriptionRecord]:
    records: list[TranscriptionRecord] = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(TranscriptionRecord.model_validate_json(line))
        except Exception:
            continue
    return records


def _estimate_tokens(records: list[TranscriptionRecord]) -> int:
    return int(sum(len(r.text) * TOKENS_PER_CHAR for r in records))


def _chunk_records(
    records: list[TranscriptionRecord],
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
) -> list[list[TranscriptionRecord]]:
    """Split records into chunks respecting token budget."""
    total_tokens = _estimate_tokens(records)
    num_chunks = max(1, math.ceil(total_tokens / max_tokens))
    chunk_size = math.ceil(len(records) / num_chunks)

    chunks = []
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i + chunk_size]
        if chunk:
            chunks.append(chunk)
    return chunks


def _format_chunk_text(records: list[TranscriptionRecord]) -> str:
    """Format a chunk of records for the survey prompt."""
    parts = []
    for r in records:
        if r.text.strip():
            parts.append(f"[{r.page_id}]\n{r.text}")
    return "\n\n".join(parts)


def _survey_chunk(
    client: genai.Client,
    chunk: list[TranscriptionRecord],
    *,
    prompt_text: str,
    model: str,
) -> dict:
    """Survey one chunk, return partial brief as dict."""
    chunk_text = _format_chunk_text(chunk)
    full_prompt = prompt_text + "\n\n" + chunk_text

    response = client.models.generate_content(
        model=model,
        contents=[full_prompt],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )
    text, _ = response_text(response)
    return json.loads(strip_json_fences(text))


def _merge_briefs(partials: list[dict], records: list[TranscriptionRecord]) -> dict:
    """Merge partial briefs from multiple chunks into one translation brief."""
    # Deduplicate terms by normalized term text
    terms_seen: dict[str, dict] = {}
    for partial in partials:
        for term in partial.get("terms", []):
            key = term.get("term", "").lower().strip()
            if key and key not in terms_seen:
                terms_seen[key] = term

    # Merge sections in order
    all_sections = []
    for partial in partials:
        all_sections.extend(partial.get("sections", []))

    # Deduplicate abbreviations
    abbrevs_seen: dict[str, dict] = {}
    for partial in partials:
        for abbrev in partial.get("abbreviations", []):
            key = abbrev.get("abbrev", "").strip()
            if key and key not in abbrevs_seen:
                abbrevs_seen[key] = abbrev

    # Deduplicate entities
    entities_seen: dict[str, dict] = {}
    for partial in partials:
        for entity in partial.get("entities", []):
            key = entity.get("name", "").lower().strip()
            if key and key not in entities_seen:
                entities_seen[key] = entity

    # Collect all flags
    all_flags = []
    for partial in partials:
        all_flags.extend(partial.get("flags", []))

    # Collect style notes
    all_style = []
    for partial in partials:
        all_style.extend(partial.get("style_notes", []))

    # Build metadata from records
    first = records[0] if records else None

    return {
        "version": 1,
        "created_at": _utc_now(),
        "document": {
            "source": first.source if first else "",
            "book_title": first.book_title if first else "",
            "total_pages": len(records),
        },
        "glossary": list(terms_seen.values()),
        "outline": all_sections,
        "abbreviations": list(abbrevs_seen.values()),
        "entities": list(entities_seen.values()),
        "difficulty_flags": all_flags,
        "style_notes": list(set(all_style)),
    }


async def run_survey(
    input_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    prompt_name: str = DEFAULT_SURVEY_PROMPT,
    max_tokens_per_chunk: int = MAX_TOKENS_PER_CHUNK,
) -> dict:
    """Survey a transcription file and produce a translation brief."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records = _load_records(input_path)
    if not records:
        raise ValueError(f"No records in {input_path}")

    chunks = _chunk_records(records, max_tokens=max_tokens_per_chunk)
    prompt_text = load_prompt(prompt_name)
    client = genai.Client()

    print(f"Surveying {len(records)} pages in {len(chunks)} chunk(s) ({model})")

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            None,
            lambda c=chunk: _survey_chunk(client, c, prompt_text=prompt_text, model=model),
        )
        for chunk in chunks
    ]
    partials = await asyncio.gather(*tasks)

    brief = _merge_briefs(list(partials), records)
    output_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Brief: {output_path}")
    print(f"  Glossary terms: {len(brief['glossary'])}")
    print(f"  Abbreviations: {len(brief['abbreviations'])}")
    print(f"  Entities: {len(brief['entities'])}")
    print(f"  Flagged pages: {len(brief['difficulty_flags'])}")

    return brief


def run_survey_sync(
    input_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    prompt_name: str = DEFAULT_SURVEY_PROMPT,
) -> dict:
    return asyncio.run(run_survey(input_path, output_path, model=model, prompt_name=prompt_name))
