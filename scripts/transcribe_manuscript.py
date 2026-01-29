#!/usr/bin/env python3
"""Two-pass manuscript transcription pipeline.

This pipeline implements a proven approach for high-quality medieval manuscript
transcription using Gemini's vision capabilities:

PASS 1 (Initial Transcription):
  - Uses max-resolution IIIF image
  - Domain-specific prompt with vocabulary and abbreviation reference
  - Outputs structured transcription with line counts

PASS 2 (Refinement):
  - Uses same high-res image + Pass 1 output as context
  - Verifies each line against image
  - Enforces consistent format
  - Resolves uncertain readings
  - Adds paleographical notes

Usage:
    # Single page, both passes
    python scripts/transcribe_manuscript.py \
        --image projects/vatican_alchemy/images/pal_lat_1267_max/f001r.jpg \
        --out-dir projects/vatican_alchemy/exports/transcriptions \
        --prompt lumen_luminum

    # Batch processing
    python scripts/transcribe_manuscript.py \
        --image-dir projects/vatican_alchemy/images/pal_lat_1267_max \
        --out-dir projects/vatican_alchemy/exports/transcriptions \
        --prompt lumen_luminum \
        --pattern "f00*.jpg"

    # Skip pass 1 if draft exists
    python scripts/transcribe_manuscript.py \
        --image-dir projects/vatican_alchemy/images/pal_lat_1267_max \
        --out-dir projects/vatican_alchemy/exports/transcriptions \
        --prompt lumen_luminum \
        --skip-existing

Environment:
    GEMINI_API_KEY: Your Gemini API key (or use .env file)

Model: gemini-3-flash-preview with Agentic Vision (code_execution tool)
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment
PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"
PROMPT_SETS_DIR = PROMPTS_DIR / "sets"
DEFAULT_MODEL = "gemini-3-flash-preview"


def load_prompt(name: str) -> str:
    """Load prompt template from file."""
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def load_prompt_set(set_name: str) -> tuple[str, str]:
    """Load pass1/pass2 prompts from a set folder."""
    set_dir = PROMPT_SETS_DIR / set_name
    pass1_path = set_dir / "pass1.txt"
    pass2_path = set_dir / "pass2.txt"
    if not pass1_path.exists() or not pass2_path.exists():
        raise FileNotFoundError(
            f"Prompt set not found: {set_dir} (expected pass1.txt and pass2.txt)"
        )
    return (
        pass1_path.read_text(encoding="utf-8"),
        pass2_path.read_text(encoding="utf-8"),
    )


def load_prompt_pair(
    prompt_name: Optional[str],
    prompt_set: Optional[str],
    prompt_pass1: Optional[str],
    prompt_pass2: Optional[str],
) -> tuple[str, str]:
    """Resolve pass1/pass2 prompts from explicit paths, set, or legacy names."""
    if prompt_pass1 and prompt_pass2:
        return (
            Path(prompt_pass1).read_text(encoding="utf-8"),
            Path(prompt_pass2).read_text(encoding="utf-8"),
        )
    if prompt_set:
        return load_prompt_set(prompt_set)
    if prompt_name:
        # Legacy: <name>.txt + <name>_refine.txt
        return (
            load_prompt(prompt_name),
            load_prompt(f"{prompt_name}_refine"),
        )
    raise FileNotFoundError("No prompt specified")


def transcribe_pass1(
    client: genai.Client,
    image_path: Path,
    prompt: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Pass 1: Initial transcription from image.

    Uses domain-specific prompt with vocabulary reference to produce
    structured transcription with diplomatic and normalized layers.
    """
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution={})],
            temperature=0.1,
        ),
    )

    return response.text


