"""Async IIIF manifest fetcher for parallel downloads.

This module provides efficient parallel manifest fetching with caching
to quickly enrich manuscript records with metadata.

Usage:
    import asyncio
    from palimpsest.discovery.sources.iiif_batch import fetch_manifests_async

    results = asyncio.run(fetch_manifests_async(
        manifest_urls=["https://...manifest.json", ...],
        manifest_dir=Path("discovery/manifests"),
        max_concurrent=50,
    ))
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import requests

from palimpsest.discovery.iiif import extract_metadata


@dataclass
class ManifestResult:
    """Result of fetching a single manifest."""

    manuscript_id: str
    manifest_url: str
    success: bool
    cached: bool
    manifest_path: Optional[Path] = None
    metadata: Optional[dict] = None
    canvas_count: Optional[int] = None
    error: Optional[str] = None


def _extract_manuscript_id(manifest_url: str) -> str:
    """Extract manuscript_id from manifest URL."""
    # https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
    import re

    match = re.search(r"MSS_([^/]+)", manifest_url)
    if match:
        raw = match.group(1)
        # Pal.lat.1267 -> vat_pal_lat_1267
        normalized = raw.lower().replace(".", "_")
        return f"vat_{normalized}"
    return manifest_url.split("/")[-2]


def _count_canvases(manifest: dict) -> int:
    """Count canvases in manifest (v2 or v3)."""
    # IIIF v3
    if "items" in manifest:
        return len([i for i in manifest.get("items", []) if i.get("type") == "Canvas"])

    # IIIF v2
    count = 0
    for seq in manifest.get("sequences", []):
        count += len(seq.get("canvases", []))
    return count


async def _fetch_one_async(
    session: aiohttp.ClientSession,
    manuscript_id: str,
    manifest_url: str,
    manifest_dir: Path,
    skip_existing: bool,
) -> ManifestResult:
    """Fetch a single manifest asynchronously."""
    cache_path = manifest_dir / f"{manuscript_id}.json"

    # Check cache
    if skip_existing and cache_path.exists():
        try:
            manifest = json.loads(cache_path.read_text(encoding="utf-8"))
            return ManifestResult(
                manuscript_id=manuscript_id,
                manifest_url=manifest_url,
                success=True,
                cached=True,
                manifest_path=cache_path,
                metadata=extract_metadata(manifest),
                canvas_count=_count_canvases(manifest),
            )
        except Exception as exc:
            pass  # Re-fetch if cache is corrupt

    # HEAD request first to check existence
    try:
        async with session.head(manifest_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return ManifestResult(
                    manuscript_id=manuscript_id,
                    manifest_url=manifest_url,
                    success=False,
                    cached=False,
                    error="404 Not Found",
                )
    except Exception:
        pass  # Proceed to GET anyway

    # Fetch manifest
    try:
        async with session.get(manifest_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 404:
                return ManifestResult(
                    manuscript_id=manuscript_id,
                    manifest_url=manifest_url,
                    success=False,
                    cached=False,
                    error="404 Not Found",
                )

            resp.raise_for_status()
            manifest = await resp.json()

            # Cache to disk
            manifest_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            return ManifestResult(
                manuscript_id=manuscript_id,
                manifest_url=manifest_url,
                success=True,
                cached=False,
                manifest_path=cache_path,
                metadata=extract_metadata(manifest),
                canvas_count=_count_canvases(manifest),
            )

    except Exception as exc:
        return ManifestResult(
            manuscript_id=manuscript_id,
            manifest_url=manifest_url,
            success=False,
            cached=False,
            error=str(exc),
        )


async def fetch_manifests_async(
    manifest_urls: list[str],
    manifest_dir: Path,
    max_concurrent: int = 50,
    skip_existing: bool = True,
) -> list[ManifestResult]:
    """Fetch multiple manifests in parallel using aiohttp.

    Args:
        manifest_urls: List of manifest URLs to fetch
        manifest_dir: Directory to cache manifests
        max_concurrent: Maximum concurrent requests
        skip_existing: Skip if cached file exists

    Returns:
        List of ManifestResult objects
    """
    if not HAS_AIOHTTP:
        raise ImportError("aiohttp is required for async fetching: pip install aiohttp")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_with_semaphore(session, mid, url):
        async with semaphore:
            return await _fetch_one_async(session, mid, url, manifest_dir, skip_existing)

    connector = aiohttp.TCPConnector(limit=max_concurrent, limit_per_host=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for url in manifest_urls:
            mid = _extract_manuscript_id(url)
            tasks.append(_fetch_with_semaphore(session, mid, url))

        results = await asyncio.gather(*tasks)

    return results


def fetch_manifests_sync(
    manifest_urls: list[str],
    manifest_dir: Path,
    skip_existing: bool = True,
    delay: float = 0.25,
    verbose: bool = True,
) -> list[ManifestResult]:
    """Fetch manifests synchronously (fallback when aiohttp not available).

    Args:
        manifest_urls: List of manifest URLs to fetch
        manifest_dir: Directory to cache manifests
        skip_existing: Skip if cached file exists
        delay: Delay between requests
        verbose: Print progress

    Returns:
        List of ManifestResult objects
    """
    import time

    results: list[ManifestResult] = []
    manifest_dir.mkdir(parents=True, exist_ok=True)

    for i, manifest_url in enumerate(manifest_urls):
        manuscript_id = _extract_manuscript_id(manifest_url)
        cache_path = manifest_dir / f"{manuscript_id}.json"

        if verbose and (i + 1) % 10 == 0:
            print(f"  [{i + 1}/{len(manifest_urls)}]")

        # Check cache
        if skip_existing and cache_path.exists():
            try:
                manifest = json.loads(cache_path.read_text(encoding="utf-8"))
                results.append(
                    ManifestResult(
                        manuscript_id=manuscript_id,
                        manifest_url=manifest_url,
                        success=True,
                        cached=True,
                        manifest_path=cache_path,
                        metadata=extract_metadata(manifest),
                        canvas_count=_count_canvases(manifest),
                    )
                )
                continue
            except Exception:
                pass

        # Fetch
        try:
            resp = requests.get(manifest_url, timeout=30)
            if resp.status_code == 404:
                results.append(
                    ManifestResult(
                        manuscript_id=manuscript_id,
                        manifest_url=manifest_url,
                        success=False,
                        cached=False,
                        error="404 Not Found",
                    )
                )
                continue

            resp.raise_for_status()
            manifest = resp.json()

            cache_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            results.append(
                ManifestResult(
                    manuscript_id=manuscript_id,
                    manifest_url=manifest_url,
                    success=True,
                    cached=False,
                    manifest_path=cache_path,
                    metadata=extract_metadata(manifest),
                    canvas_count=_count_canvases(manifest),
                )
            )

        except Exception as exc:
            results.append(
                ManifestResult(
                    manuscript_id=manuscript_id,
                    manifest_url=manifest_url,
                    success=False,
                    cached=False,
                    error=str(exc),
                )
            )

        if delay:
            time.sleep(delay)

    return results


def enrich_records(
    records: list[dict],
    manifest_dir: Path,
    use_async: bool = True,
    max_concurrent: int = 50,
    skip_existing: bool = True,
    verbose: bool = True,
) -> list[dict]:
    """Enrich records with manifest metadata.

    Args:
        records: List of discovery records (must have iiif.manifest_url)
        manifest_dir: Directory to cache manifests
        use_async: Use async fetching if aiohttp available
        max_concurrent: Max concurrent requests (async only)
        skip_existing: Skip if manifest already cached
        verbose: Print progress

    Returns:
        Enriched records
    """
    # Build URL -> record mapping
    by_url: dict[str, list[dict]] = {}
    for rec in records:
        url = (rec.get("iiif") or {}).get("manifest_url")
        if url:
            if url not in by_url:
                by_url[url] = []
            by_url[url].append(rec)

    urls = list(by_url.keys())
    if not urls:
        return records

    if verbose:
        print(f"Fetching {len(urls)} manifests...")

    # Fetch manifests
    if use_async and HAS_AIOHTTP:
        results = asyncio.run(
            fetch_manifests_async(
                manifest_urls=urls,
                manifest_dir=manifest_dir,
                max_concurrent=max_concurrent,
                skip_existing=skip_existing,
            )
        )
    else:
        results = fetch_manifests_sync(
            manifest_urls=urls,
            manifest_dir=manifest_dir,
            skip_existing=skip_existing,
            verbose=verbose,
        )

    # Update records
    result_by_url = {r.manifest_url: r for r in results}
    enriched = 0
    for rec in records:
        url = (rec.get("iiif") or {}).get("manifest_url")
        if not url or url not in result_by_url:
            continue

        result = result_by_url[url]
        if not result.success:
            continue

        # Update record
        iiif = rec.setdefault("iiif", {})
        iiif["manifest_path"] = str(result.manifest_path) if result.manifest_path else None
        iiif["canvas_count"] = result.canvas_count

        if result.metadata:
            content = rec.setdefault("content", {})
            for key, value in result.metadata.items():
                if value and not content.get(key):
                    content[key] = value

        enriched += 1

    if verbose:
        cached = sum(1 for r in results if r.cached)
        fetched = sum(1 for r in results if r.success and not r.cached)
        failed = sum(1 for r in results if not r.success)
        print(f"  Cached: {cached}, Fetched: {fetched}, Failed: {failed}")
        print(f"  Enriched: {enriched} records")

    return records
