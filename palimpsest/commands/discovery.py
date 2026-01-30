from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from palimpsest.library.iiif import extract_canvases


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")


def _merge_discovery(old: dict | None, new: dict | None) -> dict:
    merged: dict = {}
    if old:
        merged.update(old)
    if new:
        merged.update(new)

    def _pick_min(a: str | None, b: str | None) -> str | None:
        if a and b:
            return min(a, b)
        return a or b

    def _pick_max(a: str | None, b: str | None) -> str | None:
        if a and b:
            return max(a, b)
        return a or b

    first_seen = _pick_min(old.get("first_seen_at") if old else None, new.get("first_seen_at") if new else None)
    last_seen = _pick_max(old.get("last_seen_at") if old else None, new.get("last_seen_at") if new else None)
    if first_seen:
        merged["first_seen_at"] = first_seen
    if last_seen:
        merged["last_seen_at"] = last_seen
    return merged


def merge_records(existing: list[dict], new_records: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {r.get("manuscript_id") or r.get("id"): r for r in existing if r.get("manuscript_id") or r.get("id")}
    for record in new_records:
        mid = record.get("manuscript_id") or record.get("id")
        if not mid:
            continue
        if mid in by_id:
            merged = dict(by_id[mid])
            merged.update(record)
            merged["discovery"] = _merge_discovery(by_id[mid].get("discovery"), record.get("discovery"))
            by_id[mid] = merged
        else:
            by_id[mid] = record
    return list(by_id.values())


def parse_range(value: str) -> tuple[int, int]:
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError("range must be like 1000-1200")
    return int(parts[0]), int(parts[1])


def normalize_text(value: Any) -> str:
    if isinstance(value, dict):
        for vals in value.values():
            if vals:
                return vals[0] if isinstance(vals, list) else str(vals)
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def extract_metadata(manifest: dict) -> dict:
    metadata = {}
    for entry in manifest.get("metadata", []) or []:
        label = normalize_text(entry.get("label")).lower()
        value = normalize_text(entry.get("value"))
        if not label:
            continue
        metadata[label] = value

    title = normalize_text(manifest.get("label")) or metadata.get("title", "")
    author = metadata.get("author") or metadata.get("creator") or ""
    date = metadata.get("date") or metadata.get("dat") or ""
    language = metadata.get("language") or metadata.get("lingua") or ""
    return {
        "title": title,
        "author": author,
        "date": date,
        "language": language,
    }


def fetch_manifest(url: str, retries: int = 3, delay: float = 1.0) -> dict | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay * (2**attempt))
                continue
            if status == 404:
                return None
            raise exc
    return None


def build_record(collection: str, number: int, manifest_url: str, manifest: dict) -> dict:
    shelfmark = f"{collection}.{number}"
    manuscript_id = f"vat_{collection.lower().replace('.', '_')}_{number}"
    canvases = extract_canvases(manifest)
    content = extract_metadata(manifest)
    discovered_at = now_iso()
    return {
        "manuscript_id": manuscript_id,
        "shelfmark": shelfmark,
        "repository": "BAV",
        "collection": collection,
        "iiif": {
            "manifest_url": manifest_url,
            "viewer_url": f"https://digi.vatlib.it/view/MSS_{shelfmark}",
            "canvas_count": len(canvases),
        },
        "content": content,
        "discovery": {
            "discovered_date": discovered_at.split("T")[0],
            "first_seen_at": discovered_at,
            "last_seen_at": discovered_at,
            "discovered_by": "discovery_crawl.py",
            "method": "iiif_range",
        },
        "status": "discovered",
    }


def cmd_crawl(args: argparse.Namespace) -> None:
    start, end = parse_range(args.range_)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else None
    if manifest_dir:
        manifest_dir.mkdir(parents=True, exist_ok=True)

    existing = set()
    if args.append and output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("manuscript_id"))
            except json.JSONDecodeError:
                continue

    found = 0
    mode = "a" if args.append else "w"
    with output.open(mode, encoding="utf-8") as handle:
        for number in range(start, end + 1):
            if args.limit and found >= args.limit:
                break
            shelfmark = f"{args.collection}.{number}"
            manifest_url = f"https://digi.vatlib.it/iiif/MSS_{shelfmark}/manifest.json"
            manifest = fetch_manifest(manifest_url)
            if not manifest:
                continue
            record = build_record(args.collection, number, manifest_url, manifest)
            mid = record.get("manuscript_id")
            if args.append and mid in existing:
                continue

            if manifest_dir:
                out_path = manifest_dir / f"{mid}.json"
                out_path.write_text(json.dumps(manifest, ensure_ascii=True), encoding="utf-8")
                record["iiif"]["manifest_path"] = str(out_path.as_posix())

            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")
            found += 1
            if args.delay:
                time.sleep(args.delay)

    print(f"Wrote {found} records to {output}")

    if args.master:
        master_path = Path(args.master)
        master_records = _load_jsonl(master_path)
        by_id = {r.get("manuscript_id"): r for r in master_records if r.get("manuscript_id")}
        for line in output.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("manuscript_id")
            if mid:
                by_id[mid] = rec
        _write_jsonl(master_path, list(by_id.values()))
        print(f"Master now has {len(by_id)} records at {master_path}")


