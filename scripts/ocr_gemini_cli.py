#!/usr/bin/env python3
"""OCR pipeline using Gemini CLI for historical document transcription.

Uses Google AI Pro subscription via Gemini CLI for generous rate limits.
Outputs rescript.page.v1 JSON schema.

Usage:
  python scripts/ocr_gemini_cli.py \
    --image projects/pasajeros_a_indias/images/page001.jpg \
    --out projects/pasajeros_a_indias/pages/page001.page.json \
    --doc-id pares_pasajeros_001 \
    --collection "Pasajeros a Indias"

Requirements:
  - Gemini CLI installed: npm install -g @google/gemini-cli
  - Authenticated: npx @google/gemini-cli (follow prompts)
  - Google AI Pro subscription recommended for 5x rate limits
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Try to get image dimensions
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


GEMINI_MODEL = "gemini-2.5-pro"  # Best model for vision/OCR tasks

SYSTEM_PROMPT = """You are an expert paleographer and OCR specialist for 16th-17th century Spanish colonial documents.

Analyze this scanned historical document image and extract:
1. All text zones (lines, marginalia, headers, stamps)
2. For each zone: bounding box coordinates and text in multiple layers
3. Structured claims (people, places, dates, ships, cargo)

IMPORTANT INSTRUCTIONS:
- bbox_norm coordinates are normalized 0-1 fractions of image dimensions: [x, y, width, height] where (0,0) is top-left
- Estimate bounding boxes visually as best you can
- es_diplomatic: exact transcription preserving original spelling (ç, ss, u/v variations, abbreviations expanded in [])
- es_normalized: modernized Spanish spelling
- en_literal: direct English translation
- en_interpreted: contextual interpretation with implied meaning

For claims, extract:
- person_name: full names of individuals
- origin_place: where people are from ("natural de X")
- destination_place: where traveling to
- date_recorded: dates in ISO format (YYYY-MM-DD)
- ship_name: vessel names
- occupation: professions/trades
- cargo_item: goods being transported

