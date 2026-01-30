#!/usr/bin/env python3
"""Download manuscript pages from a IIIF manifest.

Usage:
  python scripts/download_iiif.py \
    --manifest "https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json" \
    --out-dir projects/vatican_alchemy/images/ \
    --max-pages 10 \
    --size 1200

Supports IIIF Presentation API 2.x and 3.0 manifests.
"""
import argparse
import sys
import time
from pathlib import Path

from palimpsest.library.iiif import build_image_url, derive_filename, extract_canvases, fetch_manifest


def download_manifest(
    manifest_url: str,
    output_dir: Path,
    max_pages: int = None,
    size: int | str = 1200,
    start: int = 0,
    delay: float = 0.5,
) -> list:
    """Download all pages from a IIIF manifest.

    Args:
        manifest_url: URL to IIIF manifest
        output_dir: Directory to save images
        max_pages: Maximum number of pages to download (None = all)
        size: Target image width in pixels, or "max"/"full" for maximum
        start: Starting page index (0-based)
        delay: Delay between downloads in seconds

    Returns:
        List of downloaded file paths
    """
    # Fetch manifest
    print(f"Fetching manifest: {manifest_url}")
    manifest = fetch_manifest(manifest_url)

    # Extract canvas info
    canvases = extract_canvases(manifest)
    if not canvases:
        raise ValueError("No canvases found in manifest")

    print(f"Found {len(canvases)} canvases")

    # Apply start/max limits
    canvases = canvases[start:]
    if max_pages:
        canvases = canvases[:max_pages]

    print(f"Downloading {len(canvases)} pages (size={size}px)")

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for i, canvas in enumerate(canvases):
        # Build image URL
        image_url = build_image_url(canvas["image_service"], size)

        # Derive filename
        filename = derive_filename(start + i, canvas.get("label", ""))
        out_path = output_dir / filename

        # Skip if already exists
        if out_path.exists():
            print(f"  [{i+1}/{len(canvases)}] {filename} (exists, skipping)")
            downloaded.append(out_path)
            continue

        # Download
        print(f"  [{i+1}/{len(canvases)}] {filename}...", end=" ", flush=True)
        try:
            resp = requests.get(image_url, timeout=60)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
            print(f"OK ({len(resp.content) // 1024} KB)")
            downloaded.append(out_path)
        except requests.RequestException as e:
            print(f"FAILED: {e}")

        # Rate limit
        if delay > 0 and i < len(canvases) - 1:
            time.sleep(delay)

    return downloaded


def main():
    parser = argparse.ArgumentParser(
        description="Download manuscript pages from a IIIF manifest"
    )
    parser.add_argument(
        "--manifest", required=True, help="IIIF manifest URL"
    )
    parser.add_argument(
        "--out-dir", required=True, help="Output directory for images"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Maximum number of pages to download",
    )
    parser.add_argument(
        "--size",
        default="1200",
        help="Target image width in pixels, or 'max' for full resolution (default: 1200)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Starting page index (0-based, default: 0)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between downloads in seconds (default: 0.5)",
    )

    args = parser.parse_args()

    # Parse size - can be int or "max"/"full"
    size = args.size
    if size.lower() not in ("max", "full"):
        try:
            size = int(size)
        except ValueError:
            print(f"Error: --size must be an integer or 'max', got '{size}'")
            sys.exit(1)

    try:
        downloaded = download_manifest(
            manifest_url=args.manifest,
            output_dir=Path(args.out_dir),
            max_pages=args.max_pages,
            size=size,
            start=args.start,
            delay=args.delay,
        )
        print(f"\n[DONE] Downloaded {len(downloaded)} pages to {args.out_dir}")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