def cmd_filter(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    paths = sorted(input_dir.glob("*.jsonl"))
    records: list[dict] = []
    for path in paths:
        records.extend(_load_jsonl(path))

    by_id: dict[str, dict] = {}
    for record in records:
        mid = record.get("manuscript_id") or record.get("id")
        if mid:
            by_id[mid] = record

    filtered = list(by_id.values())

    if args.collection:
        prefix = args.collection
        pattern = re.compile(rf"{re.escape(prefix)}\.(\d+)", re.I)
        filtered = [r for r in filtered if pattern.search(r.get("shelfmark", "")) is not None]

    if args.range_:
        start, end = parse_range(args.range_)
        pattern = re.compile(r"(\d+)")
        subset = []
        for record in filtered:
            shelf = record.get("shelfmark", "")
            m = pattern.search(shelf)
            if not m:
                continue
            num = int(m.group(1))
            if start <= num <= end:
                subset.append(record)
        filtered = subset

    def _score(record: dict) -> float:
        return (
            record.get("discovery", {}).get("wtf_factor")
            or record.get("scholarship", {}).get("obscurity_score")
            or 0
        )

    if args.min_score:
        filtered = [r for r in filtered if _score(r) >= args.min_score]

    output = Path(args.output)
    existing = set()
    if args.append and output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("manuscript_id"))
            except json.JSONDecodeError:
                continue

    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    written = 0
    with output.open(mode, encoding="utf-8") as handle:
        for record in filtered:
            mid = record.get("manuscript_id")
            if args.append and mid in existing:
                continue
            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")
            written += 1

    print(f"Wrote {written} records to {output}")

    if args.master:
        master_path = Path(args.master)
        master_existing = _load_jsonl(master_path)
        by_id = {}
        for record in master_existing + filtered:
            mid = record.get("manuscript_id") or record.get("id")
            if mid:
                by_id[mid] = record
        _write_jsonl(master_path, list(by_id.values()))
        print(f"Master now has {len(by_id)} records at {master_path}")


