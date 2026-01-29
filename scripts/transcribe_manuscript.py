#!/usr/bin/env python3
"""Two-pass manuscript transcription pipeline.

See docs/TRANSCRIPTION_CLI.md for usage examples.
"""

import argparse
import sys
from pathlib import Path

from palimpsest.transcription import DEFAULT_MODEL, PromptConfig, RunConfig, run_batch, run_single


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Two-pass manuscript transcription pipeline")

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--image", help="Single image file to transcribe")
    input_group.add_argument("--image-dir", help="Directory of images to transcribe")

    parser.add_argument("--out-dir", required=True, help="Output directory for transcriptions")
    parser.add_argument("--prompt", help="Legacy prompt base name (loads <name>.txt and <name>_refine.txt)")
    parser.add_argument("--prompt-set", help="Prompt set folder under palimpsest/prompts/sets (pass1.txt + pass2.txt)")
    parser.add_argument("--prompt-pass1", help="Explicit path to pass1 prompt file")
    parser.add_argument("--prompt-pass2", help="Explicit path to pass2 prompt file")
    parser.add_argument("--pattern", default="*.jpg", help="Glob pattern for images in batch mode (default: *.jpg)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--output-format", default="json", choices=["json"], help="Output format (json only)")
    parser.add_argument(
        "--pass-mode",
        default="both",
        choices=["both", "pass1", "pass2"],
        help="Which passes to run (default: both)",
    )
    parser.add_argument("--workers", type=int, default=10, help="Number of parallel workers (default: 10)")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max attempts per pass (default: 3)")
    parser.add_argument("--no-trace", action="store_true", help="Disable trace capture for faster runs")
    parser.add_argument("--auto-skip-non-text", action="store_true", help="Auto-skip pass2 for low-text pages")
    parser.add_argument("--shard-count", type=int, default=1, help="Total number of shards (default: 1)")
    parser.add_argument("--shard-index", type=int, default=0, help="Shard index (0-based, default: 0)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip pages that already have output files")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between API calls in seconds (default: 2.0)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    return parser


def build_run_config(args: argparse.Namespace) -> RunConfig:
    prompt = PromptConfig(
        prompt_name=args.prompt,
        prompt_set=args.prompt_set,
        prompt_pass1=args.prompt_pass1,
        prompt_pass2=args.prompt_pass2,
    )
    return RunConfig(
        prompt=prompt,
        model=args.model,
        output_format=args.output_format,
        pass_mode=args.pass_mode,
        skip_existing=args.skip_existing,
        verbose=args.verbose,
        delay=args.delay,
        workers=args.workers,
        max_attempts=args.max_attempts,
        trace=not args.no_trace,
        auto_skip_non_text=args.auto_skip_non_text,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run_config = build_run_config(args)
    if not run_config.prompt.is_valid():
        print("Error: provide --prompt, --prompt-set, or both --prompt-pass1/--prompt-pass2", file=sys.stderr)
        sys.exit(1)
    if run_config.model != DEFAULT_MODEL:
        print(f"Error: only model allowed is {DEFAULT_MODEL}", file=sys.stderr)
        sys.exit(1)
    if run_config.shard_count < 1:
        print("Error: --shard-count must be >= 1", file=sys.stderr)
        sys.exit(1)
    if run_config.shard_index < 0 or run_config.shard_index >= run_config.shard_count:
        print("Error: --shard-index must be within [0, shard-count)", file=sys.stderr)
        sys.exit(1)

    try:
        if args.image:
            print("Two-Pass Manuscript Transcription")
            print(f"Image: {args.image}")
            result = run_single(
                image_path=Path(args.image),
                out_dir=Path(args.out_dir),
                run_config=run_config,
            )
            if result["status"] == "complete":
                print(f"\nOutput: {result['pass2_path']}")
            else:
                print(f"\nFailed: {result['status']}")
                sys.exit(1)
        else:
            run_batch(
                image_dir=Path(args.image_dir),
                out_dir=Path(args.out_dir),
                pattern=args.pattern,
                run_config=run_config,
            )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