Return ONLY valid JSON matching this exact schema (no markdown, no explanation):
{
  "zones": [
    {
      "zone_id": "l0001",
      "type": "line|marginalia|header|stamp",
      "order": 10,
      "bbox_norm": [x, y, w, h],
      "text": {
        "es_diplomatic": "...",
        "es_normalized": "...",
        "en_literal": "...",
        "en_interpreted": "..."
      },
      "confidence": {
        "transcription": 0.0-1.0,
        "normalization": 0.0-1.0,
        "translation": 0.0-1.0
      },
      "notes": [{"type": "spelling|abbreviation|damage", "from": "...", "to": "..."}]
    }
  ],
  "claims": [
    {
      "claim_id": "c0001",
      "type": "person_name|origin_place|destination_place|date_recorded|ship_name|occupation|cargo_item",
      "value": "...",
      "span": {"zone_id": "l0001", "char_start": 0, "char_end": 10},
      "confidence": 0.0-1.0
    }
  ]
}"""


def get_image_info(image_path: Path) -> dict:
    """Get image dimensions and SHA256 hash."""
    # Calculate SHA256
    sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()

    # Get dimensions
    if HAS_PIL:
        with Image.open(image_path) as img:
            width, height = img.size
    else:
        # Fallback: assume standard dimensions, user can correct
        print("[WARN] PIL not installed, using placeholder dimensions. Install with: pip install Pillow", file=sys.stderr)
        width, height = 1400, 2000

    return {"width_px": width, "height_px": height, "sha256": sha256}


def run_gemini_cli(image_path: str, prompt: str, model: str = GEMINI_MODEL) -> str:
    """Execute Gemini CLI with image and prompt."""
    # Build command: gemini -m model -o json "prompt" @image_path
    # Note: prompt includes instructions, image is passed via @ syntax
    full_prompt = f'{prompt}\n\nAnalyze the document image and return JSON:'

    cmd = [
        "npx", "@google/gemini-cli",
        "-m", model,
        "-p", full_prompt,
        f"@{image_path}"
    ]

    print(f"[INFO] Calling Gemini CLI with model {model}...", file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout for large documents
            shell=True  # Needed for Windows npx
        )

        if result.returncode != 0:
            print(f"[ERROR] Gemini CLI failed: {result.stderr}", file=sys.stderr)
            raise RuntimeError(f"Gemini CLI error: {result.stderr}")

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        raise RuntimeError("Gemini CLI timed out after 120 seconds")


def extract_json_from_response(response: str) -> dict:
    """Extract JSON from Gemini response, handling markdown code blocks."""
    text = response.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1

    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in response: {text[:200]}...")

    json_str = text[start:end]
    return json.loads(json_str)


def build_page_json(
    image_path: Path,
    ocr_result: dict,
    page_id: str,
    doc_id: str,
    collection: str = "Pasajeros a Indias",
    source_ref: str = ""
) -> dict:
    """Build complete rescript.page.v1 JSON from OCR results."""

    img_info = get_image_info(image_path)

    # Make image path relative to repo root
    try:
        rel_path = image_path.relative_to(Path.cwd())
    except ValueError:
        rel_path = image_path

    page = {
        "schema_version": "rescript.page.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "page_id": page_id,
        "doc_id": doc_id,
        "source": {
            "name": "PARES",
            "collection": collection,
            "source_doc_ref": source_ref or page_id,
            "provenance_note": "Transcribed via Gemini CLI OCR pipeline"
        },
        "image": {
            "path": str(rel_path).replace("\\", "/"),
            "width_px": img_info["width_px"],
            "height_px": img_info["height_px"],
            "sha256": img_info["sha256"]
        },
        "reading_direction": "ltr",
        "coordinate_space": "norm01",
        "zones": ocr_result.get("zones", []),
        "claims": ocr_result.get("claims", []),
        "pipeline": {
            "assumed_components": {
                "layout": "gemini-vision",
                "transcription": "gemini-vision",
                "normalization": "gemini-llm",
                "translation": "gemini-llm",
                "extraction": "gemini-llm"
            },
            "model_versions": {
                "gemini": GEMINI_MODEL
            },
            "notes": "Processed via Gemini CLI with Google AI Pro subscription"
        }
    }

    return page


def main():
    parser = argparse.ArgumentParser(description="OCR historical documents using Gemini CLI")
    parser.add_argument("--image", required=True, help="Path to scanned document image")
    parser.add_argument("--out", required=True, help="Output path for page.json")
    parser.add_argument("--page-id", help="Page ID (default: derived from image filename)")
    parser.add_argument("--doc-id", required=True, help="Document ID")
    parser.add_argument("--collection", default="Pasajeros a Indias", help="Collection name")
    parser.add_argument("--source-ref", default="", help="Source document reference")
    parser.add_argument("--model", default=GEMINI_MODEL, help=f"Gemini model (default: {GEMINI_MODEL})")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"[ERROR] Image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    page_id = args.page_id or image_path.stem

    # Run OCR
    print(f"[INFO] Processing: {image_path}", file=sys.stderr)
    raw_response = run_gemini_cli(str(image_path), SYSTEM_PROMPT, args.model)

    # Parse JSON from response
    print("[INFO] Parsing OCR results...", file=sys.stderr)
    try:
        ocr_result = extract_json_from_response(raw_response)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] Failed to parse JSON: {e}", file=sys.stderr)
        print(f"[DEBUG] Raw response:\n{raw_response[:1000]}", file=sys.stderr)
        sys.exit(1)

    # Build full page JSON
    page_json = build_page_json(
        image_path=image_path,
        ocr_result=ocr_result,
        page_id=page_id,
        doc_id=args.doc_id,
        collection=args.collection,
        source_ref=args.source_ref
    )

    # Write output
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(page_json, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[OK] Written: {out_path}", file=sys.stderr)
    print(f"[INFO] Zones: {len(page_json['zones'])}, Claims: {len(page_json['claims'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
