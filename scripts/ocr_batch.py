#!/usr/bin/env python3
"""Run Gemini OCR on a batch of manuscript pages.

Usage:
  python scripts/ocr_batch.py \
    --image-dir projects/vatican_alchemy/images/ \
    --doc-id pal_lat_1267 \
    --out-dir projects/vatican_alchemy/pages/ \
    --prompt alchemical_text \
    --parallel 4

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
        description="Run Gemini OCR on a batch of manuscript pages"
    )
    parser.add_argument(
        "--image-dir", required=True, help="Directory containing page images"
    )
    parser.add_argument(
        "--doc-id", required=True, help="Document identifier"
    )
    parser.add_argument(
        "--out-dir", required=True, help="Output directory for page.json files"
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
        "--parallel",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
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

    args = parser.parse_args()

    # Check API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set")
        print("Get your key at: https://aistudio.google.com/apikey")
        sys.exit(1)

    # Validate input directory
    image_dir = Path(args.image_dir)
    if not image_dir.is_dir():
        print(f"Error: Image directory not found: {image_dir}")
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

    # Ensure output directory exists
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Process batch
    print(f"Processing images in {image_dir}...")
    print(f"Using model: {args.model}")
    print(f"Parallel workers: {args.parallel}")
    print()

    pages = ocr.process_batch(
        image_dir=image_dir,
        doc_id=args.doc_id,
        prompt_type=args.prompt,
        parallel=args.parallel,
        source_info=source_info,
    )

    # Save each page
    for page in pages:
        out_path = out_dir / f"{args.doc_id}_{page.page_id}.page.json"
        page.save(str(out_path))

    print()
    print(f"[DONE] Processed {len(pages)} pages")
    print(f"       Output: {out_dir}")


if __name__ == "__main__":
    main()
