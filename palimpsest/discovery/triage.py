"""Gemini-based manuscript triage for discovery pipeline.

This module provides automated scoring of manuscript opportunities using
Gemini Flash for fast, cheap metadata analysis. It follows the current
discovery workflow conventions rather than the deleted legacy lane.

Usage:
    from palimpsest.discovery.triage import triage_batch, triage_manuscript
    from palimpsest.discovery.access import DiscoveryStore

    db = DiscoveryStore.open("discovery/manuscripts.db")
    results = triage_batch(
        registry_path=Path("discovery/registry/master.jsonl"),
        db=db,
        workers=10,
    )
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from google import genai
from google.genai import types

from palimpsest.config import DEFAULT_MODEL_TRIAGE
from palimpsest.model_io import strip_json_fences

from .access import DiscoveryWriteAccess, Enrichment, Manuscript

BACKOFF_BASE = 2
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass
class TriageResult:
    """Result of triaging a single manuscript."""

    manuscript_id: str
    interesting: bool
    interest_score: int
    reasons: list[str]
    recommendation: str
    raw_json: str
    model: str
    triaged_at: str
    error: Optional[str] = None

    # New fields for enhanced triage
    content_type: Optional[str] = None  # original, copy_common, copy_uncommon, uncertain
    common_text_detected: Optional[str] = None
    likely_content: Optional[str] = None
    rarity_score: Optional[int] = None
    unstudied_score: Optional[int] = None
    source_basis: Optional[str] = None
    content_confidence: Optional[float] = None
    content_warning: Optional[str] = None

    # Web search results
    google_scholar_hits: Optional[int] = None
    google_web_hits: Optional[int] = None
    has_transcription: Optional[bool] = None
    has_edition: Optional[bool] = None
    attention_summary: Optional[str] = None


@dataclass(frozen=True)
class ThumbnailTriageTask:
    manuscript_id: str
    shelfmark: str
    manifest_url: Optional[str]
    metadata: dict
    initial_interest: bool
    initial_score: int


def load_triage_prompt(with_thumbnail: bool = False) -> str:
    """Load triage prompt from prompts directory."""
    if with_thumbnail:
        prompt_path = PROMPTS_DIR / "opportunity_triage_thumbnail.txt"
    else:
        prompt_path = PROMPTS_DIR / "opportunity_triage.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_triage_config(with_web_search: bool = True) -> types.GenerateContentConfig:
    """Build Gemini config for triage with optional web search."""
    config_kwargs = {"temperature": 0.2}

    if with_web_search:
        # Enable Google Search grounding for live web lookups
        config_kwargs["tools"] = [
            types.Tool(google_search=types.GoogleSearch()),
        ]

    return types.GenerateContentConfig(**config_kwargs)


def _attempt_triage(
    client: genai.Client,
    prompt: str,
    metadata_json: str,
    model: str,
    thumbnail_bytes: Optional[bytes] = None,
    max_attempts: int = 3,
    with_web_search: bool = True,
) -> str:
    """Attempt triage with retry - mirrors _attempt_pass() pattern from runner.py.

    Args:
        client: Gemini client
        prompt: System prompt for triage
        metadata_json: JSON string with manuscript metadata
        model: Model to use
        thumbnail_bytes: Optional thumbnail image
        max_attempts: Max retry attempts
        with_web_search: Enable Google Search grounding

    Returns:
        Raw JSON response string
    """
    for attempt in range(1, max_attempts + 1):
        try:
            contents: list = [prompt, f"\n\n## Manuscript to Evaluate\n\n```json\n{metadata_json}\n```"]
            if thumbnail_bytes:
                contents.append(
                    types.Part.from_bytes(data=thumbnail_bytes, mime_type="image/jpeg")
                )

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=_build_triage_config(with_web_search=with_web_search),
            )

            # Extract text from response (handles grounded responses)
            text = ""
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        text += part.text

            return strip_json_fences(text)
        except Exception as exc:
            if attempt == max_attempts:
                raise
            time.sleep(BACKOFF_BASE**attempt)
    raise RuntimeError("Unreachable triage loop")


def _parse_triage_response(
    manuscript_id: str,
    raw_text: str,
    model: str,
) -> TriageResult:
    """Parse triage response JSON into TriageResult.

    Handles both old format (simple) and new format (with web search results).
    """
    triaged_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        # Try to extract JSON from markdown code block
        import re
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return TriageResult(
                    manuscript_id=manuscript_id,
                    interesting=False,
                    interest_score=0,
                    reasons=[],
                    recommendation="error",
                    raw_json=raw_text,
                    model=model,
                    triaged_at=triaged_at,
                    error=f"JSON parse error: {exc}",
                )
        else:
            return TriageResult(
                manuscript_id=manuscript_id,
                interesting=False,
                interest_score=0,
                reasons=[],
                recommendation="error",
                raw_json=raw_text,
                model=model,
                triaged_at=triaged_at,
                error=f"JSON parse error: {exc}",
            )

    # Parse attention_received if present (new format)
    attention = data.get("attention_received", {})

    # Determine "interesting" from recommendation or explicit field
    recommendation = data.get("recommendation", "skip")
    interesting = data.get("interesting", recommendation == "queue")

    # Calculate combined interest score if we have new format
    interest_score = data.get("interest_score", 0)
    rarity_score = data.get("rarity_score")
    unstudied_score = data.get("unstudied_score")

    return TriageResult(
        manuscript_id=manuscript_id,
        interesting=interesting,
        interest_score=interest_score,
        reasons=data.get("reasons", []),
        recommendation=recommendation,
        raw_json=raw_text,
        model=model,
        triaged_at=triaged_at,
        # New fields
        content_type=data.get("content_type"),
        common_text_detected=data.get("common_text_if_detected"),
        likely_content=data.get("likely_content"),
        rarity_score=rarity_score,
        unstudied_score=unstudied_score,
        source_basis=data.get("source_basis"),
        content_confidence=data.get("content_confidence"),
        content_warning=data.get("content_warning"),
        google_scholar_hits=attention.get("google_scholar_hits"),
        google_web_hits=attention.get("google_web_hits"),
        has_transcription=attention.get("has_transcription"),
        has_edition=attention.get("has_edition"),
        attention_summary=attention.get("attention_summary"),
    )


def triage_manuscript(
    client: genai.Client,
    metadata: dict,
    prompt: str,
    model: str = DEFAULT_MODEL_TRIAGE,
    thumbnail_bytes: Optional[bytes] = None,
    max_attempts: int = 3,
    with_web_search: bool = True,
) -> TriageResult:
    """Triage single manuscript - mirrors transcribe_page() pattern.

    Args:
        client: Gemini client
        metadata: Dict with manuscript metadata
        prompt: Triage prompt
        model: Model to use
        thumbnail_bytes: Optional thumbnail image
        max_attempts: Max retry attempts
        with_web_search: Enable Google Search for live lookups

    Returns:
        TriageResult with scores and recommendation
    """
    manuscript_id = metadata.get("manuscript_id") or metadata.get("id", "unknown")
    metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2)

    try:
        raw_text = _attempt_triage(
            client=client,
            prompt=prompt,
            metadata_json=metadata_json,
            model=model,
            thumbnail_bytes=thumbnail_bytes,
            max_attempts=max_attempts,
            with_web_search=with_web_search,
        )
        return _parse_triage_response(manuscript_id, raw_text, model)
    except Exception as exc:
        triaged_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        return TriageResult(
            manuscript_id=manuscript_id,
            interesting=False,
            interest_score=0,
            reasons=[],
            recommendation="error",
            raw_json="",
            model=model,
            triaged_at=triaged_at,
            error=str(exc),
        )


def fetch_thumbnail(manifest_url: str) -> Optional[bytes]:
    """Fetch cover thumbnail from Vatican's standard location."""
    # Vatican format: https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
    # Cover is at:    https://digi.vatlib.it/pub/digit/MSS_Pal.lat.1267/cover/cover.jpg
    try:
        if "digi.vatlib.it" in manifest_url:
            # Extract MSS_* identifier
            parts = manifest_url.split("/")
            for part in parts:
                if part.startswith("MSS_"):
                    cover_url = f"https://digi.vatlib.it/pub/digit/{part}/cover/cover.jpg"
                    resp = requests.get(cover_url, timeout=30)
                    if resp.ok:
                        return resp.content
        return None
    except Exception:
        return None


