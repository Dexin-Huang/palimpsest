"""Discovery commands - simplified DB-first flow.

The single path is:
    discovery add 100 --collection Reg.lat --range 1500-2100
        ↓
    Fetches manifest → Adds to DB → Triages immediately → Done!

Commands:
    add      - Add N new manuscripts to DB and triage them
    triage   - Re-triage existing manuscripts in DB
    enrich   - Re-enrich DB from cached manifests
    stats    - Show database statistics
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from palimpsest.config import DEFAULT_MODEL_SCHOLAR_AGENT, DEFAULT_MODEL_TRIAGE
from palimpsest.library.iiif import extract_canvases, extract_manifest_summary


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def safe_console_text(value: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


def parse_range(value: str) -> tuple[int, int]:
    """Parse a range string like '1500-2100' into (start, end)."""
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError("range must be like 1500-2100")
    return int(parts[0]), int(parts[1])


def extract_metadata(manifest: dict) -> dict:
    """Extract structured metadata from IIIF manifest."""
    return extract_manifest_summary(manifest)


def fetch_manifest(url: str, retries: int = 3, delay: float = 1.0) -> dict | None:
    """Fetch IIIF manifest with retries."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None) if hasattr(exc, "response") else None
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay * (2**attempt))
                continue
            if status == 404:
                return None
            raise exc
    return None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def truncate_text(value: str | None, max_chars: int = 800) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def language_labels_from_source_catalog(source_catalog: dict[str, Any] | None) -> list[str]:
    if not source_catalog:
        return []
    labels = []
    for value in coerce_list(source_catalog.get("language_or_script")):
        if "(lang.)" in value:
            labels.append(value.split("(", 1)[0].strip())
    return labels


def subject_labels_from_source_catalog(source_catalog: dict[str, Any] | None) -> list[str]:
    if not source_catalog:
        return []
    return coerce_list(source_catalog.get("subject"))


def build_description_from_source_catalog(source_catalog: dict[str, Any] | None) -> str | None:
    if not source_catalog:
        return None
    parts = []
    title = source_catalog.get("title")
    if title:
        parts.append(f"Title: {title}")
    if source_catalog.get("find_site"):
        parts.append(f"Find site: {source_catalog['find_site']}")
    if source_catalog.get("institution"):
        parts.append(f"Institution: {source_catalog['institution']}")
    if source_catalog.get("provenance"):
        parts.append(f"Provenance: {source_catalog['provenance']}")
    source_fields = source_catalog.get("source_fields") or {}
    if isinstance(source_fields, dict):
        detail = source_fields.get("description")
        if detail:
            parts.append(truncate_text(detail))
    if not parts and source_catalog.get("search_summary"):
        parts.append(truncate_text(source_catalog["search_summary"]))
    return "; ".join(part for part in parts if part) or None


