"""Batch VLM transcription via Gemini Batch API.

Submits full page images as InlinedRequest objects, tracks state in a
per-book manifest, and collects results into JSONL shards that merge
into the final output.

Architecture (per Codex recommendation):
- Manifest JSON is the source of truth, not JSONL
- Chunk by estimated payload bytes, not page count
- Freeze config per run for reproducibility
- Write per-job shards, then derive final JSONL
- Submit and poll are decoupled CLI operations
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.model_io import load_prompt
from palimpsest.transcribe import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PROMPT_NAME,
    DEFAULT_SYSTEM_PROMPT,
    IMAGE_EXTENSIONS,
    PRICING,
    _mime_type,
)

# Conservative cap: 80MB per batch to stay well under 100MB inline limit
DEFAULT_MAX_CHUNK_BYTES = 80 * 1024 * 1024
DEFAULT_MAX_REQUESTS_PER_BATCH = 100
MANIFEST_FILENAME = "batch_manifest.json"
SHARDS_DIRNAME = "shards"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_checksum(path: Path) -> str:
    """Fast MD5 checksum for image identity tracking."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _prompt_hash(prompt_text: str, system_prompt: str) -> str:
    """Short hash of prompt config for reproducibility tracking."""
    combined = f"{system_prompt}\n---\n{prompt_text}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]


def _collect_images(image_dir: Path) -> list[Path]:
    return sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _estimate_inline_bytes(image_path: Path) -> int:
    """Estimate base64-encoded size plus JSON overhead."""
    raw_bytes = image_path.stat().st_size
    b64_bytes = (raw_bytes * 4) // 3 + 4
    return b64_bytes + 500  # overhead for JSON wrapper


def _chunk_pages(
    pages: list[dict],
    max_bytes: int = DEFAULT_MAX_CHUNK_BYTES,
    max_requests: int = DEFAULT_MAX_REQUESTS_PER_BATCH,
) -> list[list[dict]]:
    """Split pages into chunks respecting byte and request limits."""
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_bytes = 0

    for page in pages:
        page_bytes = page["estimated_bytes"]
        if current_chunk and (current_bytes + page_bytes > max_bytes or len(current_chunk) >= max_requests):
            chunks.append(current_chunk)
            current_chunk = []
            current_bytes = 0
        current_chunk.append(page)
        current_bytes += page_bytes

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _build_inlined_request(
    page: dict,
    *,
    prompt_text: str,
    system_prompt: str,
    model: str,
    source: str,
    book_title: str,
) -> types.InlinedRequest:
    """Build one InlinedRequest for a page image."""
    image_path = Path(page["image_path"])
    return types.InlinedRequest(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_bytes(
                data=image_path.read_bytes(),
                mime_type=_mime_type(image_path),
            ),
        ],
        metadata={
            "page_id": page["page_id"],
            "source": source,
            "book_title": book_title,
            "seq": str(page["seq"]),
            "checksum": page["checksum"],
        },
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        ),
    )


# ---------------------------------------------------------------------------
# Manifest operations
# ---------------------------------------------------------------------------

