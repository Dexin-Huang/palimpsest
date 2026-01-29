#!/usr/bin/env python3
"""Run Gemini OCR on a single manuscript page.

Usage:
  python scripts/ocr_page.py \
    --image projects/vatican_alchemy/images/pal_lat_1267_f020r.jpg \
    --doc-id pal_lat_1267 \
    --page-id f020r \
    --out projects/vatican_alchemy/pages/pal_lat_1267_f020r.page.json \
    --prompt alchemical_text

Requires GEMINI_API_KEY environment variable.
"""
import argparse
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from palimpsest.pipeline.ocr import GeminiOCR


def main():
    parser = argparse.ArgumentParser(
        description="Run Gemini OCR on a single manuscript page"
    )
    parser.add_argument(
        "--image", required=True, help="Path to page image file"
    )
    parser.add_argument(
        "--doc-id", required=True, help="Document identifier"
    )
    parser.add_argument(
        "--page-id", required=True, help="Page identifier (e.g., f001r)"
    )
    parser.add_argument(
        "--out", required=True, help="Output page.json path"
    )
    parser.add_argument(
        "--prompt",
        default="alchemical_text",
        choices=["alchemical_text", "technical_diagram"],
        help="Prompt template to use",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Gemini model (default: gemini-3-flash-preview with Agentic Vision)",
    )
    parser.add_argument(
        "--source-name", help="Archive/repository name"
    )
    parser.add_argument(
        "--source-collection", help="Collection name"
    )
    parser.add_argument(
        "--source-ref", help="Catalog reference"
    )
    parser.add_argument(
        "--manuscript-name",
        help="Human-readable manuscript name for prompt context",
    )

    args = parser.parse_args()

    # Check API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set")
        print("Get your key at: https://aistudio.google.com/apikey")
        sys.exit(1)

    # Build source info if provided
    source_info = None
    if args.source_name:
        source_info = {
            "name": args.source_name,
            "collection": args.source_collection,
            "source_doc_ref": args.source_ref,
        }

    # Initialize OCR
    ocr = GeminiOCR(model=args.model)

    # Process page
    print(f"Processing {args.image}...")
    try:
        page = ocr.process_page(
            image_path=Path(args.image),
            doc_id=args.doc_id,
            page_id=args.page_id,
            prompt_type=args.prompt,
            source_info=source_info,
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Ensure output directory exists
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Save
    page.save(str(out_path))
    print(f"[OK] Saved: {out_path}")
    print(f"     Zones: {len(page.zones)}")
    print(f"     Claims: {len(page.claims) if page.claims else 0}")


if __name__ == "__main__":
    main()
