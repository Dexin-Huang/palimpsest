#!/usr/bin/env python3
"""Transcribe a manuscript page image using Gemini with Agentic Vision.

This script uses gemini-3-flash-preview with code_execution enabled (Agentic Vision)
to transcribe historical manuscript images. The model can zoom and crop to read
fine details in medieval scripts.

Usage:
    python scripts/transcribe_page.py --image path/to/image.jpg --out transcription.txt
    python scripts/transcribe_page.py --image path/to/image.jpg --out output.txt --prompt alchemical_text

Requirements:
    - GEMINI_API_KEY environment variable (or .env file in project root)
    - google-genai package: pip install google-genai
    - python-dotenv package: pip install python-dotenv

Example:
    python scripts/transcribe_page.py \
        --image projects/vatican_alchemy/images/f020r.jpg \
        --out projects/vatican_alchemy/exports/transcriptions/f020r.txt \
        --prompt transcription_enhanced
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


# Default model: gemini-3-flash-preview has Agentic Vision capabilities
DEFAULT_MODEL = "gemini-3-flash-preview"

# Project root and prompts directory
PROJECT_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"


def load_env() -> None:
    """Load environment variables from .env file."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def load_prompt(prompt_name: str) -> str:
    """Load a prompt template from the prompts directory.

    Args:
        prompt_name: Name of prompt file (without .txt extension)

    Returns:
        Prompt text content

    Raises:
        FileNotFoundError: If prompt file does not exist
    """
    prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not prompt_path.exists():
        available = [p.stem for p in PROMPTS_DIR.glob("*.txt")]
        raise FileNotFoundError(
            f"Prompt '{prompt_name}' not found at {prompt_path}\n"
            f"Available prompts: {', '.join(available)}"
        )
    return prompt_path.read_text(encoding="utf-8")


def get_mime_type(path: Path) -> str:
    """Determine MIME type from file extension."""
    suffix = path.suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }
    return mime_types.get(suffix, "image/jpeg")


def transcribe_image(
    image_path: Path,
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
) -> str:
    """Transcribe a manuscript image using Gemini with Agentic Vision.

    Args:
        image_path: Path to the image file
        prompt: The transcription prompt to use
        model: Gemini model name (default: gemini-3-flash-preview)
        temperature: Model temperature (default: 0.1 for consistency)

    Returns:
        Transcription text from the model

    Raises:
        RuntimeError: If API call fails
    """
    client = genai.Client()

    # Read and prepare image
    image_bytes = image_path.read_bytes()
    mime_type = get_mime_type(image_path)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    # Call Gemini with Agentic Vision (code_execution enables zoom/crop)
    # Note: Do NOT set max_output_tokens - let the model complete fully
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            temperature=temperature,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        ),
    )

    # response.text correctly concatenates all text parts from the multi-part response
    # The SDK may warn about non-text parts (code, images) but this is informational
    return response.text


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Transcribe a manuscript page image using Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --image f020r.jpg --out f020r.txt
    %(prog)s --image f020r.jpg --out f020r.txt --prompt alchemical_text
    %(prog)s --image f020r.jpg  # prints to stdout (not recommended on Windows)
        """,
    )
    parser.add_argument(
        "--image",
        required=True,
        type=Path,
        help="Path to the manuscript image file",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output file for transcription (recommended over stdout on Windows)",
    )
    parser.add_argument(
        "--prompt",
        default="transcription_enhanced",
        help="Prompt template name (default: transcription_enhanced)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Model temperature 0.0-1.0 (default: 0.1)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress messages to stderr",
    )

    args = parser.parse_args()

    # Validate image exists
    if not args.image.exists():
        print(f"Error: Image not found: {args.image}", file=sys.stderr)
        return 1

    # Load environment
    load_env()

    # Load prompt
    if args.verbose:
        print(f"Loading prompt: {args.prompt}", file=sys.stderr)

    try:
        prompt = load_prompt(args.prompt)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Transcribe
    if args.verbose:
        print(f"Transcribing: {args.image}", file=sys.stderr)
        print(f"Model: {args.model}", file=sys.stderr)
        print(f"Prompt length: {len(prompt)} chars", file=sys.stderr)

    try:
        result = transcribe_image(
            image_path=args.image,
            prompt=prompt,
            model=args.model,
            temperature=args.temperature,
        )
    except Exception as e:
        print(f"Error during transcription: {e}", file=sys.stderr)
        return 1

    # Output
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result, encoding="utf-8")
        if args.verbose:
            print(f"Saved: {args.out} ({len(result)} chars)", file=sys.stderr)
    else:
        # Warn about Windows console encoding issues
        if sys.platform == "win32":
            print(
                "Warning: Printing to stdout on Windows may have Unicode issues. "
                "Use --out to write to a file.",
                file=sys.stderr,
            )
        print(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
