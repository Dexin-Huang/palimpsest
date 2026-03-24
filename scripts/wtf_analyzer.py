#!/usr/bin/env python3
"""
WTF Analyzer - "What the Heck is This?" manuscript identifier.

Downloads the first page of a Vatican manuscript and uses Gemini
to identify its contents, language, date, and potential interest.

This helps find truly obscure/interesting manuscripts that metadata alone
won't reveal.

Usage:
    python scripts/wtf_analyzer.py --shelfmark Pal.lat.1265
    python scripts/wtf_analyzer.py --batch discovery/registry/pal_lat_1260-1280.jsonl --top 5
"""

import argparse
import base64
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load environment
PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

from google import genai
from google.genai import types

DIGI_VATLIB_BASE = "https://digi.vatlib.it"
IIIF_BASE = f"{DIGI_VATLIB_BASE}/iiif"

WTF_PROMPT = """You are examining the FIRST PAGE of a medieval manuscript from the Vatican Library.

Your task: Figure out WHAT THE HECK THIS IS.

Analyze this image and provide:

1. **LANGUAGE(S)**: What language(s) is this written in? (Latin, Greek, Hebrew, Arabic, vernacular, etc.)

2. **SCRIPT TYPE**: What type of handwriting? (Gothic textualis, Caroline minuscule, humanist, etc.)

3. **DATE ESTIMATE**: Based on script and layout, approximately when was this written?

4. **CONTENT TYPE**: What kind of text is this?
   - Religious (Bible, liturgy, theology, sermons)
   - Scientific (medicine, astronomy, alchemy, natural philosophy)
   - Literary (poetry, chronicles, romances)
   - Legal (canon law, civil law, charters)
   - Administrative (accounts, registers)
   - Other (describe)

5. **SPECIFIC IDENTIFICATION**: Can you identify what text this is?
   - Any title visible (incipit)?
   - Recognizable author or work?
   - Any clues from decoration, layout, or marginal notes?

6. **INTERESTING FEATURES**: Note anything unusual or noteworthy:
   - Diagrams, illustrations, tables?
   - Unusual abbreviations or symbols?
   - Multiple hands or later additions?
   - Damage, palimpsest traces, erasures?

7. **WTF SCORE** (1-10): How unusual/interesting/obscure is this?
   - 1-3: Common (another Bible, missal, or standard text)
   - 4-6: Moderately interesting (less common text, some unusual features)
   - 7-9: Quite unusual (rare text, strange content, mysterious features)
   - 10: "What the actual heck?!" (completely bizarre, unidentifiable, or potentially important discovery)

8. **RECOMMENDATION**: Should we transcribe this? Why or why not?

Be concise but specific. This is scholarly reconnaissance.
"""


def fetch_first_page_image(shelfmark: str, size: int = 1200) -> bytes | None:
    """Download the first content page of a manuscript (skipping covers)."""
    mss_id = f"MSS_{shelfmark}"
    manifest_url = f"{IIIF_BASE}/{mss_id}/manifest.json"

    try:
        # Fetch manifest
        resp = requests.get(manifest_url, timeout=30)
        if resp.status_code != 200:
            print(f"  Manifest not found: {resp.status_code}")
            return None

        manifest = resp.json()

        # Get first canvas
        sequences = manifest.get("sequences", [])
        if not sequences:
            print("  No sequences in manifest")
            return None

        canvases = sequences[0].get("canvases", [])
        if not canvases:
            print("  No canvases in manifest")
            return None

        # Skip cover pages and copyright splash pages
        # - Labels like "piatto.anteriore", "contropiatto", "risguardia" are covers
        # - Vatican uses "_cy_" in image URLs for copyright/splash pages
        # - Look for "_fa_" or "_f0" in image URLs for actual folio content
        skip_labels = ["piatto", "contropiatto", "risguardia", "dorso", "taglio", "guard"]

        first_canvas = None
        for i, canvas in enumerate(canvases):
            label = str(canvas.get("label", "")).lower()

            # Skip if any skip_label is in the canvas label
            if any(skip in label for skip in skip_labels):
                continue

            # Check the image service URL for content type markers
            try:
                service = canvas["images"][0]["resource"].get("service", {})
                service_id = service.get("@id", "").lower()

                # Skip copyright/splash pages (marked with _cy_ in URL)
                if "_cy_" in service_id:
                    continue

                # Prefer actual folio pages (marked with _fa_ or _f0 in URL)
                if "_fa_" in service_id or "_f0" in service_id:
                    first_canvas = canvas
                    print(f"  Using canvas {i}: {canvas.get('label', 'unknown')} (actual folio)")
                    break
            except (KeyError, IndexError):
                pass

            # Fallback: accept canvases with folio numbers in label (1r, 2v, etc.)
            if first_canvas is None and any(c.isdigit() for c in label):
                # But only if we're past the first few canvases (skip potential covers)
                if i >= 5:
                    first_canvas = canvas
                    print(f"  Using canvas {i}: {canvas.get('label', 'unknown')} (fallback)")
                    break

        if not first_canvas:
            # Last resort: skip first 8 canvases and take the next one
            first_canvas = canvases[min(8, len(canvases)-1)]
            print(f"  Fallback to canvas 8")
        images = first_canvas.get("images", [])
        if not images:
            print("  No images in first canvas")
            return None

        # Extract IIIF image service URL
        resource = images[0].get("resource", {})
        service = resource.get("service", {})
        service_id = service.get("@id", "")

        if not service_id:
            # Fallback to resource @id
            service_id = resource.get("@id", "")
            if service_id:
                # Convert full URL to service base
                service_id = service_id.rsplit("/full/", 1)[0]

        if not service_id:
            print("  Could not find image service URL")
            return None

        # Build IIIF image URL
        image_url = f"{service_id}/full/{size},/0/default.jpg"
        print(f"  Fetching: {image_url}")

        # Download image
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code == 200:
            return img_resp.content
        else:
            print(f"  Image download failed: {img_resp.status_code}")
            return None

    except Exception as e:
        print(f"  Error: {e}")
        return None


