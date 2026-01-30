from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests

from .io import atomic_write_json, read_json
from .metadata import update_metadata
from .registry import now_iso


def _timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_file(url: str, dest: Path, timeout: float) -> tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                size += len(chunk)
                h.update(chunk)
    return size, h.hexdigest()


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True))
        handle.write("\n")


def download_pages(
    *,
    doc_dir: Path,
    overwrite: bool = False,
    delay: float = 0.5,
    timeout: float = 30.0,
) -> dict:
    page_list = read_json(doc_dir / "page_list.json")
    metadata = read_json(doc_dir / "metadata.json")
    images_dir = doc_dir / "images"
    runs_dir = doc_dir / "runs"
    log_path = runs_dir / "download.jsonl"

    pages = page_list.get("pages", [])
    total = len(pages)
    downloaded = 0
    skipped = 0
    failed = 0
    bytes_total = 0

    for page in pages:
        page_id = page.get("page_id", "")
        url = page.get("url", "")
        filename = page.get("filename") or f"{page_id}.jpg"
        out_path = images_dir / filename

        if not url:
            failed += 1
            _append_jsonl(
                log_path,
                {
                    "timestamp": _timestamp(),
                    "page_id": page_id,
                    "filename": filename,
                    "url": url,
                    "status": "failed",
                    "error": "missing_url",
                },
            )
            continue

        if out_path.exists() and not overwrite:
            size = out_path.stat().st_size
            sha = _hash_file(out_path)
            skipped += 1
            _append_jsonl(
                log_path,
                {
                    "timestamp": _timestamp(),
                    "page_id": page_id,
                    "filename": filename,
                    "url": url,
                    "status": "skipped",
                    "size_bytes": size,
                    "sha256": sha,
                },
            )
            continue

        try:
            size, sha = _download_file(url, out_path, timeout=timeout)
            bytes_total += size
            downloaded += 1
            _append_jsonl(
                log_path,
                {
                    "timestamp": _timestamp(),
                    "page_id": page_id,
                    "filename": filename,
                    "url": url,
                    "status": "downloaded",
                    "size_bytes": size,
                    "sha256": sha,
                },
            )
        except Exception as exc:
            failed += 1
            _append_jsonl(
                log_path,
                {
                    "timestamp": _timestamp(),
                    "page_id": page_id,
                    "filename": filename,
                    "url": url,
                    "status": "failed",
                    "error": str(exc),
                },
            )
        if delay:
            import time

            time.sleep(delay)

    status = "downloaded" if failed == 0 else "downloaded_partial"
    update_metadata(
        doc_dir,
        {
            "status": status,
            "downloaded_count": downloaded,
            "skipped_count": skipped,
            "page_count": total,
        },
    )

    summary = {
        "doc_id": metadata.get("doc_id", ""),
        "total": total,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "bytes_total": bytes_total,
        "completed_at": _timestamp(),
    }
    atomic_write_json(runs_dir / "download_summary.json", summary)
    return summary
