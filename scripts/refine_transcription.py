#!/usr/bin/env python3
"""Refine manuscript transcriptions using a second pass with draft context.

Usage:
    python scripts/refine_transcription.py \
        --image projects/vatican_alchemy/images/pal_lat_1267_max/f001r.jpg \
        --draft projects/vatican_alchemy/exports/transcriptions/f001r_v2.txt \
        --out projects/vatican_alchemy/exports/transcriptions/f001r_v3.txt
"""
import argparse
import sys
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


def refine_transcription(
    image_path: Path,
    draft_path: Path,
    output_path: Path,
    model: str = "gemini-3-flash-preview",
    verbose: bool = False,
) -> str:
    """Refine a transcription using draft context and high-res image.

    Args:
        image_path: Path to manuscript page image
        draft_path: Path to draft transcription file
        output_path: Path to save refined transcription
        model: Gemini model to use
        verbose: Print progress info

    Returns:
        Refined transcription text
    """
    # Load prompt template
    prompt_template = load_prompt("lumen_luminum_refine")

    # Load draft transcription
    draft_text = draft_path.read_text(encoding="utf-8")

    # Insert draft into prompt
    prompt = prompt_template.replace("{draft_transcription}", draft_text)

    # Load image
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    if verbose:
        print(f"Image: {image_path.name} ({len(image_bytes) // 1024} KB)")
        print(f"Draft: {draft_path.name} ({len(draft_text)} chars)")
        print(f"Model: {model}")
        print("Processing...", end=" ", flush=True)

    # Initialize client
    client = genai.Client()

    # Generate refined transcription
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution={})],
            temperature=0.1,
        ),
    )

    # Extract text
    text = response.text

    if verbose:
        print(f"OK ({len(text)} chars)")

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")

    if verbose:
        print(f"Saved: {output_path}")

    return text


def main():
    parser = argparse.ArgumentParser(
        description="Refine manuscript transcription with draft context"
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Path to manuscript page image",
    )
    parser.add_argument(
        "--draft",
        required=True,
        help="Path to draft transcription file",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output file path for refined transcription",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Gemini model (default: gemini-3-flash-preview)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress info",
    )

    args = parser.parse_args()

    try:
        refine_transcription(
            image_path=Path(args.image),
            draft_path=Path(args.draft),
            output_path=Path(args.out),
            model=args.model,
            verbose=args.verbose,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
