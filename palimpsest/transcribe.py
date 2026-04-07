"""Single-call VLM transcription pipeline.

Feeds full, uncropped page images to a VLM with a minimal generic prompt.
Outputs one JSONL line per page with metadata alongside the raw text.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_TRANSCRIPTION
from palimpsest.model_io import load_prompt, response_text

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
DEFAULT_PROMPT_NAME = "transcribe_raw"
DEFAULT_SYSTEM_PROMPT = (
    "You are an expert archivist performing raw, zero-shot document digitization."
)
DEFAULT_MAX_OUTPUT_TOKENS = 65536
DEFAULT_WORKERS = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(suffix, "image/jpeg")


@dataclass
class PageRef:
    """A page resolved against page_list.json (or fallback) for transcription."""
    image_path: Path
    page_id: str
    page_seq: int
    canvas_id: str = ""


def _resolve_pages(image_dir: Path) -> list[PageRef]:
    """Resolve images to PageRef list using page_list.json if available.

    Looks for page_list.json in image_dir.parent (the doc_dir). If found,
    iterates pages in declared order, matching each entry to its image file
    in image_dir. If not found, falls back to alphabetical filesystem order
    with a logged warning — page_seq is the iteration index in that case.
    """
    doc_dir = image_dir.parent
    page_list_p = doc_dir / "page_list.json"

    if page_list_p.exists():
        try:
            data = json.loads(page_list_p.read_text(encoding="utf-8"))
            pages = data.get("pages", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [WARN] page_list.json unreadable ({exc}); falling back to filesystem order")
            pages = []

        if pages:
            refs: list[PageRef] = []
            for entry in pages:
                filename = entry.get("filename", "")
                if not filename:
                    continue
                img = image_dir / filename
                if not img.exists():
                    print(f"  [WARN] declared page {entry.get('page_id', '?')} missing image file {filename}")
                    continue
                refs.append(PageRef(
                    image_path=img,
                    page_id=entry.get("page_id") or img.stem,
                    page_seq=int(entry.get("order", 0)),
                    canvas_id="",
                ))
            return refs

    # Fallback: filesystem order, page_id from stem, page_seq from iteration index.
    print(f"  [WARN] no page_list.json at {page_list_p}; using filesystem order")
    paths = sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return [
        PageRef(image_path=p, page_id=p.stem, page_seq=i + 1, canvas_id="")
        for i, p in enumerate(paths)
    ]


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


PRICING = {
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-preview": {"input": 0.15, "output": 0.60},
    "gemini-3.1-flash-lite-preview": {"input": 0.02, "output": 0.10},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}


def _estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float | None:
    prices = PRICING.get(model)
    if prices is None:
        return None
    return (prompt_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def transcribe_page(
    client: genai.Client,
    image_path: Path,
    *,
    prompt_text: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    model: str = DEFAULT_MODEL_TRANSCRIPTION,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> tuple[str, dict]:
    """Transcribe a single page image. Returns (text, usage_info)."""
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_bytes(
                data=image_path.read_bytes(),
                mime_type=_mime_type(image_path),
            ),
        ],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
            max_output_tokens=max_output_tokens,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        ),
    )
    text, _ = response_text(response)

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

    return text, usage_info


async def _transcribe_worker(
    queue: asyncio.Queue,
    client: genai.Client,
    *,
    prompt_text: str,
    system_prompt: str,
    model: str,
    source: str,
    book_title: str,
    output_path: Path,
    lock: asyncio.Lock,
    stats: dict,
):
    loop = asyncio.get_event_loop()
    while True:
        item: PageRef | None = await queue.get()
        if item is None:
            queue.task_done()
            break
        image_path = item.image_path
        page_id = item.page_id
        try:
            text, usage_info = await loop.run_in_executor(
                None,
                lambda p=image_path: transcribe_page(
                    client, p,
                    prompt_text=prompt_text,
                    system_prompt=system_prompt,
                    model=model,
                ),
            )
            record = {
                "source": source,
                "book_title": book_title,
                "page_id": page_id,
                "page_seq": item.page_seq,
                "canvas_id": item.canvas_id,
                "text": text,
                "model": model,
                "timestamp": _utc_now(),
            }
            async with lock:
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                stats["total_cost"] = stats.get("total_cost", 0.0) + (usage_info.get("cost_usd") or 0.0)
            stats["done"] += 1
            cost_str = f" ${usage_info['cost_usd']:.4f}" if usage_info.get("cost_usd") else ""
            tokens_str = f" {usage_info.get('prompt_tokens', '?')}+{usage_info.get('output_tokens', '?')} tok" if usage_info else ""
            print(f"  [{stats['done']}/{stats['total']}] {page_id} ({len(text)} chars){tokens_str}{cost_str}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  [ERROR] {page_id}: {exc}")
        finally:
            queue.task_done()


async def run_transcription(
    image_dir: Path,
    *,
    output_path: Path,
    source: str = "",
    book_title: str = "",
    model: str = DEFAULT_MODEL_TRANSCRIPTION,
    prompt_name: str = DEFAULT_PROMPT_NAME,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    workers: int = DEFAULT_WORKERS,
    skip_existing: bool = False,
) -> Path:
    """Transcribe all images in a directory to a JSONL file."""
    image_dir = image_dir.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_refs = _resolve_pages(image_dir)
    if not page_refs:
        raise FileNotFoundError(f"No images found in {image_dir}")

    if skip_existing:
        existing = _load_existing_page_ids(output_path)
        page_refs = [r for r in page_refs if r.page_id not in existing]
        if not page_refs:
            print("All pages already transcribed.")
            return output_path

    prompt_text = load_prompt(prompt_name)
    if not source:
        source = image_dir.parent.name
    if not book_title:
        book_title = image_dir.parent.name

    client = genai.Client()
    queue: asyncio.Queue = asyncio.Queue()
    lock = asyncio.Lock()
    stats = {"done": 0, "errors": 0, "total": len(page_refs)}

    print(f"Transcribing {len(page_refs)} pages with {workers} workers ({model})")
    print(f"Output: {output_path}")

    for ref in page_refs:
        await queue.put(ref)
    for _ in range(workers):
        await queue.put(None)

    worker_tasks = [
        asyncio.create_task(
            _transcribe_worker(
                queue, client,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                model=model,
                source=source,
                book_title=book_title,
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


def run_transcription_sync(
    image_dir: Path,
    *,
    output_path: Path,
    source: str = "",
    book_title: str = "",
    model: str = DEFAULT_MODEL_TRANSCRIPTION,
    prompt_name: str = DEFAULT_PROMPT_NAME,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    workers: int = DEFAULT_WORKERS,
    skip_existing: bool = False,
) -> Path:
    """Sync wrapper around run_transcription."""
    return asyncio.run(
        run_transcription(
            image_dir,
            output_path=output_path,
            source=source,
            book_title=book_title,
            model=model,
            prompt_name=prompt_name,
            system_prompt=system_prompt,
            workers=workers,
            skip_existing=skip_existing,
        )
    )
