#!/usr/bin/env python3
"""
Dump Vatican manuscript metadata to CSV for bulk analysis.

Scans IIIF manifests and exports all metadata to CSV format.
Then we can have AI analyze the text descriptions to find obscure ones.

Usage:
    python scripts/dump_vatican_csv.py --collection "Pal.lat" --range 1-2000 --output discovery/pal_lat_all.csv
"""

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

IIIF_BASE = "https://digi.vatlib.it/iiif"


def fetch_manifest(shelfmark: str, timeout: int = 15, retries: int = 2) -> dict | None:
    """Fetch manifest metadata."""
    mss_id = f"MSS_{shelfmark}"
    url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
            continue
    return None


def extract_row(manifest: dict, shelfmark: str) -> dict:
    """Extract a flat row of metadata."""
    row = {
        "shelfmark": shelfmark,
        "label": manifest.get("label", ""),
        "description": manifest.get("description", ""),
        "pages": 0,
        "title": "",
        "author": "",
        "date": "",
        "language": "",
        "subject": "",
        "other_metadata": "",
    }

    # Count pages
    sequences = manifest.get("sequences", [])
    if sequences:
        row["pages"] = len(sequences[0].get("canvases", []))

    # Extract metadata fields
    other = []
    for item in manifest.get("metadata", []):
        label = str(item.get("label", "")).strip()
        value = item.get("value", "")
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value)
        value = str(value).strip()

        label_lower = label.lower()
        if "title" in label_lower or "titolo" in label_lower:
            row["title"] = value
        elif "author" in label_lower or "autore" in label_lower:
            row["author"] = value
        elif "date" in label_lower or "data" in label_lower:
            row["date"] = value
        elif "language" in label_lower or "lingua" in label_lower:
            row["language"] = value
        elif "subject" in label_lower or "soggetto" in label_lower:
            row["subject"] = value
        elif label and value:
            other.append(f"{label}: {value}")

    row["other_metadata"] = " | ".join(other)

    return row


def scan_to_csv(prefix: str, start: int, end: int, output_path: Path, parallel: int = 3):
    """Scan manuscripts and write to CSV incrementally (crash-safe)."""
    shelfmarks = [f"{prefix}.{n}" for n in range(start, end + 1)]

    print(f"Scanning {len(shelfmarks)} manuscripts...")

    # Write header and rows incrementally
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["shelfmark", "pages", "label", "title", "author", "date", "language", "subject", "description", "other_metadata"]

    found = 0
    errors = 0

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {executor.submit(fetch_manifest, sm): sm for sm in shelfmarks}

            for i, future in enumerate(as_completed(futures)):
                shelfmark = futures[future]
                try:
                    manifest = future.result()
                    if manifest:
                        row = extract_row(manifest, shelfmark)
                        writer.writerow(row)
                        f.flush()  # Write immediately
                        found += 1
                except Exception as e:
                    errors += 1

                # Progress
                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i+1}/{len(shelfmarks)} checked, {found} found")

    print(f"\nFound {found} digitized manuscripts, {errors} errors")
    print(f"Wrote {found} rows to {output_path}")
    return found


def main():
    parser = argparse.ArgumentParser(description="Dump Vatican metadata to CSV")
    parser.add_argument("--collection", required=True, help="Collection prefix (e.g., Pal.lat)")
    parser.add_argument("--range", required=True, help="Number range (e.g., 1-2000)")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument("--parallel", type=int, default=3, help="Parallel requests")

    args = parser.parse_args()

    start, end = map(int, args.range.split("-"))
    output_path = Path(args.output)

    scan_to_csv(args.collection, start, end, output_path, args.parallel)


if __name__ == "__main__":
    main()
