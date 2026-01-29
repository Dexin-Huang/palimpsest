#!/usr/bin/env python3
"""
Explore Vatican Digital Library to find obscure/unstudied manuscripts.

This script crawls Vatican IIIF manifests and catalogs to identify
manuscripts that might be interesting and understudied.

Collections of interest:
- Palatini latini (Pal.lat.) - German Palatinate library, many medieval scientific texts
- Vaticani latini (Vat.lat.) - Main Vatican Latin collection
- Borghesiani (Borgh.) - Borghese family collection
- Reginenses latini (Reg.lat.) - Queen Christina of Sweden's collection
- Ottoboniani latini (Ott.lat.) - Ottoboni family collection
- Chigiani (Chig.) - Chigi family collection

Usage:
    python scripts/explore_vatican.py --collection "Pal.lat" --range 1200-1400
    python scripts/explore_vatican.py --search "alchemia"
    python scripts/explore_vatican.py --random 10
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import time

import requests

# Vatican IIIF base URLs
DIGI_VATLIB_BASE = "https://digi.vatlib.it"
IIIF_BASE = f"{DIGI_VATLIB_BASE}/iiif"

# Known collection prefixes
COLLECTIONS = {
    "Pal.lat": "Palatini latini",
    "Vat.lat": "Vaticani latini",
    "Borgh": "Borghesiani",
    "Reg.lat": "Reginenses latini",
    "Ott.lat": "Ottoboniani latini",
    "Chig": "Chigiani",
    "Barb.lat": "Barberiniani latini",
    "Urb.lat": "Urbinates latini",
    "Ross": "Rossiani",
    "Cappon": "Capponiani",
}

# Subject keywords that might indicate interesting/obscure content
INTERESTING_SUBJECTS = [
    "alchemia", "alchemy", "magia", "magic",
    "astronomia", "astrologia", "astrology",
    "medicina", "medicine", "chirurgia",
    "herbarium", "botanica",
    "secretis", "secrets", "arcana",
    "cabala", "kabbalah",
    "geomantia", "chiromantia",
    "lapidarium", "mineralogy",
    "bestiari", "bestiary",
    "experimenta", "experiments",
    "cryptograph", "cipher",
    "automatica", "mechanica",
]


def fetch_manifest(shelfmark: str) -> Optional[dict]:
    """Fetch IIIF manifest for a manuscript."""
    # Convert shelfmark to URL format: Pal.lat.1267 -> MSS_Pal.lat.1267
    mss_id = f"MSS_{shelfmark}"
    url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"  Error fetching {shelfmark}: {e}", file=sys.stderr)
        return None


def check_manuscript_exists(shelfmark: str) -> bool:
    """Quick check if manuscript has been digitized."""
    mss_id = f"MSS_{shelfmark}"
    url = f"{DIGI_VATLIB_BASE}/view/{mss_id}"

    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        return resp.status_code == 200
    except:
        return False


def extract_manifest_info(manifest: dict) -> dict:
    """Extract key info from IIIF manifest."""
    info = {
        "label": manifest.get("label", "Unknown"),
        "description": manifest.get("description", ""),
        "attribution": manifest.get("attribution", ""),
        "canvas_count": 0,
        "metadata": {}
    }

    # Count canvases (pages)
    sequences = manifest.get("sequences", [])
    if sequences:
        canvases = sequences[0].get("canvases", [])
        info["canvas_count"] = len(canvases)

    # Extract metadata
    for item in manifest.get("metadata", []):
        label = item.get("label", "")
        value = item.get("value", "")
        if label and value:
            info["metadata"][label] = value

    return info


def score_obscurity(info: dict) -> int:
    """
    Score how obscure/interesting a manuscript might be.
    Higher = more obscure/interesting.
    """
    score = 5  # Base score

    description = str(info.get("description", "")).lower()
    metadata_str = json.dumps(info.get("metadata", {})).lower()
    combined = description + " " + metadata_str

    # Bonus for interesting subjects
    for subject in INTERESTING_SUBJECTS:
        if subject in combined:
            score += 1

    # Bonus for small collections (less studied)
    canvas_count = info.get("canvas_count", 0)
    if 10 <= canvas_count <= 50:
        score += 1  # Sweet spot - enough content, not huge

    # Penalty for very common subjects
    common_subjects = ["biblia", "liturgic", "evangeliar", "missale", "breviarium"]
    for subject in common_subjects:
        if subject in combined:
            score -= 2

    return max(1, min(10, score))


def scan_collection_range(prefix: str, start: int, end: int, verbose: bool = False) -> list:
    """Scan a range of manuscript numbers in a collection."""
    results = []

    print(f"Scanning {prefix}.{start}-{end}...")

    for num in range(start, end + 1):
        shelfmark = f"{prefix}.{num}"

        if verbose:
            print(f"  Checking {shelfmark}...", end=" ", flush=True)

        manifest = fetch_manifest(shelfmark)
        if manifest:
            info = extract_manifest_info(manifest)
            obscurity = score_obscurity(info)

            result = {
                "shelfmark": shelfmark,
                "label": info["label"],
                "canvas_count": info["canvas_count"],
                "obscurity_score": obscurity,
                "metadata": info["metadata"],
            }
            results.append(result)

            if verbose:
                print(f"FOUND ({info['canvas_count']} pages, obscurity={obscurity})")

            # Rate limiting
            time.sleep(0.5)
        else:
            if verbose:
                print("not digitized")

    return results


def create_discovery_record(shelfmark: str, info: dict, method: str = "iiif_crawl") -> dict:
    """Create a discovery record for the registry."""
    prefix = shelfmark.rsplit(".", 1)[0]
    collection = COLLECTIONS.get(prefix, prefix)

    return {
        "manuscript_id": f"vat_{shelfmark.lower().replace('.', '_')}",
        "shelfmark": shelfmark,
        "repository": "BAV",
        "collection": collection,
        "iiif": {
            "manifest_url": f"{IIIF_BASE}/MSS_{shelfmark}/manifest.json",
            "viewer_url": f"{DIGI_VATLIB_BASE}/view/MSS_{shelfmark}",
            "canvas_count": info.get("canvas_count", 0),
        },
        "content": {
            "catalog_description": info.get("label", ""),
        },
        "scholarship": {
            "obscurity_score": info.get("obscurity_score", 5),
        },
        "discovery": {
            "discovered_date": datetime.now().strftime("%Y-%m-%d"),
            "discovered_by": "explore_vatican.py",
            "method": method,
            "initial_interest": "Automated discovery via IIIF scan",
            "wtf_factor": info.get("obscurity_score", 5),
        },
        "status": "discovered",
        "audit_log": [{
            "timestamp": datetime.now().isoformat() + "Z",
            "action": "discovered",
            "agent": "explore_vatican.py",
            "details": f"Found via {method}",
        }],
    }


def main():
    parser = argparse.ArgumentParser(description="Explore Vatican manuscripts")
    parser.add_argument("--collection", help="Collection prefix (e.g., Pal.lat)")
    parser.add_argument("--range", help="Number range (e.g., 1200-1400)")
    parser.add_argument("--check", help="Check single shelfmark")
    parser.add_argument("--top", type=int, default=10, help="Show top N most interesting")
    parser.add_argument("--output", help="Output file for discoveries (JSONL)")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.check:
        # Single manuscript check
        print(f"Checking {args.check}...")
        manifest = fetch_manifest(args.check)
        if manifest:
            info = extract_manifest_info(manifest)
            info["obscurity_score"] = score_obscurity(info)
            print(json.dumps(info, indent=2))
        else:
            print("Not found or not digitized")
        return

    if args.collection and args.range:
        # Scan collection range
        start, end = map(int, args.range.split("-"))
        results = scan_collection_range(args.collection, start, end, args.verbose)

        # Sort by obscurity score
        results.sort(key=lambda x: x["obscurity_score"], reverse=True)

        print(f"\n{'='*60}")
        print(f"Found {len(results)} digitized manuscripts")
        print(f"Top {args.top} most interesting:")
        print(f"{'='*60}\n")

        for i, r in enumerate(results[:args.top], 1):
            print(f"{i}. {r['shelfmark']} (obscurity={r['obscurity_score']}, {r['canvas_count']} pages)")
            print(f"   {r['label']}")
            print()

        # Save discoveries if output specified
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "a", encoding="utf-8") as f:
                for r in results:
                    record = create_discovery_record(r["shelfmark"], r)
                    f.write(json.dumps(record) + "\n")

            print(f"Saved {len(results)} discoveries to {args.output}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