def combined_interest_score(result: TriageResult) -> int:
    scores = [
        score
        for score in (result.interest_score, result.rarity_score, result.unstudied_score)
        if score is not None
    ]
    if not scores:
        return 0
    return round(sum(scores) / len(scores))


def resolve_triage_method(
    *,
    with_thumbnail: bool = False,
    with_web_search: bool = True,
) -> str:
    if with_thumbnail and with_web_search:
        return "gemini_web_thumbnail"
    if with_thumbnail:
        return "gemini_flash_thumbnail"
    if with_web_search:
        return "gemini_web_search"
    return "gemini_flash"


def save_triage_result(
    db: DiscoveryWriteAccess,
    result: TriageResult,
    with_thumbnail: bool = False,
    with_web_search: bool = True,
) -> None:
    """Save triage result to database."""
    triage_method = resolve_triage_method(
        with_thumbnail=with_thumbnail,
        with_web_search=with_web_search,
    )
    combined_score = combined_interest_score(result)

    interest_reason = build_interest_reason(result)

    # Update opportunity record
    updates = {
        "initial_interest": result.interesting,
        "initial_score": result.interest_score,
        "interest_score": combined_score,
        "interest_reason": interest_reason,
        "triage_method": triage_method,
        "triage_model": result.model,
        "triage_at": result.triaged_at,
        "triage_json": result.raw_json,
        "status": "triaged" if not result.error else "triage_error",
    }

    db.ensure_opportunity(result.manuscript_id)
    db.update_opportunity(result.manuscript_id, updates)

    # Also update manuscript score
    db.update_manuscript(
        result.manuscript_id,
        {"interest_score": combined_score},
        agent=triage_method,
    )

    # Save web search results to enrichment table if present
    if result.google_scholar_hits is not None or result.google_web_hits is not None:
        enrichment = db.get_enrichment(result.manuscript_id)
        if not enrichment:
            enrichment = Enrichment(manuscript_id=result.manuscript_id)

        enrichment.google_scholar_hits = result.google_scholar_hits
        enrichment.google_search_hits = result.google_web_hits
        enrichment.transcription_exists = result.has_transcription
        enrichment.common_text_detected = result.common_text_detected

        db.upsert_enrichment(enrichment)