def merge_source_catalog_with_manifest(
    source_catalog: dict[str, Any] | None,
    manifest_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if source_catalog is None and manifest_summary is None:
        return None

    merged = dict(source_catalog or {})
    if not manifest_summary:
        return merged

    merged["manifest_summary"] = manifest_summary
    for key in ("title", "date", "language", "description"):
        if not merged.get(key) and manifest_summary.get(key):
            merged[key] = manifest_summary[key]
    if manifest_summary.get("canvas_count") is not None:
        merged["canvas_count"] = manifest_summary["canvas_count"]
    if manifest_summary.get("metadata_field_count") is not None:
        merged["manifest_metadata_fields"] = manifest_summary["metadata_field_count"]
    return merged


def repository_code_for_source(source_id: str) -> str:
    mapping = {
        "vatican": "BAV",
        "idp": "IDP",
    }
    return mapping.get(source_id, source_id.upper())


def cmd_add(args: argparse.Namespace) -> None:
    """Add N new manuscripts to database and triage them immediately."""
    from palimpsest.discovery import DiscoveryDB, Manuscript
    from palimpsest.discovery.triage import (
        build_triage_metadata,
        combined_interest_score,
        load_triage_prompt,
        save_triage_result,
        triage_manuscript,
    )
    from google import genai

    db = DiscoveryDB(args.db)
    client = genai.Client()
    prompt = load_triage_prompt()

    # Get existing manuscript IDs
    existing_ids = set()
    for ms in db.list_manuscripts(limit=100000):
        existing_ids.add(ms.id)

    collection = args.collection
    added = 0
    triaged = 0
    high_interest = 0

    # Parse range if provided
    if args.range_:
        start, end = parse_range(args.range_)
    else:
        start, end = 1, 3000

    print(f"Adding up to {args.count} new manuscripts from {collection} (range {start}-{end})...")

    for number in range(start, end + 1):
        if added >= args.count:
            break

        manuscript_id = f"vat_{collection.lower().replace('.', '_')}_{number}"
        if manuscript_id in existing_ids:
            continue

        shelfmark = f"{collection}.{number}"
        manifest_url = f"https://digi.vatlib.it/iiif/MSS_{shelfmark}/manifest.json"

        manifest = fetch_manifest(manifest_url, retries=3, delay=max(args.delay, 1.0))

        if not manifest:
            continue

        # Extract metadata
        canvases = extract_canvases(manifest)
        content = extract_metadata(manifest)
        title = content.get("title") if not content.get("title_is_shelfmark") else None
        language = content.get("language")
        description_parts = []
        if content.get("author"):
            description_parts.append(f"Author: {content['author']}")
        if content.get("contributor"):
            description_parts.append(f"Contributor: {content['contributor']}")
        if content.get("place"):
            description_parts.append(f"Place: {content['place']}")
        if content.get("description"):
            description_parts.append(content["description"])

        # Add to database
        ms = Manuscript(
            id=manuscript_id,
            shelfmark=shelfmark,
            repository="BAV",
            collection=collection,
            iiif_manifest_url=manifest_url,
            canvas_count=len(canvases),
            title=title,
            date_range=content.get("date"),
            languages=[language] if language else None,
            description="; ".join(description_parts) if description_parts else None,
            source_catalog=content,
        )
        db.add_manuscript(ms, agent="discovery_add")
        db.ensure_opportunity(manuscript_id)
        added += 1

        metadata = build_triage_metadata(ms)

        try:
            result = triage_manuscript(client, metadata, prompt)
            save_triage_result(db, result, with_web_search=True)
            i_score = result.interest_score or 0
            r_score = result.rarity_score or 0
            u_score = result.unstudied_score or 0
            combined_score = combined_interest_score(result)
            triaged += 1
            if combined_score >= 7:
                high_interest += 1
            status = f"{combined_score} (I:{i_score} R:{r_score} U:{u_score})"
        except Exception as e:
            status = f"triage error: {e}"

        print(f"  [{added}/{args.count}] {shelfmark}: {status}")
        time.sleep(args.delay)

    print(f"\nDone! Added {added}, triaged {triaged}, high interest (>=7): {high_interest}")
    db.close()


def cmd_triage(args: argparse.Namespace) -> None:
    """Run Gemini triage on manuscripts in database."""
    from palimpsest.discovery import DiscoveryDB
    from palimpsest.discovery.triage import triage_from_db

    db = DiscoveryDB(args.db)
    collection_filter = set(args.collection or [])
    manuscript_id_filter = set(args.manuscript_id or [])

    if args.dry_run:
        manuscripts = db.list_manuscripts(limit=10000)
        to_triage = []
        for ms in manuscripts:
            if collection_filter and ms.collection not in collection_filter:
                continue
            if manuscript_id_filter and ms.id not in manuscript_id_filter:
                continue
            if not args.force:
                opp = db.get_opportunity(ms.id)
                if opp and opp.triage_at:
                    continue
            to_triage.append(ms)
        if args.limit:
            to_triage = to_triage[:args.limit]

        print(f"Would triage {len(to_triage)} manuscripts")
        for i, ms in enumerate(to_triage[:10]):
            title = safe_console_text(ms.title or "(no title)")
            print(f"  {i + 1}. {ms.shelfmark}: {title}")
        if len(to_triage) > 10:
            print(f"  ... and {len(to_triage) - 10} more")
        db.close()
        return

    results = triage_from_db(
        db=db,
        model=args.model,
        workers=args.workers,
        limit=args.limit,
        force=args.force,
        collections=args.collection,
        manuscript_ids=args.manuscript_id,
        verbose=True,
    )

    print(f"\nTriaged {len(results)} manuscripts")
    db.close()


def cmd_enrich(args: argparse.Namespace) -> None:
    """Re-enrich database manuscripts from cached manifests."""
    from palimpsest.discovery import DiscoveryDB

    db = DiscoveryDB(args.db)
    manifest_dir = Path(args.manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manuscripts = db.list_manuscripts(limit=100000)
    collection_filter = set(args.collection or [])
    manuscript_id_filter = set(args.manuscript_id or [])
    enriched = 0

    print(f"Enriching manifests into {manifest_dir}...")

    for ms in manuscripts:
        if collection_filter and ms.collection not in collection_filter:
            continue
        if manuscript_id_filter and ms.id not in manuscript_id_filter:
            continue

        manifest_path = manifest_dir / f"{ms.id}.json"
        manifest = None
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        elif args.fetch_missing and ms.iiif_manifest_url:
            try:
                manifest = fetch_manifest(ms.iiif_manifest_url)
                if manifest:
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception as e:
                print(f"  Error fetching {ms.id}: {e}")
                continue
        else:
            continue

        try:
            meta = extract_metadata(manifest)

            updates = {"source_catalog": meta}
            if meta.get("title") and not meta.get("title_is_shelfmark"):
                updates["title"] = meta["title"]
            if meta.get("date"):
                updates["date_range"] = meta["date"]
            if meta.get("language"):
                updates["languages"] = [meta["language"]]
            if meta.get("canvas_count"):
                updates["canvas_count"] = meta["canvas_count"]

            desc_parts = []
            if meta.get("author"):
                desc_parts.append(f"Author: {meta['author']}")
            if meta.get("contributor"):
                desc_parts.append(f"Contributor: {meta['contributor']}")
            if meta.get("place"):
                desc_parts.append(f"Place: {meta['place']}")
            if desc_parts:
                updates["description"] = "; ".join(desc_parts)

            if updates:
                db.update_manuscript(ms.id, updates, agent="enrich")
                enriched += 1

        except Exception as e:
            print(f"  Error enriching {ms.id}: {e}")

    print(f"Enriched {enriched} manuscripts")
    db.close()


def cmd_stats(args: argparse.Namespace) -> None:
    """Show database statistics."""
    from palimpsest.discovery import DiscoveryDB

    db = DiscoveryDB(args.db)

    manuscripts = db.list_manuscripts(limit=100000)
    opportunities = db.list_opportunities()

    print(f"Database: {args.db}")
    print(f"Total manuscripts: {len(manuscripts)}")
    print(f"Total opportunities: {len(opportunities)}")

    # Score distribution
    triaged = [o for o in opportunities if o.initial_score is not None]
    print(f"\nTriaged: {len(triaged)}")

    if triaged:
        scores = {}
        for o in triaged:
            bucket = int(o.initial_score) if o.initial_score else 0
            scores[bucket] = scores.get(bucket, 0) + 1

        print("\nScore distribution:")
        for s in sorted(scores.keys(), reverse=True):
            bar = "#" * scores[s]
            print(f"  {s:2d}: {scores[s]:4d} {bar}")

    # Top 10
    top = sorted(triaged, key=lambda x: x.initial_score or 0, reverse=True)[:10]
    if top:
        print("\nTop 10 by combined score:")
        for o in top:
            print(f"  {o.initial_score:.1f} | {o.manuscript_id}")

    db.close()


def cmd_sources_list(args: argparse.Namespace) -> None:
    from palimpsest.discovery.sources import get_source_adapter, get_source_adapters

    if args.source:
        adapters = {args.source: get_source_adapter(args.source)}
    else:
        adapters = get_source_adapters()

    for source_id, adapter in sorted(adapters.items()):
        print(f"{source_id}: {adapter.label}")
        for collection in adapter.list_collections():
            line = f"  - {collection.key}: {collection.label}"
            print(safe_console_text(line))
            fit_bits = []
            if collection.automation_fit is not None:
                fit_bits.append(f"automation={collection.automation_fit}/5")
            if collection.north_star_fit is not None:
                fit_bits.append(f"north_star={collection.north_star_fit}/5")
            if collection.access:
                fit_bits.append(f"access={collection.access}")
            if fit_bits:
                print(f"      {safe_console_text(' | '.join(fit_bits))}")
            if collection.notes:
                print(f"      {safe_console_text(collection.notes)}")
            if collection.listing_url:
                print(f"      {collection.listing_url}")


def cmd_sources_scrape(args: argparse.Namespace) -> None:
    from palimpsest.discovery.sources import get_source_adapter

    adapter = get_source_adapter(args.source)
    refs = adapter.scrape_collection(
        args.collection,
        max_pages=args.max_pages,
        limit=args.limit,
        delay=args.delay,
        include_details=not args.no_details,
    )

    print(
        f"Scraped {len(refs)} refs from {args.source}:{args.collection}"
    )
    for ref in refs[: min(len(refs), args.preview)]:
        title = None
        if ref.source_catalog:
            title = ref.source_catalog.get("title")
        title_text = safe_console_text(title or "(no title)")
        print(f"  - {safe_console_text(ref.shelfmark)} | {title_text}")
        if ref.viewer_url:
            print(f"      {ref.viewer_url}")
        if ref.manifest_url:
            print(f"      {ref.manifest_url}")

    if args.output:
        rows = [ref.as_record() for ref in refs]
        written = write_jsonl(Path(args.output), rows)
        print(f"Wrote {written} refs to {args.output}")


def cmd_sources_ingest(args: argparse.Namespace) -> None:
    from palimpsest.discovery import DiscoveryDB, Manuscript
    from palimpsest.discovery.sources import get_source_adapter
    from palimpsest.discovery.triage import triage_from_db

    adapter = get_source_adapter(args.source)
    refs = adapter.scrape_collection(
        args.collection,
        max_pages=args.max_pages,
        limit=args.limit,
        delay=args.delay,
        include_details=not args.no_details,
    )

    db = DiscoveryDB(args.db)
    added = 0
    updated = 0
    skipped = 0
    ingested_ids: list[str] = []

    print(f"Ingesting {len(refs)} refs from {args.source}:{args.collection} into {args.db}")

    for ref in refs:
        manifest_summary = None
        canvas_count = None
        if ref.manifest_url and not args.no_manifest_fetch:
            try:
                manifest = fetch_manifest(ref.manifest_url)
                manifest_summary = extract_metadata(manifest)
                canvas_count = manifest_summary.get("canvas_count")
            except Exception as exc:
                print(f"  ! manifest fetch failed for {safe_console_text(ref.shelfmark)}: {exc}")

        source_catalog = merge_source_catalog_with_manifest(ref.source_catalog, manifest_summary)
        languages = language_labels_from_source_catalog(source_catalog)
        subject_areas = subject_labels_from_source_catalog(source_catalog)
        title = source_catalog.get("title") if source_catalog else None
        date_range = None
        if source_catalog:
            date_range = source_catalog.get("date")
        description = build_description_from_source_catalog(source_catalog)

        ms = Manuscript(
            id=ref.manuscript_id,
            shelfmark=ref.shelfmark,
            repository=repository_code_for_source(ref.source_id),
            iiif_manifest_url=ref.manifest_url,
            canvas_count=canvas_count,
            collection=ref.collection,
            title=title,
            date_range=date_range,
            languages=languages or None,
            subject_areas=subject_areas or None,
            description=description,
            source_catalog=source_catalog,
        )

        existing = db.get_manuscript(ref.manuscript_id)
        if existing and not args.update_existing:
            skipped += 1
            db.ensure_opportunity(ref.manuscript_id)
            continue

        if existing:
            db.update_manuscript(
                ref.manuscript_id,
                {
                    "iiif_manifest_url": ms.iiif_manifest_url,
                    "canvas_count": ms.canvas_count,
                    "collection": ms.collection,
                    "title": ms.title,
                    "date_range": ms.date_range,
                    "languages": ms.languages,
                    "subject_areas": ms.subject_areas,
                    "description": ms.description,
                    "source_catalog": ms.source_catalog,
                },
                agent=f"{args.source}_sources_ingest",
            )
            updated += 1
        else:
            db.add_manuscript(ms, agent=f"{args.source}_sources_ingest")
            added += 1

        db.ensure_opportunity(ref.manuscript_id)
        ingested_ids.append(ref.manuscript_id)
        print(f"  - {safe_console_text(ref.shelfmark)}")

    print(f"Added {added}, updated {updated}, skipped {skipped}")

    if args.triage and ingested_ids:
        print(f"Triage on {len(ingested_ids)} ingested manuscripts...")
        triage_from_db(
            db=db,
            model=args.model,
            workers=args.workers,
            manuscript_ids=ingested_ids,
            force=args.force_triage,
            verbose=True,
        )

    db.close()


def cmd_scout(args: argparse.Namespace) -> None:
    from palimpsest.discovery.scout import run_candidate_scout, utc_now_slug

    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    else:
        scope_bits = [
            args.repository.lower() if args.repository else "all",
            args.collection.lower() if args.collection else "all",
            utc_now_slug(),
        ]
        out_dir = (Path("discovery") / "scouts" / "_".join(scope_bits)).resolve()

    payload = asyncio.run(
        run_candidate_scout(
            db_path=Path(args.db).resolve(),
            out_dir=out_dir,
            repository=args.repository,
            collection=args.collection,
            min_score=args.min_score,
            limit=args.limit,
            model=args.model,
            with_web_search=args.with_web_search,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
            max_thinking_tokens=args.max_thinking_tokens,
            permission_mode=args.permission_mode,
        )
    )

    print(f"candidates: {payload['candidate_count']}")
    print(f"memo: {payload['memo_path']}")
    print(f"meta: {payload['meta_path']}")
    print(f"bundle: {payload['candidates_path']}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("discovery", help="Discovery utilities")
    sub = parser.add_subparsers(dest="discovery_cmd", required=True)

    # Add command - the main entry point
    add = sub.add_parser("add", help="Add N new manuscripts to DB and triage them")
    add.add_argument("count", type=int, help="Number of new manuscripts to add")
    add.add_argument("--collection", default="Reg.lat", help="Collection (default: Reg.lat)")
    add.add_argument("--range", dest="range_", help="Shelfmark range like 1500-2100")
    add.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    add.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    add.set_defaults(func=cmd_add)

    # Triage command - re-triage existing entries
    triage = sub.add_parser("triage", help="Run Gemini triage on manuscripts in DB")
    triage.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    triage.add_argument("--limit", type=int, help="Max items to process")
    triage.add_argument("--force", action="store_true", help="Re-triage already scored items")
    triage.add_argument("--collection", action="append", help="Restrict to collection (repeatable)")
    triage.add_argument("--manuscript-id", action="append", help="Restrict to manuscript id (repeatable)")
    triage.add_argument("--dry-run", action="store_true", help="Show what would be triaged")
    triage.add_argument("--model", default=DEFAULT_MODEL_TRIAGE, help="Gemini model")
    triage.add_argument("--workers", type=int, default=10, help="Parallel workers")
    triage.set_defaults(func=cmd_triage)

    # Enrich command
    enrich = sub.add_parser("enrich", help="Re-enrich DB from cached manifests")
    enrich.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    enrich.add_argument("--manifest-dir", default="discovery/manifests", help="Manifest cache")
    enrich.add_argument("--fetch-missing", action="store_true", help="Fetch live manifests when cache entries are missing")
    enrich.add_argument("--collection", action="append", help="Restrict to collection (repeatable)")
    enrich.add_argument("--manuscript-id", action="append", help="Restrict to manuscript id (repeatable)")
    enrich.set_defaults(func=cmd_enrich)

    # Stats command
    stats = sub.add_parser("stats", help="Show database statistics")
    stats.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    stats.set_defaults(func=cmd_stats)

    scout = sub.add_parser("scout", help="Run the dedicated Claude scouting agent over DB candidates")
    scout.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    scout.add_argument("--repository", help="Restrict candidates to one repository code, e.g. IDP or BAV")
    scout.add_argument("--collection", help="Restrict candidates to one collection key")
    scout.add_argument("--min-score", type=int, default=8, help="Minimum combined interest score")
    scout.add_argument("--limit", type=int, default=12, help="Maximum candidates to include")
    scout.add_argument("--out-dir", help="Explicit scout workspace output directory")
    scout.add_argument("--model", default=DEFAULT_MODEL_SCHOLAR_AGENT, help="Claude model for the scout agent")
    scout.add_argument("--with-web-search", action="store_true", help="Allow WebSearch during scout runs")
    scout.add_argument("--max-turns", type=int, default=60, help="Maximum turns for the scout run")
    scout.add_argument("--max-budget-usd", type=float, default=None, help="Maximum budget in USD")
    scout.add_argument("--max-thinking-tokens", type=int, default=None, help="Maximum thinking tokens per response")
    scout.add_argument("--permission-mode", default="default", choices=["default", "plan"], help="Claude permission mode")
    scout.set_defaults(func=cmd_scout)

    # Source adapter commands
    sources = sub.add_parser("sources", help="List or scrape curated discovery sources")
    sources_sub = sources.add_subparsers(dest="sources_cmd", required=True)

    sources_list = sources_sub.add_parser("list", help="List registered discovery sources")
    sources_list.add_argument("--source", help="Restrict to one source id")
    sources_list.set_defaults(func=cmd_sources_list)

    sources_scrape = sources_sub.add_parser(
        "scrape",
        help="Scrape one curated source collection into JSONL refs",
    )
    sources_scrape.add_argument("--source", required=True, help="Source adapter id")
    sources_scrape.add_argument("--collection", required=True, help="Curated collection key")
    sources_scrape.add_argument("--max-pages", type=int, default=1, help="Max result pages")
    sources_scrape.add_argument("--limit", type=int, help="Max refs to return")
    sources_scrape.add_argument("--delay", type=float, default=0.2, help="Delay between requests")
    sources_scrape.add_argument("--no-details", action="store_true", help="Skip per-record detail fetches")
    sources_scrape.add_argument("--preview", type=int, default=5, help="How many refs to print")
    sources_scrape.add_argument("--output", help="Optional JSONL output path")
    sources_scrape.set_defaults(func=cmd_sources_scrape)

    sources_ingest = sources_sub.add_parser(
        "ingest",
        help="Scrape one curated source collection into the discovery DB and optionally triage it",
    )
    sources_ingest.add_argument("--source", required=True, help="Source adapter id")
    sources_ingest.add_argument("--collection", required=True, help="Curated collection key")
    sources_ingest.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    sources_ingest.add_argument("--max-pages", type=int, default=1, help="Max result pages")
    sources_ingest.add_argument("--limit", type=int, help="Max refs to ingest")
    sources_ingest.add_argument("--delay", type=float, default=0.2, help="Delay between requests")
    sources_ingest.add_argument("--no-details", action="store_true", help="Skip per-record detail fetches")
    sources_ingest.add_argument("--no-manifest-fetch", action="store_true", help="Do not fetch manifests for canvas counts")
    sources_ingest.add_argument("--update-existing", action="store_true", help="Update existing manuscript records")
    sources_ingest.add_argument("--triage", action="store_true", help="Run triage after ingest")
    sources_ingest.add_argument("--force-triage", action="store_true", help="Re-triage items even if already scored")
    sources_ingest.add_argument("--model", default=DEFAULT_MODEL_TRIAGE, help="Gemini model")
    sources_ingest.add_argument("--workers", type=int, default=10, help="Parallel triage workers")
    sources_ingest.set_defaults(func=cmd_sources_ingest)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Discovery commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
