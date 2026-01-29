#!/usr/bin/env python3
"""Batch refine manuscript transcriptions using second pass with draft context.

Usage:
    python scripts/batch_refine.py \
        --image-dir projects/vatican_alchemy/images/pal_lat_1267_max \
        --draft-dir projects/vatican_alchemy/exports/transcriptions \
        --out-dir projects/vatican_alchemy/exports/transcriptions_v3 \
        --pattern "f00*.jpg"
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


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompts_dir = Path(__file__).parent.parent / "palimpsest" / "prompts"
    prompt_file = prompts_dir / f"{name}.txt"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


def find_draft(image_name: str, draft_dir: Path) -> Path | None:
    """Find matching draft transcription for an image.

    Tries patterns like:
    - f001r.jpg -> f001r_v2.txt, f001r.txt
    """
    base = Path(image_name).stem  # e.g., "f001r"

    # Try different suffixes
    for suffix in ["_v2.txt", ".txt"]:
        draft_path = draft_dir / f"{base}{suffix}"
        if draft_path.exists():
            return draft_path

    return None


def refine_single(
    image_path: Path,
    draft_path: Path,
    output_path: Path,
    client: genai.Client,
    prompt_template: str,
    model: str,
) -> tuple[bool, str]:
    """Refine a single transcription.

    Returns:
        Tuple of (success, result_message)
    """
    try:
        # Load draft
        draft_text = draft_path.read_text(encoding="utf-8")

        # Insert draft into prompt
        prompt = prompt_template.replace("{draft_transcription}", draft_text)

        # Load image
        image_bytes = image_path.read_bytes()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        # Generate
        response = client.models.generate_content(
            model=model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                tools=[types.Tool(code_execution={})],
                temperature=0.1,
            ),
        )

        text = response.text

        # Save
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

        return True, f"OK ({len(text)} chars)"

    except Exception as e:
        return False, f"FAILED: {e}"


def batch_refine(
    image_dir: Path,
    draft_dir: Path,
    output_dir: Path,
    pattern: str = "f*.jpg",
    model: str = "gemini-3-flash-preview",
    delay: float = 2.0,
    skip_existing: bool = True,
) -> tuple[int, int]:
    """Batch refine transcriptions.

    Returns:
        Tuple of (success_count, fail_count)
    """
    # Find images
    images = sorted(image_dir.glob(pattern))
    if not images:
        print(f"No images found matching {pattern} in {image_dir}")
        return 0, 0

    print(f"Found {len(images)} images matching '{pattern}'")

    # Load prompt template
    prompt_template = load_prompt("lumen_luminum_refine")
    print(f"Loaded prompt: lumen_luminum_refine ({len(prompt_template)} chars)")
    print(f"Model: {model}")
    print(f"Output: {output_dir}")
    print("-" * 60)

    # Initialize client
    client = genai.Client()

    success_count = 0
    fail_count = 0

    for i, image_path in enumerate(images, 1):
        # Find draft
        draft_path = find_draft(image_path.name, draft_dir)
        if draft_path is None:
            print(f"[{i}/{len(images)}] {image_path.name} - SKIPPED (no draft found)")
            continue

        # Output path
        base = image_path.stem
        output_path = output_dir / f"{base}_v3.txt"

        # Skip if exists
        if skip_existing and output_path.exists():
            print(f"[{i}/{len(images)}] {image_path.name} - SKIPPED (exists)")
            continue

        # Process
        print(f"[{i}/{len(images)}] {image_path.name}...", end=" ", flush=True)

        success, message = refine_single(
            image_path=image_path,
            draft_path=draft_path,
            output_path=output_path,
            client=client,
            prompt_template=prompt_template,
            model=model,
        )

        print(message)

        if success:
            success_count += 1
        else:
            fail_count += 1

        # Rate limit
        if delay > 0 and i < len(images):
            time.sleep(delay)

    print("-" * 60)
    print(f"Complete: {success_count} succeeded, {fail_count} failed")

    return success_count, fail_count


def main():
    parser = argparse.ArgumentParser(
        description="Batch refine manuscript transcriptions"
    )
    parser.add_argument(
        "--image-dir",
        required=True,
        help="Directory containing manuscript images",
    )
    parser.add_argument(
        "--draft-dir",
        required=True,
        help="Directory containing draft transcriptions",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for refined transcriptions",
    )
    parser.add_argument(
        "--pattern",
        default="f*.jpg",
        help="Glob pattern for images (default: f*.jpg)",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Gemini model (default: gemini-3-flash-preview)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between requests in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Don't skip existing output files",
    )

    args = parser.parse_args()

    try:
        batch_refine(
            image_dir=Path(args.image_dir),
            draft_dir=Path(args.draft_dir),
            output_dir=Path(args.out_dir),
            pattern=args.pattern,
            model=args.model,
            delay=args.delay,
            skip_existing=not args.no_skip,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