def build_interest_reason(result: TriageResult) -> Optional[str]:
    """Build a readable triage summary without turning weak guesses into facts."""
    content_lede = result.likely_content
    if content_lede and (
        result.source_basis not in (None, "source_catalog")
        or (result.content_confidence is not None and result.content_confidence < 0.8)
    ):
        basis = result.source_basis or "unclear_basis"
        if result.content_confidence is None:
            content_lede = f"Hypothesis ({basis}): {content_lede}"
        else:
            content_lede = f"Hypothesis ({basis}, conf {result.content_confidence:.2f}): {content_lede}"
        if result.content_warning:
            content_lede = f"{content_lede}. {result.content_warning}"

    if result.likely_content and result.attention_summary:
        return f"{content_lede}. {result.attention_summary}"
    if content_lede:
        return content_lede
    if result.reasons:
        return "; ".join(result.reasons)
    return None


def build_triage_metadata(ms: Manuscript) -> dict:
    """Build the metadata packet sent to the triage model."""
    source_title = ms.title if ms.title else None
    if ms.source_catalog:
        source_catalog = ms.source_catalog
    else:
        title_is_shelfmark = not source_title or source_title.strip().lower() == ms.shelfmark.strip().lower()
        source_catalog = {
            "label": source_title or ms.shelfmark,
            "title": source_title,
            "title_is_shelfmark": title_is_shelfmark,
            "shelfmark": ms.shelfmark,
            "date": ms.date_range,
            "language": ms.languages[0] if ms.languages else None,
            "description": ms.description,
        }

    return {
        "manuscript_id": ms.id,
        "shelfmark": ms.shelfmark,
        "repository": ms.repository,
        "collection": ms.collection,
        "title": ms.title,
        "date": ms.date_range,
        "language": ms.languages[0] if ms.languages else None,
        "languages": ms.languages,
        "description": ms.description,
        "canvas_count": ms.canvas_count,
        "subject_areas": ms.subject_areas,
        "source_catalog": source_catalog,
    }