def analyze_manuscript(shelfmark: str, client: genai.Client, model: str) -> dict | None:
    """Analyze a manuscript's first page with Gemini."""
    print(f"\nAnalyzing {shelfmark}...")

    # Download first page
    image_data = fetch_first_page_image(shelfmark)
    if not image_data:
        return None

    print(f"  Downloaded {len(image_data):,} bytes")

    # Create image part for Gemini
    image_part = types.Part.from_bytes(
        data=image_data,
        mime_type="image/jpeg"
    )

    # Call Gemini
    print("  Sending to Gemini...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=[WTF_PROMPT, image_part],
            config=types.GenerateContentConfig(
                temperature=0.3,
            )
        )

        # Extract text
        if hasattr(response, 'text'):
            analysis = response.text
        else:
            # Handle non-text parts
            text_parts = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'text'):
                        text_parts.append(part.text)
            analysis = "\n".join(text_parts)

        return {
            "shelfmark": shelfmark,
            "analyzed_at": datetime.now().isoformat(),
            "analysis": analysis,
            "image_size": len(image_data),
        }

    except Exception as e:
        print(f"  Gemini error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="WTF Analyzer for Vatican manuscripts")
    parser.add_argument("--shelfmark", help="Single manuscript to analyze (e.g., Pal.lat.1265)")
    parser.add_argument("--batch", help="JSONL file with discovery records")
    parser.add_argument("--top", type=int, default=5, help="Analyze top N from batch")
    parser.add_argument("--model", default="gemini-3-flash-preview", help="Gemini model")
    parser.add_argument("--output", help="Output file for results (JSONL)")

    args = parser.parse_args()

    # Initialize Gemini client
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    results = []

    if args.shelfmark:
        # Single manuscript
        result = analyze_manuscript(args.shelfmark, client, args.model)
        if result:
            results.append(result)
            print(f"\n{'='*60}")
            print(f"ANALYSIS: {args.shelfmark}")
            print(f"{'='*60}")
            print(result["analysis"])

    elif args.batch:
        # Batch from discovery file
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"Error: {args.batch} not found", file=sys.stderr)
            sys.exit(1)

        # Load discoveries
        discoveries = []
        with open(batch_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    discoveries.append(json.loads(line))

        # Sort by obscurity score if available
        discoveries.sort(
            key=lambda x: x.get("scholarship", {}).get("obscurity_score", 5),
            reverse=True
        )

        # Analyze top N
        print(f"Analyzing top {args.top} manuscripts from {batch_path.name}...")
        for disc in discoveries[:args.top]:
            shelfmark = disc.get("shelfmark")
            if shelfmark:
                result = analyze_manuscript(shelfmark, client, args.model)
                if result:
                    results.append(result)
                    print(f"\n{'='*60}")
                    print(f"ANALYSIS: {shelfmark}")
                    print(f"{'='*60}")
                    print(result["analysis"])
                    print()

    else:
        parser.print_help()
        return

    # Save results if output specified
    if args.output and results:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

        print(f"\nSaved {len(results)} analyses to {args.output}")


if __name__ == "__main__":
    main()
