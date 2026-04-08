from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

from palimpsest.discovery.database import DiscoveryDB
from palimpsest.discovery.iiif import extract_metadata as discovery_extract_metadata
from palimpsest.discovery.records import Manuscript

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Palimpsest discovery)",
}


def safe_console_text(value: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


def format_source_fit(
    *,
    automation_fit: int | None,
    north_star_fit: int | None,
    access: str | None,
) -> str | None:
    bits: list[str] = []
    if automation_fit is not None:
        bits.append(f"automation={automation_fit}/5")
    if north_star_fit is not None:
        bits.append(f"north_star={north_star_fit}/5")
    if access:
        bits.append(f"access={access}")
    return " | ".join(bits) if bits else None


def extract_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    return discovery_extract_metadata(manifest)


def fetch_manifest(url: str, retries: int = 3, delay: float = 1.0) -> dict[str, Any] | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
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


def triage_manuscripts(
    *,
    db_path: str,
    limit: int | None,
    force: bool,
    collections: list[str] | None,
    manuscript_ids: list[str] | None,
    dry_run: bool,
    model: str,
    workers: int,
) -> None:
    from palimpsest.discovery.triage import triage_from_db

    with DiscoveryDB(db_path) as db:
        collection_filter = set(collections or [])
        manuscript_id_filter = set(manuscript_ids or [])

        if dry_run:
            manuscripts = db.list_manuscripts(limit=10000)
            to_triage = []
            for manuscript in manuscripts:
                if collection_filter and manuscript.collection not in collection_filter:
                    continue
                if manuscript_id_filter and manuscript.id not in manuscript_id_filter:
                    continue
                if not force:
                    opportunity = db.get_opportunity(manuscript.id)
                    if opportunity and opportunity.triage_at:
                        continue
                to_triage.append(manuscript)
            if limit:
                to_triage = to_triage[:limit]

            print(f"Would triage {len(to_triage)} manuscripts")
            for index, manuscript in enumerate(to_triage[:10]):
                title = safe_console_text(manuscript.title or "(no title)")
                print(f"  {index + 1}. {manuscript.shelfmark}: {title}")
            if len(to_triage) > 10:
                print(f"  ... and {len(to_triage) - 10} more")
            return

        results = triage_from_db(
            db=db,
            model=model,
            workers=workers,
            limit=limit,
            force=force,
            collections=collections,
            manuscript_ids=manuscript_ids,
            verbose=True,
        )
        print(f"\nTriaged {len(results)} manuscripts")


def enrich_manuscripts(
    *,
    db_path: str,
    manifest_dir: Path,
    fetch_missing: bool,
    collections: list[str] | None,
    manuscript_ids: list[str] | None,
) -> None:
    with DiscoveryDB(db_path) as db:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manuscripts = db.list_manuscripts(limit=100000)
        collection_filter = set(collections or [])
        manuscript_id_filter = set(manuscript_ids or [])
        enriched = 0

        print(f"Enriching manifests into {manifest_dir}...")

        for manuscript in manuscripts:
            if collection_filter and manuscript.collection not in collection_filter:
                continue
            if manuscript_id_filter and manuscript.id not in manuscript_id_filter:
                continue

            manifest_path = manifest_dir / f"{manuscript.id}.json"
            manifest = None
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            elif fetch_missing and manuscript.iiif_manifest_url:
                try:
                    manifest = fetch_manifest(manuscript.iiif_manifest_url)
                    if manifest:
                        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:
                    print(f"  Error fetching {manuscript.id}: {exc}")
                    continue
            else:
                continue

            try:
                meta = extract_metadata(manifest)
                updates: dict[str, Any] = {"source_catalog": meta}
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
                    db.update_manuscript(manuscript.id, updates, agent="enrich")
                    enriched += 1
            except Exception as exc:
                print(f"  Error enriching {manuscript.id}: {exc}")

        print(f"Enriched {enriched} manuscripts")


def show_stats(*, db_path: str) -> None:
    with DiscoveryDB(db_path) as db:
        manuscripts = db.list_manuscripts(limit=100000)
        opportunities = db.list_opportunities()

        print(f"Database: {db_path}")
        print(f"Total manuscripts: {len(manuscripts)}")
        print(f"Total opportunities: {len(opportunities)}")

        triaged = [opportunity for opportunity in opportunities if opportunity.initial_score is not None]
        print(f"\nTriaged: {len(triaged)}")

        if triaged:
            scores: dict[int, int] = {}
            for opportunity in triaged:
                bucket = int(opportunity.initial_score) if opportunity.initial_score else 0
                scores[bucket] = scores.get(bucket, 0) + 1

            print("\nScore distribution:")
            for score in sorted(scores.keys(), reverse=True):
                bar = "#" * scores[score]
                print(f"  {score:2d}: {scores[score]:4d} {bar}")

        top = sorted(triaged, key=lambda item: item.initial_score or 0, reverse=True)[:10]
        if top:
            print("\nTop 10 by combined score:")
            for opportunity in top:
                print(f"  {opportunity.initial_score:.1f} | {opportunity.manuscript_id}")


def list_sources(*, source: str | None) -> None:
    from palimpsest.discovery.sources import get_source_adapter, get_source_adapters

    if source:
        adapters = {source: get_source_adapter(source)}
    else:
        adapters = get_source_adapters()

    for source_id, adapter in sorted(adapters.items()):
        print(f"{source_id}: {adapter.label}")
        source_fit = format_source_fit(
            automation_fit=getattr(adapter, "automation_fit", None),
            north_star_fit=getattr(adapter, "north_star_fit", None),
            access=getattr(adapter, "access", None),
        )
        if source_fit:
            print(f"      {safe_console_text(source_fit)}")
        for collection in adapter.list_collections():
            line = f"  - {collection.key}: {collection.label}"
            print(safe_console_text(line))
            collection_fit = format_source_fit(
                automation_fit=collection.automation_fit,
                north_star_fit=collection.north_star_fit,
                access=collection.access,
            )
            if collection_fit and collection_fit != source_fit:
                print(f"      {safe_console_text(collection_fit)}")
            if collection.notes:
                print(f"      {safe_console_text(collection.notes)}")
            if collection.listing_url:
                print(f"      {collection.listing_url}")


def scrape_sources(
    *,
    source: str,
    collection: str,
    max_pages: int,
    limit: int | None,
    delay: float,
    include_details: bool,
    preview: int,
    output: str | None,
) -> None:
    from palimpsest.discovery.sources import get_source_adapter

    adapter = get_source_adapter(source)
    refs = adapter.scrape_collection(
        collection,
        max_pages=max_pages,
        limit=limit,
        delay=delay,
        include_details=include_details,
    )

    print(f"Scraped {len(refs)} refs from {source}:{collection}")
    for ref in refs[: min(len(refs), preview)]:
        title = ref.source_catalog.get("title") if ref.source_catalog else None
        print(f"  - {safe_console_text(ref.shelfmark)} | {safe_console_text(title or '(no title)')}")
        if ref.viewer_url:
            print(f"      {ref.viewer_url}")
        if ref.manifest_url:
            print(f"      {ref.manifest_url}")

    if output:
        rows = [ref.as_record() for ref in refs]
        written = write_jsonl(Path(output), rows)
        print(f"Wrote {written} refs to {output}")


def ingest_sources(
    *,
    source: str,
    collection: str,
    db_path: str,
    max_pages: int,
    limit: int | None,
    delay: float,
    include_details: bool,
    fetch_manifests: bool,
    update_existing: bool,
    run_triage: bool,
    force_triage: bool,
    model: str,
    workers: int,
) -> None:
    from palimpsest.discovery.sources import get_source_adapter
    from palimpsest.discovery.triage import triage_from_db

    adapter = get_source_adapter(source)
    refs = adapter.scrape_collection(
        collection,
        max_pages=max_pages,
        limit=limit,
        delay=delay,
        include_details=include_details,
    )

    with DiscoveryDB(db_path) as db:
        added = 0
        updated = 0
        skipped = 0
        ingested_ids: list[str] = []

        print(f"Ingesting {len(refs)} refs from {source}:{collection} into {db_path}")

        for ref in refs:
            manifest_summary = None
            canvas_count = None
            if ref.manifest_url and fetch_manifests:
                try:
                    manifest = fetch_manifest(ref.manifest_url)
                    manifest_summary = extract_metadata(manifest)
                    canvas_count = manifest_summary.get("canvas_count")
                except Exception as exc:
                    print(f"  ! manifest fetch failed for {safe_console_text(ref.shelfmark)}: {exc}")

            source_catalog = merge_source_catalog_with_manifest(ref.source_catalog, manifest_summary)
            title = source_catalog.get("title") if source_catalog else None
            date_range = source_catalog.get("date") if source_catalog else None

            manuscript = Manuscript(
                id=ref.manuscript_id,
                shelfmark=ref.shelfmark,
                repository=repository_code_for_source(ref.source_id),
                iiif_manifest_url=ref.manifest_url,
                canvas_count=canvas_count,
                collection=ref.collection,
                title=title,
                date_range=date_range,
                languages=language_labels_from_source_catalog(source_catalog) or None,
                subject_areas=subject_labels_from_source_catalog(source_catalog) or None,
                description=build_description_from_source_catalog(source_catalog),
                source_catalog=source_catalog,
            )

            existing = db.get_manuscript(ref.manuscript_id) or db.get_manuscript_by_shelfmark(ref.shelfmark)
            target_manuscript_id = existing.id if existing else ref.manuscript_id

            if existing and not update_existing:
                skipped += 1
                db.ensure_opportunity(target_manuscript_id)
                continue

            if existing:
                db.update_manuscript(
                    target_manuscript_id,
                    {
                        "iiif_manifest_url": manuscript.iiif_manifest_url,
                        "canvas_count": manuscript.canvas_count,
                        "collection": manuscript.collection,
                        "title": manuscript.title,
                        "date_range": manuscript.date_range,
                        "languages": manuscript.languages,
                        "subject_areas": manuscript.subject_areas,
                        "description": manuscript.description,
                        "source_catalog": manuscript.source_catalog,
                    },
                    agent=f"{source}_sources_ingest",
                )
                updated += 1
            else:
                db.add_manuscript(manuscript, agent=f"{source}_sources_ingest")
                added += 1

            db.ensure_opportunity(target_manuscript_id)
            ingested_ids.append(target_manuscript_id)
            print(f"  - {safe_console_text(ref.shelfmark)}")

        print(f"Added {added}, updated {updated}, skipped {skipped}")

        if run_triage and ingested_ids:
            print(f"Triage on {len(ingested_ids)} ingested manuscripts...")
            triage_from_db(
                db=db,
                model=model,
                workers=workers,
                manuscript_ids=ingested_ids,
                force=force_triage,
                verbose=True,
            )


def run_scout(
    *,
    db_path: str,
    repository: str | None,
    collection: str | None,
    min_score: int,
    limit: int,
    out_dir: str | None,
    model: str,
    with_web_search: bool,
    max_turns: int,
    max_budget_usd: float | None,
    max_thinking_tokens: int | None,
    permission_mode: str,
) -> None:
    from palimpsest.discovery.scout import run_candidate_scout, utc_now_slug

    if out_dir:
        resolved_out_dir = Path(out_dir).resolve()
    else:
        scope_bits = [
            repository.lower() if repository else "all",
            collection.lower() if collection else "all",
            utc_now_slug(),
        ]
        resolved_out_dir = (Path("discovery") / "scouts" / "_".join(scope_bits)).resolve()

    payload = asyncio.run(
        run_candidate_scout(
            db_path=Path(db_path).resolve(),
            out_dir=resolved_out_dir,
            repository=repository,
            collection=collection,
            min_score=min_score,
            limit=limit,
            model=model,
            with_web_search=with_web_search,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            max_thinking_tokens=max_thinking_tokens,
            permission_mode=permission_mode,
        )
    )

    print(f"candidates: {payload['candidate_count']}")
    print(f"memo: {payload['memo_path']}")
    print(f"meta: {payload['meta_path']}")
    print(f"bundle: {payload['candidates_path']}")