def _load_registry(registry_path: Path) -> list[dict]:
    """Load records from JSONL registry file."""
    if not registry_path.exists():
        return []
    records: list[dict] = []
    for line in registry_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def triage_batch(
    registry_path: Path,
    db: DiscoveryWriteAccess,
    model: str = DEFAULT_MODEL_TRIAGE,
    workers: int = 10,
    limit: Optional[int] = None,
    skip_existing: bool = True,
    with_thumbnails: bool = False,
    min_score_for_thumbnail: int = 10,
    with_web_search: bool = True,
    verbose: bool = True,
) -> list[TriageResult]:
    """Batch triage - mirrors run_batch() pattern from runner.py.

    Args:
        registry_path: Path to JSONL registry file
        db: Database connection
        model: Gemini model to use
        workers: Number of parallel workers
        limit: Max items to process
        skip_existing: Skip items already triaged
        with_thumbnails: Fetch thumbnails for high-score items
        min_score_for_thumbnail: Minimum score to trigger thumbnail fetch
        verbose: Print progress

    Returns:
        List of TriageResult objects
    """
    records = _load_registry(registry_path)
    if not records:
        if verbose:
            print(f"No records found in {registry_path}")
        return []

    # Filter to items needing triage
    to_process: list[dict] = []
    for record in records:
        mid = record.get("manuscript_id") or record.get("id")
        if not mid:
            continue

        if skip_existing:
            opp = db.get_opportunity(mid)
            if opp and opp.triage_at:
                continue

        to_process.append(record)

    if limit:
        to_process = to_process[:limit]

    if not to_process:
        if verbose:
            print("No items need triage")
        return []

    if verbose:
        print(f"Triaging {len(to_process)} manuscripts with {workers} workers")
        print(f"Model: {model}")
        print("=" * 60)

    prompt = load_triage_prompt(with_thumbnail=False)
    thumbnail_prompt = load_triage_prompt(with_thumbnail=True) if with_thumbnails else None
    results: list[TriageResult] = []

    def _worker(record: dict) -> TriageResult:
        client = genai.Client()
        result = triage_manuscript(
            client=client,
            metadata=record,
            prompt=prompt,
            model=model,
            with_web_search=with_web_search,
        )

        # If high score and thumbnails enabled, do second pass
        if (
            with_thumbnails
            and thumbnail_prompt
            and result.interest_score >= min_score_for_thumbnail
            and not result.error
        ):
            manifest_url = (record.get("iiif") or {}).get("manifest_url")
            if manifest_url:
                thumbnail_bytes = fetch_thumbnail(manifest_url)
                if thumbnail_bytes:
                    result = triage_manuscript(
                        client=client,
                        metadata=record,
                        prompt=thumbnail_prompt,
                        model=model,
                        thumbnail_bytes=thumbnail_bytes,
                        with_web_search=with_web_search,
                    )

        return result

    if workers <= 1:
        client = genai.Client()
        for i, record in enumerate(to_process, 1):
            if verbose:
                mid = record.get("manuscript_id") or record.get("id")
                print(f"[{i}/{len(to_process)}] {mid}...", end=" ", flush=True)

            result = _worker(record)
            results.append(result)
            save_triage_result(db, result, with_thumbnails, with_web_search)

            if verbose:
                status = "OK" if not result.error else f"ERROR: {result.error}"
                print(f"{result.interest_score}/10 [{result.recommendation}] {status}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, rec): rec for rec in to_process}
            for i, future in enumerate(as_completed(futures), 1):
                record = futures[future]
                mid = record.get("manuscript_id") or record.get("id")
                try:
                    result = future.result()
                    results.append(result)
                    save_triage_result(db, result, with_thumbnails, with_web_search)
                    if verbose:
                        status = "OK" if not result.error else f"ERROR: {result.error}"
                        print(
                            f"[{i}/{len(to_process)}] {mid}: "
                            f"{result.interest_score}/10 [{result.recommendation}] {status}"
                        )
                except Exception as exc:
                    triaged_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
                    result = TriageResult(
                        manuscript_id=mid,
                        interesting=False,
                        interest_score=0,
                        reasons=[],
                        recommendation="error",
                        raw_json="",
                        model=model,
                        triaged_at=triaged_at,
                        error=str(exc),
                    )
                    results.append(result)
                    if verbose:
                        print(f"[{i}/{len(to_process)}] {mid}: ERROR: {exc}")

    # Summary
    if verbose:
        print("=" * 60)
        ok_count = sum(1 for r in results if not r.error)
        err_count = len(results) - ok_count
        high_score = sum(1 for r in results if r.interest_score >= 7)
        queue_count = sum(1 for r in results if r.recommendation == "queue")
        print(f"Complete: {ok_count}, Errors: {err_count}")
        print(f"High interest (>=7): {high_score}")
        print(f"Recommended to queue: {queue_count}")

    return results


def triage_from_db(
    db: DiscoveryWriteAccess,
    model: str = DEFAULT_MODEL_TRIAGE,
    workers: int = 10,
    limit: Optional[int] = None,
    force: bool = False,
    collections: Optional[list[str]] = None,
    manuscript_ids: Optional[list[str]] = None,
    with_web_search: bool = True,
    verbose: bool = True,
) -> list[TriageResult]:
    """Triage manuscripts directly from database with full metadata.

    Args:
        db: Database connection
        model: Gemini model to use
        workers: Number of parallel workers
        limit: Max items to process
        force: Re-triage even if already done
        verbose: Print progress

    Returns:
        List of TriageResult objects
    """
    # Get all manuscripts
    manuscripts = db.list_manuscripts(limit=10000)
    collection_filter = set(collections or [])
    manuscript_id_filter = set(manuscript_ids or [])

    # Filter to those needing triage
    to_process = []
    for ms in manuscripts:
        if collection_filter and ms.collection not in collection_filter:
            continue
        if manuscript_id_filter and ms.id not in manuscript_id_filter:
            continue
        if not force:
            opp = db.get_opportunity(ms.id)
            if opp and opp.triage_at:
                continue
        to_process.append(ms)

    if limit:
        to_process = to_process[:limit]

    if not to_process:
        if verbose:
            print("No manuscripts need triage")
        return []

    if verbose:
        print(f"Triaging {len(to_process)} manuscripts with {workers} workers")
        print(f"Model: {model}")
        print("=" * 60)

    prompt = load_triage_prompt()
    results: list[TriageResult] = []

    def _worker(ms: Manuscript) -> TriageResult:
        client = genai.Client()
        metadata = build_triage_metadata(ms)
        return triage_manuscript(
            client=client,
            metadata=metadata,
            prompt=prompt,
            model=model,
            with_web_search=with_web_search,
        )

    if workers <= 1:
        for i, ms in enumerate(to_process, 1):
            if verbose:
                print(f"[{i}/{len(to_process)}] {ms.shelfmark}...", end=" ", flush=True)
            result = _worker(ms)
            results.append(result)
            save_triage_result(db, result, with_web_search=with_web_search)
            if verbose:
                status = "OK" if not result.error else f"ERROR: {result.error}"
                print(f"{result.interest_score}/10 [{result.recommendation}] {status}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, ms): ms for ms in to_process}
            for i, future in enumerate(as_completed(futures), 1):
                ms = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    save_triage_result(db, result, with_web_search=with_web_search)
                    if verbose:
                        status = "OK" if not result.error else f"ERROR: {result.error}"
                        print(f"[{i}/{len(to_process)}] {ms.shelfmark}: {result.interest_score}/10 [{result.recommendation}] {status}")
                except Exception as exc:
                    triaged_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
                    result = TriageResult(
                        manuscript_id=ms.id,
                        interesting=False,
                        interest_score=0,
                        reasons=[],
                        recommendation="error",
                        raw_json="",
                        model=model,
                        triaged_at=triaged_at,
                        error=str(exc),
                    )
                    results.append(result)
                    if verbose:
                        print(f"[{i}/{len(to_process)}] {ms.shelfmark}: ERROR: {exc}")

    # Summary
    if verbose:
        print("=" * 60)
        ok_count = sum(1 for r in results if not r.error)
        err_count = len(results) - ok_count
        high_score = sum(1 for r in results if r.interest_score >= 7)
        tens = sum(1 for r in results if r.interest_score == 10)
        print(f"Complete: {ok_count}, Errors: {err_count}")
        print(f"High interest (>=7): {high_score}")
        print(f"Score 10: {tens}")

    return results


def triage_high_scorers(
    db: DiscoveryWriteAccess,
    model: str = DEFAULT_MODEL_TRIAGE,
    workers: int = 10,
    min_score: int = 10,
    limit: Optional[int] = None,
    with_web_search: bool = False,
    verbose: bool = True,
) -> list[TriageResult]:
    """Run thumbnail triage on high-scoring items.

    This is a second-pass triage for items that scored highly on
    metadata-only triage. It fetches thumbnails and re-scores with
    visual information.

    Args:
        db: Database connection
        model: Gemini model to use
        workers: Number of parallel workers
        min_score: Minimum initial_score to process
        limit: Max items to process
        verbose: Print progress

    Returns:
        List of TriageResult objects
    """
    # Get high scorers that haven't had thumbnail triage
    opps = db.list_opportunities(min_initial_score=min_score, limit=limit or 1000)
    to_process = [
        opp for opp in opps
        if opp.triage_method not in {"gemini_flash_thumbnail", "gemini_web_thumbnail"}
    ]

    if limit:
        to_process = to_process[:limit]

    if not to_process:
        if verbose:
            print(f"No items with score >= {min_score} need thumbnail triage")
        return []

    if verbose:
        print(f"Thumbnail triage for {len(to_process)} high-scoring manuscripts")
        print(f"Model: {model}")
        print("=" * 60)

    prompt = load_triage_prompt(with_thumbnail=True)
    results: list[TriageResult] = []
    tasks: list[ThumbnailTriageTask] = []

    for opp in to_process:
        ms = db.get_manuscript(opp.manuscript_id)
        if not ms:
            results.append(
                TriageResult(
                    manuscript_id=opp.manuscript_id,
                    interesting=False,
                    interest_score=0,
                    reasons=[],
                    recommendation="error",
                    raw_json="",
                    model=model,
                    triaged_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    error="Manuscript not found",
                )
            )
            continue

        metadata = build_triage_metadata(ms)
        metadata["previous_score"] = opp.initial_score
        metadata["previous_reasons"] = opp.interest_reason
        tasks.append(
            ThumbnailTriageTask(
                manuscript_id=opp.manuscript_id,
                shelfmark=ms.shelfmark,
                manifest_url=ms.iiif_manifest_url,
                metadata=metadata,
                initial_interest=opp.initial_interest or False,
                initial_score=opp.initial_score or 0,
            )
        )

    if not tasks:
        if verbose:
            print("No thumbnail triage tasks could be prepared")
        return results

    def _worker(task: ThumbnailTriageTask) -> TriageResult:
        client = genai.Client()
        thumbnail_bytes = fetch_thumbnail(task.manifest_url) if task.manifest_url else None

        if not thumbnail_bytes:
            return TriageResult(
                manuscript_id=task.manuscript_id,
                interesting=task.initial_interest,
                interest_score=task.initial_score,
                reasons=[],
                recommendation="skip",
                raw_json="",
                model=model,
                triaged_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                error="No thumbnail available",
            )

        return triage_manuscript(
            client=client,
            metadata=task.metadata,
            prompt=prompt,
            model=model,
            thumbnail_bytes=thumbnail_bytes,
            with_web_search=with_web_search,
        )

    if workers <= 1:
        for i, task in enumerate(tasks, 1):
            if verbose:
                print(f"[{i}/{len(tasks)}] {task.shelfmark}...", end=" ", flush=True)

            result = _worker(task)
            results.append(result)
            save_triage_result(db, result, with_thumbnail=True, with_web_search=with_web_search)

            if verbose:
                status = "OK" if not result.error else f"ERROR: {result.error}"
                print(f"{result.interest_score}/10 [{result.recommendation}] {status}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, task): task for task in tasks}
            for i, future in enumerate(as_completed(futures), 1):
                task = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    save_triage_result(db, result, with_thumbnail=True, with_web_search=with_web_search)
                    if verbose:
                        status = "OK" if not result.error else f"ERROR: {result.error}"
                        print(
                            f"[{i}/{len(tasks)}] {task.shelfmark}: "
                            f"{result.interest_score}/10 [{result.recommendation}] {status}"
                        )
                except Exception as exc:
                    triaged_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
                    result = TriageResult(
                        manuscript_id=task.manuscript_id,
                        interesting=False,
                        interest_score=0,
                        reasons=[],
                        recommendation="error",
                        raw_json="",
                        model=model,
                        triaged_at=triaged_at,
                        error=str(exc),
                    )
                    results.append(result)
                    if verbose:
                        print(f"[{i}/{len(tasks)}] {task.shelfmark}: ERROR: {exc}")

    if verbose:
        print("=" * 60)
        ok_count = sum(1 for r in results if not r.error)
        err_count = len(results) - ok_count
        high_score = sum(1 for r in results if r.interest_score >= 7)
        print(f"Complete: {ok_count}, Errors: {err_count}")
        print(f"High interest (>=7): {high_score}")

    return results