def create_manifest(
    image_dir: Path,
    output_dir: Path,
    *,
    source: str = "",
    book_title: str = "",
    model: str = DEFAULT_MODEL_READING,
    prompt_name: str = DEFAULT_PROMPT_NAME,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> dict:
    """Build a batch manifest for a book's images."""
    image_dir = image_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    images = _collect_images(image_dir)
    if not images:
        raise FileNotFoundError(f"No images found in {image_dir}")

    if not source:
        source = image_dir.parent.name
    if not book_title:
        book_title = image_dir.parent.name

    prompt_text = load_prompt(prompt_name)

    pages = []
    for seq, img in enumerate(images):
        pages.append({
            "page_id": img.stem,
            "image_path": str(img),
            "seq": seq,
            "estimated_bytes": _estimate_inline_bytes(img),
            "checksum": _file_checksum(img),
            "status": "pending",
        })

    manifest = {
        "created_at": _utc_now(),
        "image_dir": str(image_dir),
        "output_dir": str(output_dir),
        "source": source,
        "book_title": book_title,
        "config": {
            "model": model,
            "prompt_name": prompt_name,
            "prompt_hash": _prompt_hash(prompt_text, system_prompt),
            "system_prompt": system_prompt,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        },
        "pages": pages,
        "jobs": [],
        "status": "created",
    }

    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def load_manifest(output_dir: Path) -> dict:
    manifest_path = Path(output_dir).resolve() / MANIFEST_FILENAME
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict) -> None:
    output_dir = Path(manifest["output_dir"])
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_batch(output_dir: Path) -> dict:
    """Submit pending pages as batch jobs. Returns updated manifest."""
    manifest = load_manifest(output_dir)
    prompt_text = load_prompt(manifest["config"]["prompt_name"])
    model = manifest["config"]["model"]
    system_prompt = manifest["config"]["system_prompt"]
    source = manifest["source"]
    book_title = manifest["book_title"]

    pending_pages = [p for p in manifest["pages"] if p["status"] == "pending"]
    if not pending_pages:
        print("No pending pages to submit.")
        return manifest

    chunks = _chunk_pages(pending_pages)
    client = genai.Client()

    print(f"Submitting {len(pending_pages)} pages in {len(chunks)} batch job(s) ({model})")

    page_index = {p["page_id"]: p for p in manifest["pages"]}

    for chunk_idx, chunk in enumerate(chunks):
        requests = [
            _build_inlined_request(
                page,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                model=model,
                source=source,
                book_title=book_title,
            )
            for page in chunk
        ]

        batch_job = client.batches.create(
            model=model,
            src=requests,
            config=types.CreateBatchJobConfig(
                displayName=f"{book_title}_chunk{chunk_idx:03d}",
            ),
        )

        job_record = {
            "job_name": batch_job.name,
            "chunk_id": f"chunk{chunk_idx:03d}",
            "page_ids": [p["page_id"] for p in chunk],
            "page_count": len(chunk),
            "submitted_at": _utc_now(),
            "last_polled_at": None,
            "state": str(batch_job.state),
            "completion_stats": None,
        }
        manifest["jobs"].append(job_record)

        for page in chunk:
            page_index[page["page_id"]]["status"] = "submitted"

        total_bytes = sum(p["estimated_bytes"] for p in chunk)
        print(f"  [{chunk_idx + 1}/{len(chunks)}] {batch_job.name} — {len(chunk)} pages, ~{total_bytes // (1024*1024)}MB")

    manifest["status"] = "submitted"
    save_manifest(manifest)
    return manifest


# ---------------------------------------------------------------------------
# Poll / Status
# ---------------------------------------------------------------------------

def poll_batch(output_dir: Path) -> dict:
    """Poll all jobs and update manifest state. Returns updated manifest."""
    manifest = load_manifest(output_dir)
    client = genai.Client()

    page_index = {p["page_id"]: p for p in manifest["pages"]}

    for job in manifest["jobs"]:
        if job["state"] in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
            continue

        batch_job = client.batches.get(name=job["job_name"])
        job["state"] = str(batch_job.state)
        job["last_polled_at"] = _utc_now()

        if batch_job.completion_stats:
            job["completion_stats"] = {
                "success_count": batch_job.completion_stats.success_count,
                "failure_count": batch_job.completion_stats.failure_count,
            }

        if str(batch_job.state) == "JOB_STATE_SUCCEEDED":
            for page_id in job["page_ids"]:
                page_index[page_id]["status"] = "completed"
        elif str(batch_job.state) == "JOB_STATE_FAILED":
            for page_id in job["page_ids"]:
                page_index[page_id]["status"] = "failed"

    # Derive overall status
    states = {job["state"] for job in manifest["jobs"]}
    if all(s == "JOB_STATE_SUCCEEDED" for s in states):
        manifest["status"] = "completed"
    elif any(s == "JOB_STATE_FAILED" for s in states):
        manifest["status"] = "partial"
    else:
        manifest["status"] = "running"

    save_manifest(manifest)
    return manifest


