"""Enrichment pipeline: translate transcribed manuscript pages via LLM.

Reads transcriptions.jsonl, sends each page's text to a Gemini model for
translation, and writes enriched.jsonl with the additional fields.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.model_io import load_prompt, response_text
from palimpsest.models.enriched import TranscriptionRecord, EnrichedRecord

DEFAULT_PROMPT_NAME = "enrich_translate"
DEFAULT_WORKERS = 8

PRICING = {
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-preview": {"input": 0.15, "output": 0.60},
    "gemini-3.1-flash-lite-preview": {"input": 0.02, "output": 0.10},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    prices = PRICING.get(model)
    if prices is None:
        return None
    return (prompt_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def _load_transcription_records(input_path: Path) -> list[TranscriptionRecord]:
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


def _load_existing_page_ids(output_path: Path) -> set[str]:
    ids: set[str] = set()
    if not output_path.exists():
        return ids
    for line in output_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line)["page_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def enrich_page(
    client: genai.Client,
    record: TranscriptionRecord,
    *,
    prompt_text: str,
    model: str = DEFAULT_MODEL_READING,
) -> tuple[str, dict]:
    """Translate a single page's text. Returns (translation, usage_info)."""
    full_prompt = prompt_text + "\n\n" + record.text

    response = client.models.generate_content(
        model=model,
        contents=[full_prompt],
        config=types.GenerateContentConfig(
            temperature=0.1,
        ),
    )
    translation, _ = response_text(response)

    usage = getattr(response, "usage_metadata", None)
    usage_info = {}
    if usage:
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        usage_info = {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": getattr(usage, "total_token_count", 0) or 0,
            "cost_usd": _estimate_cost(model, prompt_tokens, output_tokens),
        }

    return translation, usage_info


async def _enrich_worker(
    queue: asyncio.Queue,
    client: genai.Client,
    *,
    prompt_text: str,
    model: str,
    output_path: Path,
    lock: asyncio.Lock,
    stats: dict,
):
    loop = asyncio.get_event_loop()
    while True:
        record: TranscriptionRecord | None = await queue.get()
        if record is None:
            queue.task_done()
            break
        page_id = record.page_id
        try:
            translation, usage_info = await loop.run_in_executor(
                None,
                lambda r=record: enrich_page(
                    client, r,
                    prompt_text=prompt_text,
                    model=model,
                ),
            )
            enriched = EnrichedRecord(
                source=record.source,
                book_title=record.book_title,
                page_id=record.page_id,
                text=record.text,
                translation=translation,
                translation_model=model,
                translation_timestamp=_utc_now(),
                prompt_tokens=usage_info.get("prompt_tokens"),
                output_tokens=usage_info.get("output_tokens"),
                total_tokens=usage_info.get("total_tokens"),
                cost_usd=usage_info.get("cost_usd"),
            )
            async with lock:
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(enriched.model_dump_json(exclude_none=True) + "\n")
                stats["total_cost"] = stats.get("total_cost", 0.0) + (usage_info.get("cost_usd") or 0.0)
            stats["done"] += 1
            cost_str = f" ${usage_info['cost_usd']:.4f}" if usage_info.get("cost_usd") else ""
            tokens_str = f" {usage_info.get('prompt_tokens', '?')}+{usage_info.get('output_tokens', '?')} tok" if usage_info else ""
            print(f"  [{stats['done']}/{stats['total']}] {page_id} ({len(translation)} chars){tokens_str}{cost_str}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  [ERROR] {page_id}: {exc}")
        finally:
            queue.task_done()


async def run_enrichment(
    input_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    prompt_name: str = DEFAULT_PROMPT_NAME,
    workers: int = DEFAULT_WORKERS,
    skip_existing: bool = False,
) -> Path:
    """Enrich all transcription records with translations."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    records = _load_transcription_records(input_path)
    if not records:
        raise ValueError(f"No transcription records found in {input_path}")

    if skip_existing:
        existing = _load_existing_page_ids(output_path)
        records = [r for r in records if r.page_id not in existing]
        if not records:
            print("All pages already enriched.")
            return output_path

    prompt_text = load_prompt(prompt_name)
    client = genai.Client()
    queue: asyncio.Queue = asyncio.Queue()
    lock = asyncio.Lock()
    stats = {"done": 0, "errors": 0, "total": len(records)}

    print(f"Enriching {len(records)} pages with {workers} workers ({model})")
    print(f"Output: {output_path}")

    for record in records:
        await queue.put(record)
    for _ in range(workers):
        await queue.put(None)

    worker_tasks = [
        asyncio.create_task(
            _enrich_worker(
                queue, client,
                prompt_text=prompt_text,
                model=model,
                output_path=output_path,
                lock=lock,
                stats=stats,
            )
        )
        for _ in range(workers)
    ]
    await asyncio.gather(*worker_tasks)

    total_cost = stats.get("total_cost", 0.0)
    cost_line = f", total cost: ${total_cost:.4f}" if total_cost else ""
    print(f"Done: {stats['done']} pages, {stats['errors']} errors{cost_line}")
    return output_path


def run_enrichment_sync(
    input_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    prompt_name: str = "enrich_translate",
    workers: int = 8,
    skip_existing: bool = False,
) -> Path:
    """Sync wrapper around run_enrichment."""
    return asyncio.run(
        run_enrichment(
            input_path,
            output_path,
            model=model,
            prompt_name=prompt_name,
            workers=workers,
            skip_existing=skip_existing,
        )
    )
