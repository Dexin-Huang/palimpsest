#!/usr/bin/env python3
"""
Analyze Trionfi/allegorical manuscript images.

Creates scholarly documentation of illustrated manuscript pages.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

from google import genai
from google.genai import types

PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"
DEFAULT_MODEL = "gemini-2.0-flash"


def load_prompt(name: str) -> str:
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def analyze_page(client: genai.Client, image_path: Path, prompt: str, model: str) -> str:
    """Analyze a single manuscript page."""
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            temperature=0.2,
        ),
    )

    return response.text


def main():
    parser = argparse.ArgumentParser(description="Analyze Trionfi manuscript pages")
    parser.add_argument("--image-dir", required=True, help="Directory with images")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument("--prompt", default="trionfi_analysis", help="Prompt name")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model")
    parser.add_argument("--pattern", default="f*.jpg", help="File pattern")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if output exists")

    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    prompt = load_prompt(args.prompt)

    image_dir = Path(args.image_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(image_dir.glob(args.pattern))
    print(f"Found {len(images)} images matching '{args.pattern}'")
    print(f"Output: {out_dir}")
    print("=" * 60)

    for i, img_path in enumerate(images, 1):
        stem = img_path.stem
        out_path = out_dir / f"{stem}_analysis.txt"

        if args.skip_existing and out_path.exists():
            print(f"[{i}/{len(images)}] {img_path.name} - exists, skipping")
            continue

        print(f"[{i}/{len(images)}] {img_path.name}...", end=" ", flush=True)

        try:
            analysis = analyze_page(client, img_path, prompt, args.model)
            out_path.write_text(analysis, encoding="utf-8")
            print(f"OK ({len(analysis)} chars)")
        except Exception as e:
            print(f"FAILED: {e}")

    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
