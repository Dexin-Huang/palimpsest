"""Approach A: deterministic 3-pass batch translation with brief + overlap.

Pass 1 — Translate every page with left/right source-text context.
Pass 2 — Repair: re-translate pages flagged as starts_mid_sentence or
          ends_mid_sentence, injecting neighboring *translations* as
          additional context.
Pass 3 — Write final enriched_a.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.model_io import load_prompt, response_text
from palimpsest.models.enriched import TranscriptionRecord, EnrichedRecord

DEFAULT_WORKERS = 8
PROMPT_NAME = "translate_with_brief"

PRICING = {
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-preview": {"input": 0.15, "output": 0.60},
    "gemini-3.1-flash-lite-preview": {"input": 0.02, "output": 0.10},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}

_FLAGS_RE = re.compile(r"---FLAGS---\s*\n(.*?)\n\s*---END FLAGS---", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _estimate_cost(model: str, pt: int, ot: int) -> float | None:
    p = PRICING.get(model)
    return (pt * p["input"] + ot * p["output"]) / 1_000_000 if p else None


def _load_records(path: Path) -> list[TranscriptionRecord]:
    records: list[TranscriptionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(TranscriptionRecord.model_validate_json(line))
        except Exception:
            continue
    return records


def _load_existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ids.add(json.loads(line)["page_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def _parse_flags(text: str) -> tuple[str, dict]:
    """Split translation text from the ---FLAGS--- block."""
    m = _FLAGS_RE.search(text)
    if not m:
        return text.strip(), {}
    clean = text[: m.start()].strip()
    try:
        flags = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        flags = {}
    return clean, flags


def _neighbor_text(
    records: list[TranscriptionRecord],
    idx: int,
    overlap: int,
    translations: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Build left/right context strings for page at *idx*.

    When *translations* is provided (repair pass), each neighbor block
    includes both source text and its existing translation.
    """
    def _fmt(i: int) -> str:
        pid, src = records[i].page_id, records[i].text
        if translations is not None:
            tr = translations.get(pid, "")
            return f"[{pid}]\nSource: {src}\nTranslation: {tr}"
        return f"[{pid}]\n{src}"

    left = "\n\n".join(_fmt(i) for i in range(max(0, idx - overlap), idx))
    right = "\n\n".join(
        _fmt(i) for i in range(idx + 1, min(len(records), idx + 1 + overlap))
    )
    return left or "(none)", right or "(none)"


def _fill_prompt(template: str, brief: str, page: str, left: str, right: str) -> str:
    return (
        template
        .replace("{BRIEF}", brief)
        .replace("{PAGE_TEXT}", page)
        .replace("{LEFT_CONTEXT}", left)
        .replace("{RIGHT_CONTEXT}", right)
    )


# ---------------------------------------------------------------------------
# Single-page API call
# ---------------------------------------------------------------------------

def _translate_page(client: genai.Client, prompt: str, model: str) -> tuple[str, dict]:
    resp = client.models.generate_content(
        model=model, contents=[prompt],
        config=types.GenerateContentConfig(temperature=0.1),
    )
    text, _ = response_text(resp)
    usage = getattr(resp, "usage_metadata", None)
    info: dict = {}
    if usage:
        pt = getattr(usage, "prompt_token_count", 0) or 0
        ot = getattr(usage, "candidates_token_count", 0) or 0
        info = {
            "prompt_tokens": pt, "output_tokens": ot,
            "total_tokens": getattr(usage, "total_token_count", 0) or 0,
            "cost_usd": _estimate_cost(model, pt, ot),
        }
    return text, info


# ---------------------------------------------------------------------------
# Unified async worker (serves both passes)
# ---------------------------------------------------------------------------

async def _worker(
    queue: asyncio.Queue,
    client: genai.Client,
    *,
    template: str,
    brief: str,
    records: list[TranscriptionRecord],
    model: str,
    overlap: int,
    results: dict,
    lock: asyncio.Lock,
    stats: dict,
    translations: dict[str, str] | None = None,
    tag: str = "P1",
):
    """Translate pages from *queue*.

    If *translations* is None (pass 1) context is source-only.
    If provided (pass 2) context includes existing translations.
    """
    loop = asyncio.get_event_loop()
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        idx: int = item
        rec = records[idx]
        pid = rec.page_id
        try:
            left, right = _neighbor_text(records, idx, overlap, translations)
            prompt = _fill_prompt(template, brief, rec.text, left, right)
            raw, usage = await loop.run_in_executor(
                None, lambda p=prompt: _translate_page(client, p, model),
            )
            tr, flags = _parse_flags(raw)

            async with lock:
                if pid in results:
                    # Repair pass — accumulate costs
                    old = results[pid]["usage"]
                    for k in ("prompt_tokens", "output_tokens", "total_tokens"):
                        old[k] = (old.get(k) or 0) + (usage.get(k) or 0)
                    old["cost_usd"] = (old.get("cost_usd") or 0) + (usage.get("cost_usd") or 0)
                    results[pid]["translation"] = tr
                    results[pid]["flags"] = flags
                else:
                    results[pid] = {"translation": tr, "flags": flags, "usage": usage, "idx": idx}
                stats["total_cost"] += usage.get("cost_usd") or 0.0

            stats["done"] += 1
            cost_s = f" ${usage.get('cost_usd', 0):.4f}" if usage.get("cost_usd") else ""
            print(f"  [{tag} {stats['done']}/{stats['total']}] {pid}{cost_s}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  [{tag} ERROR] {pid}: {exc}")
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# Queue launcher helper
# ---------------------------------------------------------------------------

