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
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests


def get_manifest(url: str) -> dict:
    """Fetch and parse IIIF manifest."""
    print(f"Fetching manifest: {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_canvases_v2(manifest: dict) -> list:
    """Extract canvas info from IIIF 2.x manifest."""
    canvases = []
    for sequence in manifest.get("sequences", []):
        for canvas in sequence.get("canvases", []):
            # Get image resource
            for image in canvas.get("images", []):
                resource = image.get("resource", {})
                service = resource.get("service", {})

                # Get base IIIF image URL
                image_id = service.get("@id", "")
                if not image_id:
                    # Try direct resource URL
                    image_id = resource.get("@id", "")

                if image_id:
                    canvases.append({
                        "id": canvas.get("@id", ""),
                        "label": canvas.get("label", ""),
                        "image_service": image_id,
                        "width": resource.get("width", canvas.get("width")),
                        "height": resource.get("height", canvas.get("height")),
                    })
                    break  # One image per canvas
    return canvases


def extract_canvases_v3(manifest: dict) -> list:
    """Extract canvas info from IIIF 3.0 manifest."""
    canvases = []
    for item in manifest.get("items", []):
        if item.get("type") != "Canvas":
            continue

        for anno_page in item.get("items", []):
            for anno in anno_page.get("items", []):
                body = anno.get("body", {})
                if isinstance(body, list):
                    body = body[0] if body else {}

                service = body.get("service", [])
                if isinstance(service, list) and service:
                    service = service[0]

                image_id = ""
                if isinstance(service, dict):
                    image_id = service.get("id", service.get("@id", ""))
                if not image_id:
                    image_id = body.get("id", "")

                if image_id:
                    # Get label
                    label = item.get("label", {})
                    if isinstance(label, dict):
                        # IIIF 3.0 label format: {"en": ["Folio 1r"]}
                        for vals in label.values():
                            if vals:
                                label = vals[0] if isinstance(vals, list) else vals
                                break
                        else:
                            label = ""

                    canvases.append({
                        "id": item.get("id", ""),
                        "label": label,
                        "image_service": image_id,
                        "width": item.get("width"),
                        "height": item.get("height"),
                    })
                    break
    return canvases


def extract_canvases(manifest: dict) -> list:
    """Extract canvas info from IIIF manifest (auto-detect version)."""
    # Check for IIIF 3.0 context
    context = manifest.get("@context", "")
    if isinstance(context, list):
        context = " ".join(str(c) for c in context)

    if "iiif.io/api/presentation/3" in str(context) or "items" in manifest:
        return extract_canvases_v3(manifest)
    else:
        return extract_canvases_v2(manifest)


def build_image_url(service_url: str, size: int | str) -> str:
    """Build IIIF image URL for download.

    Constructs URL in format: {service}/full/{size},/0/default.jpg

    Args:
        service_url: IIIF image service URL
        size: Width in pixels, or "max"/"full" for maximum resolution
    """
    # Ensure no trailing slash
    base = service_url.rstrip("/")

    # Handle "max" or "full" for maximum resolution
    if isinstance(size, str) and size.lower() in ("max", "full"):
        size_param = "max"
    else:
        size_param = f"{size},"

    # If it's already a full image URL, try to modify it
    if "/full/" in base:
        # Replace size parameter
        return re.sub(r"/full/[^/]+/", f"/full/{size_param}/", base)

    # Build standard IIIF image URL
    return f"{base}/full/{size_param}/0/default.jpg"


def derive_filename(index: int, label: str) -> str:
    """Derive filename from canvas index and label."""
    # Try to extract folio number from label
    if label:
        # Match patterns like "f. 1r", "Folio 1v", "1r", etc.
        m = re.search(r"(?:f(?:ol(?:io)?)?\.?\s*)?(\d+)\s*([rv])?", str(label), re.I)
        if m:
            num = int(m.group(1))
            side = (m.group(2) or "r").lower()
            return f"f{num:03d}{side}.jpg"

    # Fallback to simple numbering
    return f"page_{index:04d}.jpg"


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
    manifest = get_manifest(manifest_url)

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
