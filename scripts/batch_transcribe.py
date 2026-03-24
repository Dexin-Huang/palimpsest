#!/usr/bin/env python3
"""Batch transcribe manuscript pages.

Usage:
    python scripts/batch_transcribe.py \
        --image-dir projects/vatican_alchemy/images/pal_lat_1267 \
        --out-dir projects/vatican_alchemy/exports/transcriptions \
        --prompt lumen_luminum \
        --pattern "f*.jpg"
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


# Load environment
PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"
DEFAULT_MODEL = "gemini-3-flash-preview"


def load_prompt(prompt_name: str) -> str:
    """Load prompt from file."""
    prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def get_mime_type(path: Path) -> str:
    """Get MIME type from extension."""
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(path.suffix.lower(), "image/jpeg")


def transcribe_image(client, model: str, prompt: str, image_path: Path) -> str:
    """Transcribe a single image."""
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type=get_mime_type(image_path)
    )

    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            temperature=0.1,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        ),
    )
    return response.text


def main():
    parser = argparse.ArgumentParser(description="Batch transcribe manuscript pages")
    parser.add_argument("--image-dir", required=True, type=Path, help="Directory with images")
    parser.add_argument("--out-dir", required=True, type=Path, help="Output directory")
    parser.add_argument("--prompt", default="lumen_luminum", help="Prompt template name")
    parser.add_argument("--pattern", default="f*.jpg", help="Glob pattern for images")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests (seconds)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if output exists")

    args = parser.parse_args()

    # Validate paths
    if not args.image_dir.exists():
        print(f"Error: Image directory not found: {args.image_dir}", file=sys.stderr)
        return 1

    # Find images
    images = sorted(args.image_dir.glob(args.pattern))
    if not images:
        print(f"Error: No images match pattern '{args.pattern}' in {args.image_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(images)} images matching '{args.pattern}'")

    # Load prompt
    try:
        prompt = load_prompt(args.prompt)
        print(f"Loaded prompt: {args.prompt} ({len(prompt)} chars)")
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Create output directory
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Initialize client
    client = genai.Client()
    print(f"Model: {args.model}")
    print(f"Output: {args.out_dir}")
    print("-" * 60)

    # Process images
    success = 0
    failed = 0

    for i, img_path in enumerate(images):
        out_path = args.out_dir / f"{img_path.stem}.txt"

        # Skip if exists
        if args.skip_existing and out_path.exists():
            print(f"[{i+1}/{len(images)}] {img_path.name} - SKIPPED (exists)")
            success += 1
            continue

        print(f"[{i+1}/{len(images)}] {img_path.name}...", end=" ", flush=True)

        try:
            result = transcribe_image(client, args.model, prompt, img_path)
            out_path.write_text(result, encoding="utf-8")
            print(f"OK ({len(result)} chars)")
            success += 1
        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1

        # Rate limit
        if i < len(images) - 1 and args.delay > 0:
            time.sleep(args.delay)

    print("-" * 60)
    print(f"Complete: {success} succeeded, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
