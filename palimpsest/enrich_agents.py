"""Approach B: Multi-agent translation pipeline.

Phases:
  1. Survey (pre-existing) — produces translation_brief.json
  2. Lexicographer — Claude refines the brief's glossary / entities / abbrevs
  3. Parallel translators — Gemini translates pages with the locked brief
  4. Reviewer — Claude checks chunk boundaries for split sentences / term drift
"""

from __future__ import annotations

import asyncio, json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING, DEFAULT_MODEL_SCHOLAR_AGENT
from palimpsest.model_io import load_prompt, response_text, strip_json_fences
from palimpsest.models.enriched import EnrichedRecord, TranscriptionRecord

CHUNK_SIZE = 25
BOUNDARY_OVERLAP = 2
DEFAULT_WORKERS = 8

PRICING = {
    "gemini-3.1-pro-preview":       {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-preview":     {"input": 0.15, "output": 0.60},
    "gemini-3.1-flash-lite-preview":{"input": 0.02, "output": 0.10},
    "gemini-2.5-pro":               {"input": 1.25, "output": 10.00},
}

_utc_now = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _estimate_cost(model: str, p: int, o: int) -> float | None:
    pr = PRICING.get(model)
    return (p * pr["input"] + o * pr["output"]) / 1_000_000 if pr else None


# -- IO helpers --------------------------------------------------------------

def _load_records(path: Path) -> list[TranscriptionRecord]:
    out: list[TranscriptionRecord] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try: out.append(TranscriptionRecord.model_validate_json(ln))
            except Exception: pass
    return out


def _load_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try: ids.add(json.loads(ln)["page_id"])
            except Exception: pass
    return ids


def _chunk_records(records: list[TranscriptionRecord]) -> list[list[TranscriptionRecord]]:
    n = max(1, math.ceil(len(records) / CHUNK_SIZE))
    sz = math.ceil(len(records) / n)
    return [records[i:i + sz] for i in range(0, len(records), sz)]


def _format_brief(brief: dict[str, Any]) -> str:
    """Render brief sections into a compact text block for prompts."""
    sections: list[str] = []
    if brief.get("glossary"):
        lines = ["GLOSSARY:"]
        for g in brief["glossary"]:
            note = f' -- {g["note"]}' if g.get("note") else ""
            lines.append(f'  {g["term"]} -> {g["translation"]}{note}')
        sections.append("\n".join(lines))
    if brief.get("abbreviation_policy"):
        lines = ["ABBREVIATIONS:"]
        for a in brief["abbreviation_policy"]:
            lines.append(f'  {a["abbrev"]} -> {a["expansion"]}')
        sections.append("\n".join(lines))
    if brief.get("named_entities"):
        lines = ["NAMED ENTITIES:"]
        for e in brief["named_entities"]:
            lines.append(f'  {e["name"]} -> {e["translation"]}')
        sections.append("\n".join(lines))
    if brief.get("style_rules"):
        lines = ["STYLE RULES:"]
        lines.extend(f"  - {r}" for r in brief["style_rules"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


# -- Phase 2: Lexicographer (Claude) ----------------------------------------

async def _query_claude(prompt: str) -> str:
    """Send a query to Claude via Agent SDK and return the result text."""
    import claude_agent_sdk
    result_text = ""
    async for message in claude_agent_sdk.query(prompt=prompt):
        if hasattr(message, 'result'):
            result_text = message.result
        elif hasattr(message, 'content'):
            # AssistantMessage
            content = message.content
            if isinstance(content, str):
                result_text = content
            elif isinstance(content, list):
                for block in content:
                    if hasattr(block, 'text'):
                        result_text += block.text
    return result_text


async def _run_lexicographer(brief: dict[str, Any], *, model: str) -> dict[str, Any]:
    prompt = load_prompt("agent_lexicographer")
    payload = {k: brief.get(k, []) for k in
               ("glossary", "abbreviation_policy", "named_entities", "style_rules")}
    for k in ("document", "outline"):
        if k in brief:
            payload[k] = brief[k]

    print(f"  Lexicographer: refining brief with {model} ...")
    result_text = await _query_claude(
        prompt + "\n\nReview this translation brief:\n\n"
        + json.dumps(payload, indent=2, ensure_ascii=False),
    )
    refined = json.loads(strip_json_fences(result_text))
    for key in ("glossary", "abbreviation_policy", "named_entities", "style_rules"):
        if key in refined:
            brief[key] = refined[key]

    ng, ne = len(brief.get("glossary", [])), len(brief.get("named_entities", []))
    print(f"  Lexicographer done: {ng} glossary, {ne} entities")
    return brief


# -- Phase 3: Parallel translators (Gemini) ---------------------------------

def _translate_page(
    client: genai.Client, rec: TranscriptionRecord,
    *, sys_prompt: str, brief_ctx: str, model: str,
) -> tuple[str, dict]:
    prompt = (sys_prompt + "\n\n--- TRANSLATION BRIEF ---\n" + brief_ctx
              + "\n\n--- PAGE: " + rec.page_id + " ---\n" + rec.text)
    resp = client.models.generate_content(
        model=model, contents=[prompt],
        config=types.GenerateContentConfig(temperature=0.1),
    )
    text, _ = response_text(resp)
    usage = getattr(resp, "usage_metadata", None)
    info: dict[str, Any] = {}
    if usage:
        pi = getattr(usage, "prompt_token_count", 0) or 0
        po = getattr(usage, "candidates_token_count", 0) or 0
        info = {"prompt_tokens": pi, "output_tokens": po,
                "total_tokens": getattr(usage, "total_token_count", 0) or 0,
                "cost_usd": _estimate_cost(model, pi, po)}
    return text, info


async def _translate_worker(
    queue: asyncio.Queue, client: genai.Client, *,
    sys_prompt: str, brief_ctx: str, model: str,
    results: dict, lock: asyncio.Lock, stats: dict,
):
    loop = asyncio.get_event_loop()
    while True:
        rec: TranscriptionRecord | None = await queue.get()
        if rec is None:
            queue.task_done(); break
        try:
            trans, info = await loop.run_in_executor(
                None, lambda r=rec: _translate_page(
                    client, r, sys_prompt=sys_prompt, brief_ctx=brief_ctx, model=model))
            async with lock:
                results[rec.page_id] = (trans, info)
                stats["cost"] += info.get("cost_usd") or 0.0
            stats["done"] += 1
            c = f" ${info['cost_usd']:.4f}" if info.get("cost_usd") else ""
            t = f" {info.get('prompt_tokens','?')}+{info.get('output_tokens','?')} tok"
            print(f"  [{stats['done']}/{stats['total']}] {rec.page_id}{t}{c}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  [ERROR] {rec.page_id}: {exc}")
        finally:
            queue.task_done()


async def _run_translators(
    records: list[TranscriptionRecord], brief: dict[str, Any],
    *, model: str, workers: int,
) -> dict[str, tuple[str, dict]]:
    sys_prompt = load_prompt("agent_translate_chunk")
    brief_ctx = _format_brief(brief)
    client = genai.Client()
    queue: asyncio.Queue = asyncio.Queue()
    lock = asyncio.Lock()
    results: dict[str, tuple[str, dict]] = {}
    stats = {"done": 0, "errors": 0, "total": len(records), "cost": 0.0}

    print(f"  Translators: {len(records)} pages, {workers} workers ({model})")
    for r in records:
        await queue.put(r)
    for _ in range(workers):
        await queue.put(None)

    await asyncio.gather(*[
        asyncio.create_task(_translate_worker(
            queue, client, sys_prompt=sys_prompt, brief_ctx=brief_ctx,
            model=model, results=results, lock=lock, stats=stats))
        for _ in range(workers)
    ])
    cl = f", ${stats['cost']:.4f}" if stats["cost"] else ""
    print(f"  Translators done: {stats['done']} ok, {stats['errors']} errors{cl}")
    return results


# -- Phase 4: Reviewer (Claude) ---------------------------------------------

async def _run_reviewer(
    chunks: list[list[TranscriptionRecord]],
    translations: dict[str, tuple[str, dict]],
    brief: dict[str, Any], *, model: str,
) -> dict[str, str]:
    if len(chunks) <= 1:
        return {}
    prompt = load_prompt("agent_reviewer")
    brief_ctx = _format_brief(brief)
    corrections: dict[str, str] = {}
    nb = len(chunks) - 1
    print(f"  Reviewer: checking {nb} boundaries ...")

    for i in range(nb):
        tail = chunks[i][-BOUNDARY_OVERLAP:]
        head = chunks[i + 1][:BOUNDARY_OVERLAP]
        parts = ["--- BRIEF ---", brief_ctx, ""]
        for label, pages in [("END OF CHUNK", tail), ("START OF NEXT CHUNK", head)]:
            parts.append(f"--- {label} ---")
            for rec in pages:
                tr = translations.get(rec.page_id, ("(missing)", {}))[0]
                parts.append(f"\n[{rec.page_id}] LATIN:\n{rec.text}")
                parts.append(f"\n[{rec.page_id}] ENGLISH:\n{tr}")
        try:
            result_text = await _query_claude(
                prompt + "\n\n" + "\n".join(parts),
            )
            parsed = json.loads(strip_json_fences(result_text))
            ok = parsed.get("boundary_ok", True)
            tag = "OK" if ok else f"{len(parsed.get('issues', []))} issue(s)"
            print(f"    boundary {i+1}/{nb}: {tag}")
            for pid, fix in parsed.get("corrections", {}).items():
                if fix and isinstance(fix, str):
                    corrections[pid] = fix
        except Exception as exc:
            print(f"    boundary {i+1}/{nb}: [ERROR] {exc}")

    print(f"  Reviewer: {len(corrections)} correction(s)" if corrections
          else "  Reviewer: all boundaries clean")
    return corrections


# -- Orchestrator ------------------------------------------------------------

async def _run_pipeline(
    input_path: Path, output_path: Path, brief_path: Path, *,
    translation_model: str, agent_model: str, workers: int, skip_existing: bool,
) -> Path:
    input_path, output_path, brief_path = (
        input_path.resolve(), output_path.resolve(), brief_path.resolve())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if not brief_path.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")

    records = _load_records(input_path)
    if not records:
        raise ValueError(f"No transcription records in {input_path}")
    if skip_existing:
        existing = _load_existing_ids(output_path)
        records = [r for r in records if r.page_id not in existing]
        if not records:
            print("All pages already translated."); return output_path

    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    print(f"Approach B: {len(records)} pages, brief from {brief_path.name}")

    # Phase 2 — Lexicographer
    brief = await _run_lexicographer(brief, model=agent_model)
    refined_path = output_path.parent / "translation_brief_refined.json"
    refined_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Refined brief -> {refined_path}")

    # Phase 3 — Parallel translators
    chunks = _chunk_records(records)
    print(f"  {len(chunks)} chunk(s) of ~{CHUNK_SIZE} pages")
    translations = await _run_translators(
        records, brief, model=translation_model, workers=workers)

    # Phase 4 — Reviewer
    corrections = await _run_reviewer(chunks, translations, brief, model=agent_model)
    for pid, fix in corrections.items():
        if pid in translations:
            translations[pid] = (fix, translations[pid][1])

    # Write JSONL in original page order
    with open(output_path, "a", encoding="utf-8") as f:
        for rec in records:
            trans, info = translations.get(rec.page_id, ("", {}))
            if not trans:
                continue
            enriched = EnrichedRecord(
                source=rec.source, book_title=rec.book_title,
                page_id=rec.page_id, text=rec.text,
                translation=trans, translation_model=translation_model,
                translation_timestamp=_utc_now(),
                prompt_tokens=info.get("prompt_tokens"),
                output_tokens=info.get("output_tokens"),
                total_tokens=info.get("total_tokens"),
                cost_usd=info.get("cost_usd"),
            )
            f.write(enriched.model_dump_json(exclude_none=True) + "\n")

    print(f"Done. Output: {output_path}")
    return output_path


def run_agent_translation_sync(
    input_path: Path, output_path: Path, brief_path: Path, *,
    translation_model: str = DEFAULT_MODEL_READING,
    agent_model: str = DEFAULT_MODEL_SCHOLAR_AGENT,
    workers: int = DEFAULT_WORKERS,
    skip_existing: bool = False,
) -> Path:
    """Sync entry point for Approach B agent translation pipeline."""
    return asyncio.run(_run_pipeline(
        input_path, output_path, brief_path,
        translation_model=translation_model, agent_model=agent_model,
        workers=workers, skip_existing=skip_existing))