def cmd_master_build(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    records: list[dict] = []
    for path in sorted(input_dir.glob("*.jsonl")):
        if path.name == Path(args.output).name:
            continue
        records.extend(_load_jsonl(path))
    merged = merge_records([], records)
    _write_jsonl(Path(args.output), merged)
    print(f"Wrote {len(merged)} records to {args.output}")


def cmd_master_append(args: argparse.Namespace) -> None:
    master_path = Path(args.output)
    master = _load_jsonl(master_path)
    incoming = _load_jsonl(Path(args.input))
    merged = merge_records(master, incoming)
    _write_jsonl(master_path, merged)
    print(f"Master now has {len(merged)} records")


def cmd_master_fetch(args: argparse.Namespace) -> None:
    master_path = Path(args.output)
    records = _load_jsonl(master_path)
    manifest_dir = Path(args.manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    fetched = 0
    for record in records:
        if args.limit and fetched >= args.limit:
            break
        mid = record.get("manuscript_id") or record.get("id")
        iiif = record.get("iiif") or {}
        manifest_url = iiif.get("manifest_url")
        if not mid or not manifest_url:
            continue
        out_path = manifest_dir / f"{mid}.json"
        if out_path.exists() and not args.overwrite:
            iiif["manifest_path"] = str(out_path.as_posix())
            record["iiif"] = iiif
        try:
            head = requests.head(manifest_url, timeout=30)
            if head.ok:
                iiif["manifest_last_modified"] = head.headers.get("Last-Modified")
                iiif["manifest_etag"] = head.headers.get("ETag")
                iiif["manifest_date_header"] = head.headers.get("Date")
        except Exception:
            pass
        if out_path.exists() and not args.overwrite:
            record["iiif"] = iiif
            continue
        try:
            resp = requests.get(manifest_url, timeout=30)
            resp.raise_for_status()
            out_path.write_text(resp.text, encoding="utf-8")
            iiif["manifest_path"] = str(out_path.as_posix())
            iiif["manifest_last_modified"] = resp.headers.get("Last-Modified")
            iiif["manifest_etag"] = resp.headers.get("ETag")
            iiif["manifest_date_header"] = resp.headers.get("Date")
            iiif.pop("manifest_error", None)
            record["iiif"] = iiif
            fetched += 1
        except Exception as exc:
            iiif["manifest_error"] = str(exc)
            record["iiif"] = iiif
        if args.delay:
            time.sleep(args.delay)

    _write_jsonl(master_path, records)
    print(f"Fetched {fetched} manifests into {manifest_dir}")


def cmd_master_sync_library(args: argparse.Namespace) -> None:
    master_path = Path(args.output)
    records = _load_jsonl(master_path)
    library_index = Path(args.library_root) / "index.jsonl"
    library_entries = _load_jsonl(library_index)

    by_manifest = {entry.get("manifest_url"): entry for entry in library_entries if entry.get("manifest_url")}
    updated = 0
    for record in records:
        iiif = record.get("iiif") or {}
        manifest_url = iiif.get("manifest_url")
        if not manifest_url:
            continue
        lib = by_manifest.get(manifest_url)
        if not lib:
            continue
        record["processing"] = {
            "doc_id": lib.get("doc_id"),
            "status": lib.get("status"),
            "updated_at": lib.get("updated_at"),
        }
        updated += 1

    _write_jsonl(master_path, records)
    print(f"Synced {updated} records from {library_index}")


def cmd_run(args: argparse.Namespace) -> None:
    cmd_crawl(args)
    cmd_master_sync_library(
        argparse.Namespace(
            output=args.master,
            library_root=args.library_root,
        )
    )


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("discovery", help="Discovery utilities")
    sub = parser.add_subparsers(dest="discovery_cmd", required=True)

    crawl = sub.add_parser("crawl", help="Crawl IIIF manifests by shelfmark range")
    crawl.add_argument("--collection", required=True, help="Shelfmark prefix (e.g., Pal.lat)")
    crawl.add_argument("--range", dest="range_", required=True, help="Numeric range like 1000-1200")
    crawl.add_argument("--output", required=True, help="Output JSONL file")
    crawl.add_argument("--append", action="store_true", help="Append to output")
    crawl.add_argument("--limit", type=int, help="Max found records to write")
    crawl.add_argument("--delay", type=float, default=0.5)
    crawl.add_argument("--manifest-dir", help="Cache manifests to this directory")
    crawl.add_argument("--master", help="Optional master JSONL to append into")
    crawl.set_defaults(func=cmd_crawl)

    filt = sub.add_parser("filter", help="Filter discovery registries")
    filt.add_argument("--input-dir", default="discovery/registry", help="Directory with JSONL registries")
    filt.add_argument("--collection", help="Shelfmark prefix (e.g., Pal.lat)")
    filt.add_argument("--range", dest="range_", help="Numeric range like 1000-1200")
    filt.add_argument("--min-score", type=float, default=0)
    filt.add_argument("--output", required=True, help="Output JSONL file")
    filt.add_argument("--append", action="store_true", help="Append new records")
    filt.add_argument("--master", help="Optional master JSONL to append into")
    filt.set_defaults(func=cmd_filter)

    master = sub.add_parser("master", help="Manage master discovery list")
    master_sub = master.add_subparsers(dest="master_cmd", required=True)

    m_build = master_sub.add_parser("build", help="Build master list from registry dir")
    m_build.add_argument("--input-dir", default="discovery/registry")
    m_build.add_argument("--output", default="discovery/registry/master.jsonl")
    m_build.set_defaults(func=cmd_master_build)

    m_append = master_sub.add_parser("append", help="Append a JSONL into master list")
    m_append.add_argument("--input", required=True)
    m_append.add_argument("--output", default="discovery/registry/master.jsonl")
    m_append.set_defaults(func=cmd_master_append)

    m_fetch = master_sub.add_parser("fetch-manifests", help="Download IIIF manifests locally")
    m_fetch.add_argument("--output", default="discovery/registry/master.jsonl")
    m_fetch.add_argument("--manifest-dir", default="discovery/manifests")
    m_fetch.add_argument("--limit", type=int)
    m_fetch.add_argument("--delay", type=float, default=0.25)
    m_fetch.add_argument("--overwrite", action="store_true")
    m_fetch.set_defaults(func=cmd_master_fetch)

    m_sync = master_sub.add_parser("sync-library", help="Sync processing status from library/index.jsonl")
    m_sync.add_argument("--output", default="discovery/registry/master.jsonl")
    m_sync.add_argument("--library-root", default="library")
    m_sync.set_defaults(func=cmd_master_sync_library)

    run = sub.add_parser("run", help="Crawl then sync library")
    run.add_argument("--collection", required=True, help="Shelfmark prefix (e.g., Pal.lat)")
    run.add_argument("--range", dest="range_", required=True, help="Numeric range like 1000-1200")
    run.add_argument("--limit", type=int, default=200, help="Max records to add")
    run.add_argument("--delay", type=float, default=0.5, help="Delay between manifest requests")
    run.add_argument("--output", required=True, help="Inventory JSONL output")
    run.add_argument("--master", default="discovery/registry/master.jsonl", help="Master JSONL")
    run.add_argument("--manifest-dir", default="discovery/manifests", help="Manifest cache dir")
    run.add_argument("--library-root", default="library", help="Library root for sync")
    run.set_defaults(func=cmd_run)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Discovery commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)

