#!/usr/bin/env python3
"""
Vatican Metadata Scanner - Find interesting manuscripts via metadata only.

Scans Vatican IIIF manifests for metadata without downloading images.
Uses keyword matching, date ranges, and collection patterns to identify
potentially interesting/obscure manuscripts.

NO API COSTS - purely HTTP requests to Vatican's public IIIF endpoints.

Usage:
    python scripts/scan_vatican_metadata.py --collection "Pal.lat" --range 1-500
    python scripts/scan_vatican_metadata.py --collection "Pal.lat" --range 1200-1400 --keywords "alchemia,magia"
    python scripts/scan_vatican_metadata.py --random-sample 100
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

DIGI_VATLIB_BASE = "https://digi.vatlib.it"
IIIF_BASE = f"{DIGI_VATLIB_BASE}/iiif"

# Collections in the Vatican Library
COLLECTIONS = {
    "Pal.lat": {"name": "Palatini latini", "range": (1, 2000), "desc": "German Palatinate library - many medieval scientific texts"},
    "Vat.lat": {"name": "Vaticani latini", "range": (1, 15000), "desc": "Main Vatican Latin collection"},
    "Borgh": {"name": "Borghesiani", "range": (1, 500), "desc": "Borghese family collection"},
    "Reg.lat": {"name": "Reginenses latini", "range": (1, 2100), "desc": "Queen Christina of Sweden's collection"},
    "Ott.lat": {"name": "Ottoboniani latini", "range": (1, 3500), "desc": "Ottoboni family collection"},
    "Chig": {"name": "Chigiani", "range": (1, 800), "desc": "Chigi family collection"},
    "Barb.lat": {"name": "Barberiniani latini", "range": (1, 4000), "desc": "Barberini family collection"},
    "Urb.lat": {"name": "Urbinates latini", "range": (1, 1800), "desc": "Urbino ducal library"},
    "Ross": {"name": "Rossiani", "range": (1, 1200), "desc": "Rossi collection"},
    "Vat.gr": {"name": "Vaticani graeci", "range": (1, 2400), "desc": "Greek manuscripts"},
    "Pal.gr": {"name": "Palatini graeci", "range": (1, 450), "desc": "Palatine Greek collection"},
    "Vat.ebr": {"name": "Vaticani ebraici", "range": (1, 700), "desc": "Hebrew manuscripts"},
    "Vat.ar": {"name": "Vaticani arabici", "range": (1, 1700), "desc": "Arabic manuscripts"},
}

# Keywords that indicate potentially interesting content
INTERESTING_KEYWORDS = {
    # Alchemy & Chemistry
    "alchim": 8, "alquim": 8, "lapis philosophorum": 10, "transmutatio": 9,
    "elixir": 8, "aqua vitae": 7, "distillatio": 6,

    # Magic & Occult
    "magi": 7, "necromant": 9, "daemon": 8, "incantation": 8,
    "talismanic": 8, "astrolog": 6, "geomant": 7, "chiromant": 7,

    # Secret Knowledge
    "secret": 6, "arcana": 7, "occult": 7, "mysterium": 6,
    "cabala": 8, "kabbal": 8, "hermet": 7,

    # Science
    "astronomia": 5, "cosmograph": 5, "mathemat": 4,
    "medicin": 4, "chirurg": 5, "anatom": 5,
    "natural": 4, "experiment": 6, "optic": 5,

    # Unusual
    "monstru": 7, "mirabili": 6, "prodig": 6,
    "prophetia": 6, "apocalyp": 5, "visio": 5,
    "autograph": 6, "original": 5,

    # Languages (rare ones score higher)
    "arabic": 5, "hebraic": 5, "graec": 3,
    "coptic": 7, "syriac": 7, "ethiop": 8,
    "armenian": 6, "persian": 6,

    # Historical figures
    "albertus magnus": 6, "roger bacon": 7, "raimundus lullus": 7,
    "arnaldus": 5, "avicenna": 4, "geber": 7,
}

# Common/boring keywords (reduce score)
BORING_KEYWORDS = {
    "biblia": -3, "missale": -3, "breviarium": -3,
    "psalterium": -2, "evangeliar": -2, "liturgic": -2,
    "homilia": -2, "sermones": -1, "epistola": -1,
    "graduale": -2, "antiphon": -2,
}


def fetch_manifest_metadata(shelfmark: str, timeout: int = 15, retries: int = 3) -> Optional[dict]:
    """Fetch just the metadata from a manifest (no images)."""
    mss_id = f"MSS_{shelfmark}"
    url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                manifest = resp.json()
                return extract_metadata(manifest, shelfmark)
            elif resp.status_code == 429:
                # Rate limited - wait and retry
                time.sleep(2 ** attempt)
                continue
            elif resp.status_code == 404:
                return None  # Not digitized
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
            continue
    return None


def extract_metadata(manifest: dict, shelfmark: str) -> dict:
    """Extract relevant metadata from manifest."""
    metadata = {
        "shelfmark": shelfmark,
        "label": manifest.get("label", ""),
        "description": manifest.get("description", ""),
        "attribution": manifest.get("attribution", ""),
        "canvas_count": 0,
        "fields": {},
    }

    # Count canvases
    sequences = manifest.get("sequences", [])
    if sequences:
        metadata["canvas_count"] = len(sequences[0].get("canvases", []))

    # Extract metadata fields
    for item in manifest.get("metadata", []):
        label = item.get("label", "")
        value = item.get("value", "")
        if label and value:
            # Handle list values
            if isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            metadata["fields"][label] = str(value)

    return metadata


def score_metadata(metadata: dict) -> dict:
    """Score manuscript based on metadata content."""
    score = 5  # Base score
    reasons = []

    # Combine all text fields for keyword matching
    all_text = " ".join([
        metadata.get("label", ""),
        metadata.get("description", ""),
        " ".join(str(v) for v in metadata.get("fields", {}).values())
    ]).lower()

    # Check interesting keywords
    for keyword, points in INTERESTING_KEYWORDS.items():
        if keyword.lower() in all_text:
            score += points
            reasons.append(f"+{points}: {keyword}")

    # Check boring keywords
    for keyword, points in BORING_KEYWORDS.items():
        if keyword.lower() in all_text:
            score += points  # points are negative
            reasons.append(f"{points}: {keyword}")

    # Bonus for unusual page counts (very small or medium-sized)
    canvas_count = metadata.get("canvas_count", 0)
    if 20 <= canvas_count <= 100:
        score += 1
        reasons.append("+1: good size (20-100 pages)")
    elif canvas_count < 20 and canvas_count > 0:
        score += 2
        reasons.append("+2: small manuscript")

    # Bonus for multiple authors (compilations)
    authors = metadata.get("fields", {}).get("Author", "")
    if isinstance(authors, list) and len(authors) > 2:
        score += 1
        reasons.append("+1: multiple authors")

    # Cap score
    score = max(1, min(10, score))

    return {
        "score": score,
        "reasons": reasons,
    }


def scan_collection_range(
    prefix: str,
    start: int,
    end: int,
    keywords: list = None,
    parallel: int = 10,
    verbose: bool = False
) -> list:
    """Scan a range of manuscripts in parallel."""
    results = []
    shelfmarks = [f"{prefix}.{n}" for n in range(start, end + 1)]

    print(f"Scanning {len(shelfmarks)} manuscripts in {prefix}...")

    found = 0
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {
            executor.submit(fetch_manifest_metadata, sm): sm
            for sm in shelfmarks
        }

        for future in as_completed(futures):
            shelfmark = futures[future]
            try:
                metadata = future.result()
                if metadata:
                    found += 1
                    scoring = score_metadata(metadata)
                    metadata["interest_score"] = scoring["score"]
                    metadata["score_reasons"] = scoring["reasons"]

                    # Filter by keywords if specified
                    if keywords:
                        all_text = json.dumps(metadata).lower()
                        if not any(kw.lower() in all_text for kw in keywords):
                            continue

                    results.append(metadata)

                    if verbose:
                        print(f"  {shelfmark}: score={scoring['score']}, pages={metadata['canvas_count']}")

            except Exception as e:
                if verbose:
                    print(f"  {shelfmark}: error - {e}")

    print(f"Found {found} digitized, {len(results)} matching criteria")
    return results


def create_discovery_record(metadata: dict) -> dict:
    """Convert metadata to discovery registry format."""
    prefix = metadata["shelfmark"].rsplit(".", 1)[0]
    collection_info = COLLECTIONS.get(prefix, {"name": prefix})

    return {
        "manuscript_id": f"vat_{metadata['shelfmark'].lower().replace('.', '_')}",
        "shelfmark": metadata["shelfmark"],
        "repository": "BAV",
        "collection": collection_info.get("name", prefix),
        "iiif": {
            "manifest_url": f"{IIIF_BASE}/MSS_{metadata['shelfmark']}/manifest.json",
            "viewer_url": f"{DIGI_VATLIB_BASE}/view/MSS_{metadata['shelfmark']}",
            "canvas_count": metadata.get("canvas_count", 0),
        },
        "content": {
            "title": metadata.get("fields", {}).get("Title", metadata.get("label", "")),
            "author": metadata.get("fields", {}).get("Author", ""),
            "date": metadata.get("fields", {}).get("Date", ""),
            "language": metadata.get("fields", {}).get("Language", ""),
        },
        "scholarship": {
            "obscurity_score": metadata.get("interest_score", 5),
            "score_reasons": metadata.get("score_reasons", []),
        },
        "discovery": {
            "discovered_date": datetime.now().strftime("%Y-%m-%d"),
            "discovered_by": "scan_vatican_metadata.py",
            "method": "iiif_crawl",
            "wtf_factor": metadata.get("interest_score", 5),
        },
        "status": "discovered",
    }


def main():
    parser = argparse.ArgumentParser(description="Scan Vatican metadata for interesting manuscripts")
    parser.add_argument("--collection", help="Collection prefix (e.g., Pal.lat)")
    parser.add_argument("--range", help="Number range (e.g., 1-500)")
    parser.add_argument("--keywords", help="Comma-separated keywords to filter by")
    parser.add_argument("--min-score", type=int, default=6, help="Minimum interest score (default: 6)")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    parser.add_argument("--parallel", type=int, default=3, help="Parallel requests (keep low to avoid rate limits)")
    parser.add_argument("--output", help="Output JSONL file")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--list-collections", action="store_true", help="List available collections")

    args = parser.parse_args()

    if args.list_collections:
        print("Available Vatican collections:")
        print("-" * 60)
        for prefix, info in sorted(COLLECTIONS.items()):
            print(f"  {prefix:12} {info['name']}")
            print(f"              Range: {info['range'][0]}-{info['range'][1]}")
            print(f"              {info['desc']}")
            print()
        return

    if not args.collection or not args.range:
        parser.print_help()
        return

    # Parse range
    start, end = map(int, args.range.split("-"))

    # Parse keywords
    keywords = args.keywords.split(",") if args.keywords else None

    # Scan
    results = scan_collection_range(
        args.collection, start, end,
        keywords=keywords,
        parallel=args.parallel,
        verbose=args.verbose
    )

    # Filter by minimum score
    results = [r for r in results if r.get("interest_score", 0) >= args.min_score]

    # Sort by score
    results.sort(key=lambda x: x.get("interest_score", 0), reverse=True)

    # Print top results
    print(f"\n{'='*70}")
    print(f"Top {min(args.top, len(results))} most interesting manuscripts (score >= {args.min_score}):")
    print(f"{'='*70}\n")

    for i, r in enumerate(results[:args.top], 1):
        score = r.get("interest_score", 0)
        pages = r.get("canvas_count", 0)
        title = r.get("fields", {}).get("Title", r.get("label", ""))[:60]
        author = r.get("fields", {}).get("Author", "")[:40]

        print(f"{i:2}. {r['shelfmark']:<15} Score: {score}/10  Pages: {pages}")
        print(f"    Title: {title.encode('ascii', 'replace').decode()}")
        if author:
            print(f"    Author: {author.encode('ascii', 'replace').decode()}")
        reasons = r.get("score_reasons", [])[:3]
        if reasons:
            print(f"    Why: {', '.join(reasons)}")
        print()

    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for r in results:
                record = create_discovery_record(r)
                f.write(json.dumps(record) + "\n")

        print(f"Saved {len(results)} discoveries to {args.output}")


if __name__ == "__main__":
    main()