def transcribe_pass2(
    client: genai.Client,
    image_path: Path,
    draft: str,
    refine_prompt_template: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Pass 2: Refinement with draft context.

    Uses high-res image + Pass 1 output to:
    - Verify each line against manuscript
    - Correct errors in reading/expansion
    - Enforce consistent format
    - Resolve uncertain readings
    - Add paleographical notes
    """
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    # Insert draft into refinement prompt
    prompt = refine_prompt_template.replace("{draft_transcription}", draft)

    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution={})],
            temperature=0.1,
        ),
    )

    return response.text


def transcribe_page(
    image_path: Path,
    out_dir: Path,
    prompt_name: Optional[str],
    prompt_set: Optional[str],
    prompt_pass1: Optional[str],
    prompt_pass2: Optional[str],
    client: genai.Client,
    model: str = DEFAULT_MODEL,
    skip_existing: bool = False,
    verbose: bool = False,
) -> dict:
    """Run two-pass transcription on a single page.

    Returns:
        Dict with keys: pass1_path, pass2_path, pass1_chars, pass2_chars, status
    """
    base = image_path.stem
    pass1_path = out_dir / f"{base}_pass1.txt"
    pass2_path = out_dir / f"{base}_final.txt"

    result = {
        "image": image_path.name,
        "pass1_path": pass1_path,
        "pass2_path": pass2_path,
        "pass1_chars": 0,
        "pass2_chars": 0,
        "status": "pending",
    }

    # Load prompts
    try:
        pass1_prompt, refine_prompt = load_prompt_pair(
            prompt_name=prompt_name,
            prompt_set=prompt_set,
            prompt_pass1=prompt_pass1,
            prompt_pass2=prompt_pass2,
        )
    except FileNotFoundError as e:
        result["status"] = f"error: {e}"
        return result

    out_dir.mkdir(parents=True, exist_ok=True)

    # Pass 1: Initial transcription
    if skip_existing and pass1_path.exists():
        if verbose:
            print(f"  Pass 1: SKIPPED (exists)")
        pass1_text = pass1_path.read_text(encoding="utf-8")
    else:
        if verbose:
            print(f"  Pass 1: Processing...", end=" ", flush=True)
        try:
            pass1_text = transcribe_pass1(client, image_path, pass1_prompt, model)
            pass1_path.write_text(pass1_text, encoding="utf-8")
            if verbose:
                print(f"OK ({len(pass1_text)} chars)")
        except Exception as e:
            result["status"] = f"pass1 error: {e}"
            if verbose:
                print(f"FAILED: {e}")
            return result

    result["pass1_chars"] = len(pass1_text)

    # Pass 2: Refinement
    if skip_existing and pass2_path.exists():
        if verbose:
            print(f"  Pass 2: SKIPPED (exists)")
        pass2_text = pass2_path.read_text(encoding="utf-8")
    else:
        if verbose:
            print(f"  Pass 2: Refining...", end=" ", flush=True)
        try:
            pass2_text = transcribe_pass2(
                client, image_path, pass1_text, refine_prompt, model
            )
            pass2_path.write_text(pass2_text, encoding="utf-8")
            if verbose:
                print(f"OK ({len(pass2_text)} chars)")
        except Exception as e:
            result["status"] = f"pass2 error: {e}"
            if verbose:
                print(f"FAILED: {e}")
            return result

    result["pass2_chars"] = len(pass2_text)
    result["status"] = "complete"

    return result


def transcribe_batch(
    image_dir: Path,
    out_dir: Path,
    prompt_name: Optional[str],
    prompt_set: Optional[str],
    prompt_pass1: Optional[str],
    prompt_pass2: Optional[str],
    pattern: str = "*.jpg",
    model: str = DEFAULT_MODEL,
    skip_existing: bool = False,
    delay: float = 2.0,
    verbose: bool = False,
) -> list[dict]:
    """Run two-pass transcription on multiple pages."""

    images = sorted(image_dir.glob(pattern))
    if not images:
        print(f"No images found matching '{pattern}' in {image_dir}")
        return []

    print(f"Two-Pass Manuscript Transcription Pipeline")
    print(f"=" * 60)
    print(f"Images: {len(images)} matching '{pattern}'")
    if prompt_set:
        print(f"Prompt set: {prompt_set}")
    elif prompt_pass1 and prompt_pass2:
        print(f"Prompt files: {prompt_pass1}, {prompt_pass2}")
    else:
        print(f"Prompt: {prompt_name}")
    print(f"Model: {model}")
    print(f"Output: {out_dir}")
    print(f"=" * 60)

    client = genai.Client()
    results = []

    for i, image_path in enumerate(images, 1):
        print(f"\n[{i}/{len(images)}] {image_path.name}")

        result = transcribe_page(
            image_path=image_path,
            out_dir=out_dir,
            prompt_name=prompt_name,
            prompt_set=prompt_set,
            prompt_pass1=prompt_pass1,
            prompt_pass2=prompt_pass2,
            client=client,
            model=model,
            skip_existing=skip_existing,
            verbose=True,
        )
        results.append(result)

        # Rate limiting between pages
        if delay > 0 and i < len(images):
            time.sleep(delay)

    # Summary
    print(f"\n{'=' * 60}")
    complete = sum(1 for r in results if r["status"] == "complete")
    failed = len(results) - complete
    print(f"Complete: {complete} pages, Failed: {failed} pages")

    if verbose:
        print(f"\nDetails:")
        for r in results:
            improvement = ""
            if r["pass1_chars"] > 0 and r["pass2_chars"] > 0:
                pct = (r["pass2_chars"] - r["pass1_chars"]) / r["pass1_chars"] * 100
                improvement = f" (+{pct:.0f}%)" if pct > 0 else f" ({pct:.0f}%)"
            print(f"  {r['image']}: {r['pass1_chars']} → {r['pass2_chars']} chars{improvement} [{r['status']}]")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Two-pass manuscript transcription pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single image
  python scripts/transcribe_manuscript.py \\
      --image f001r.jpg --out-dir output/ --prompt lumen_luminum

  # Batch processing
  python scripts/transcribe_manuscript.py \\
      --image-dir images/ --out-dir output/ --prompt lumen_luminum \\
      --pattern "f00*.jpg" --skip-existing
        """,
    )

    # Input (mutually exclusive: single image or directory)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--image",
        help="Single image file to transcribe",
    )
    input_group.add_argument(
        "--image-dir",
        help="Directory of images to transcribe",
    )

    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for transcriptions",
    )
    parser.add_argument(
        "--prompt",
        help="Legacy prompt base name (loads <name>.txt and <name>_refine.txt)",
    )
    parser.add_argument(
        "--prompt-set",
        help="Prompt set folder under palimpsest/prompts/sets (pass1.txt + pass2.txt)",
    )
    parser.add_argument(
        "--prompt-pass1",
        help="Explicit path to pass1 prompt file",
    )
    parser.add_argument(
        "--prompt-pass2",
        help="Explicit path to pass2 prompt file",
    )
    parser.add_argument(
        "--pattern",
        default="*.jpg",
        help="Glob pattern for images in batch mode (default: *.jpg)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip pages that already have output files",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between API calls in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Validate prompt inputs
    if not (args.prompt or args.prompt_set or (args.prompt_pass1 and args.prompt_pass2)):
        print("Error: provide --prompt, --prompt-set, or both --prompt-pass1/--prompt-pass2", file=sys.stderr)
        sys.exit(1)

    try:
        if args.image:
            # Single image mode
            client = genai.Client()
            print(f"Two-Pass Manuscript Transcription")
            print(f"Image: {args.image}")

            result = transcribe_page(
                image_path=Path(args.image),
                out_dir=Path(args.out_dir),
                prompt_name=args.prompt,
                prompt_set=args.prompt_set,
                prompt_pass1=args.prompt_pass1,
                prompt_pass2=args.prompt_pass2,
                client=client,
                model=args.model,
                skip_existing=args.skip_existing,
                verbose=True,
            )

            if result["status"] == "complete":
                print(f"\nOutput: {result['pass2_path']}")
            else:
                print(f"\nFailed: {result['status']}")
                sys.exit(1)
        else:
            # Batch mode
            transcribe_batch(
                image_dir=Path(args.image_dir),
                out_dir=Path(args.out_dir),
                prompt_name=args.prompt,
                prompt_set=args.prompt_set,
                prompt_pass1=args.prompt_pass1,
                prompt_pass2=args.prompt_pass2,
                pattern=args.pattern,
                model=args.model,
                skip_existing=args.skip_existing,
                delay=args.delay,
                verbose=args.verbose,
            )

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