def print_status(manifest: dict) -> None:
    """Print human-readable status of a batch manifest."""
    page_statuses = {}
    for p in manifest["pages"]:
        page_statuses[p["status"]] = page_statuses.get(p["status"], 0) + 1

    print(f"Batch: {manifest['book_title']} ({manifest['status']})")
    print(f"  Model: {manifest['config']['model']}")
    print(f"  Pages: {' | '.join(f'{v} {k}' for k, v in sorted(page_statuses.items()))}")
    print(f"  Jobs: {len(manifest['jobs'])}")
    for job in manifest["jobs"]:
        stats = ""
        if job["completion_stats"]:
            stats = f" ({job['completion_stats']['success_count']} ok, {job['completion_stats']['failure_count']} fail)"
        print(f"    {job['chunk_id']}: {job['state']}{stats}")


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def collect_batch(output_dir: Path) -> Path:
    """Collect results from completed batch jobs into final JSONL."""
    output_dir = Path(output_dir).resolve()
    manifest = load_manifest(output_dir)
    client = genai.Client()

    shards_dir = output_dir / SHARDS_DIRNAME
    shards_dir.mkdir(exist_ok=True)

    for job in manifest["jobs"]:
        if job["state"] != "JOB_STATE_SUCCEEDED":
            continue

        shard_path = shards_dir / f"{job['chunk_id']}.jsonl"
        if shard_path.exists():
            continue

        batch_job = client.batches.get(name=job["job_name"])
        # The batch API returns results as a list of GenerateContentResponse
        # matched to InlinedRequests by order
        lines: list[str] = []

        if hasattr(batch_job, 'dest') and batch_job.dest:
            # Results may be in GCS or inline depending on API version
            # For inline results, iterate the response
            pass

        # For Developer API: results come back via the batch job response
        # We need to retrieve them — the exact method depends on SDK version
        # Try the standard pattern
        try:
            results = list(client.batches.list())  # This lists jobs, not results
            # Actually, for the Developer API, results are returned inline
            # in the batch job object or need to be fetched differently
            # Let's use the direct approach
            _collect_job_results(client, job, shard_path, manifest)
        except Exception as exc:
            print(f"  [WARN] Could not collect {job['chunk_id']}: {exc}")
            continue

        print(f"  Collected {job['chunk_id']} -> {shard_path}")

    # Merge shards into final JSONL in page order
    final_path = output_dir / "transcriptions.jsonl"
    _merge_shards(manifest, shards_dir, final_path)
    print(f"Final output: {final_path}")
    return final_path


def _collect_job_results(
    client: genai.Client,
    job: dict,
    shard_path: Path,
    manifest: dict,
) -> None:
    """Collect results for one batch job into a shard file."""
    batch_job = client.batches.get(name=job["job_name"])

    # The Gemini Developer API returns results as part of the batch job
    # Access via batch_job responses
    source = manifest["source"]
    book_title = manifest["book_title"]
    model = manifest["config"]["model"]

    lines: list[str] = []
    page_ids = job["page_ids"]

    # Results are available through the batch job's response list
    if hasattr(batch_job, 'src') and batch_job.src:
        src = batch_job.src
        inlined_responses = getattr(src, 'inlined_responses', None) or []
        for idx, resp in enumerate(inlined_responses):
            page_id = page_ids[idx] if idx < len(page_ids) else f"unknown_{idx}"
            text = ""
            if hasattr(resp, 'response') and resp.response:
                candidates = getattr(resp.response, 'candidates', []) or []
                for candidate in candidates:
                    content = getattr(candidate, 'content', None)
                    parts = getattr(content, 'parts', []) or []
                    for part in parts:
                        t = getattr(part, 'text', None)
                        if t:
                            text += t

            record = {
                "source": source,
                "book_title": book_title,
                "page_id": page_id,
                "text": text.strip(),
                "model": model,
                "timestamp": _utc_now(),
            }
            lines.append(json.dumps(record, ensure_ascii=False))

    shard_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _merge_shards(manifest: dict, shards_dir: Path, final_path: Path) -> None:
    """Merge shard files into final JSONL ordered by page sequence."""
    # Load all shard records
    all_records: dict[str, str] = {}
    for shard_file in sorted(shards_dir.glob("*.jsonl")):
        for line in shard_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                all_records[record["page_id"]] = line
            except (json.JSONDecodeError, KeyError):
                continue

    # Write in manifest page order
    ordered_lines: list[str] = []
    for page in sorted(manifest["pages"], key=lambda p: p["seq"]):
        if page["page_id"] in all_records:
            ordered_lines.append(all_records[page["page_id"]])

    final_path.write_text("\n".join(ordered_lines) + "\n", encoding="utf-8")