async def _launch(indices, workers, worker_kwargs):
    q: asyncio.Queue = asyncio.Queue()
    for idx in indices:
        await q.put(idx)
    for _ in range(workers):
        await q.put(None)
    tasks = [asyncio.create_task(_worker(q, **worker_kwargs)) for _ in range(workers)]
    await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _run_batch(
    input_path: Path, output_path: Path, brief_path: Path,
    *, model: str, workers: int, skip_existing: bool, overlap: int,
) -> Path:
    input_path, output_path, brief_path = (
        input_path.resolve(), output_path.resolve(), brief_path.resolve(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if not brief_path.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")

    records = _load_records(input_path)
    if not records:
        raise ValueError(f"No records in {input_path}")

    brief = brief_path.read_text(encoding="utf-8")
    template = load_prompt(PROMPT_NAME)
    client = genai.Client()

    if skip_existing:
        existing = _load_existing_ids(output_path)
        todo = [i for i, r in enumerate(records) if r.page_id not in existing]
    else:
        todo = list(range(len(records)))
    if not todo:
        print("All pages already translated.")
        return output_path

    # --- Pass 1 -----------------------------------------------------------
    results: dict = {}
    lock = asyncio.Lock()
    stats = {"done": 0, "errors": 0, "total": len(todo), "total_cost": 0.0}
    print(f"Pass 1: translating {len(todo)} pages ({workers} workers, {model})")

    common = dict(
        client=client, template=template, brief=brief,
        records=records, model=model, overlap=overlap,
        results=results, lock=lock, stats=stats,
    )
    await _launch(todo, workers, {**common, "translations": None, "tag": "P1"})
    print(f"Pass 1 done: {stats['done']} ok, {stats['errors']} errors")

    # --- Pass 2 — repair flagged pages ------------------------------------
    flagged = sorted(
        r["idx"] for r in results.values()
        if r.get("flags", {}).get("starts_mid_sentence")
        or r.get("flags", {}).get("ends_mid_sentence")
    )
    if flagged:
        translations = {pid: r["translation"] for pid, r in results.items()}
        stats.update(done=0, errors=0, total=len(flagged))
        print(f"Pass 2: repairing {len(flagged)} flagged pages")
        await _launch(flagged, workers, {
            **common, "translations": translations, "tag": "P2",
        })
        print(f"Pass 2 done: {stats['done']} repaired, {stats['errors']} errors")
    else:
        print("Pass 2: no flagged pages — skipping repair.")

    # --- Pass 3 — write output --------------------------------------------
    print(f"Pass 3: writing {output_path}")
    written = 0
    with open(output_path, "a", encoding="utf-8") as fh:
        for idx in todo:
            pid = records[idx].page_id
            if pid not in results:
                continue
            r = results[pid]
            rec = EnrichedRecord(
                source=records[idx].source,
                book_title=records[idx].book_title,
                page_id=pid,
                text=records[idx].text,
                translation=r["translation"],
                translation_model=model,
                translation_timestamp=_utc_now(),
                prompt_tokens=r["usage"].get("prompt_tokens"),
                output_tokens=r["usage"].get("output_tokens"),
                total_tokens=r["usage"].get("total_tokens"),
                cost_usd=r["usage"].get("cost_usd"),
                flags=r.get("flags") or None,
            )
            fh.write(rec.model_dump_json(exclude_none=True) + "\n")
            written += 1

    cost = stats.get("total_cost", 0.0)
    print(f"Done: {written} pages written{f', ${cost:.4f}' if cost else ''}")
    return output_path


# ---------------------------------------------------------------------------
# Public sync entry point
# ---------------------------------------------------------------------------

def run_batch_translation_sync(
    input_path: Path,
    output_path: Path,
    brief_path: Path,
    *,
    model: str = DEFAULT_MODEL_READING,
    workers: int = DEFAULT_WORKERS,
    skip_existing: bool = False,
    overlap: int = 2,
) -> Path:
    """Run the 3-pass batch translation pipeline.

    Pass 1 — Translate every page with source-text overlap context.
    Pass 2 — Repair pages flagged as mid-sentence with neighbor translations.
    Pass 3 — Write enriched_a.jsonl.
    """
    return asyncio.run(
        _run_batch(
            input_path, output_path, brief_path,
            model=model, workers=workers,
            skip_existing=skip_existing, overlap=overlap,
        )
    )
