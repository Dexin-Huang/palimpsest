from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.transcribe import DEFAULT_PROMPT_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_WORKERS


def cmd_run(args: argparse.Namespace) -> None:
    from palimpsest.transcribe import run_transcription_sync

    run_transcription_sync(
        Path(args.image_dir),
        output_path=Path(args.output),
        source=args.source or "",
        book_title=args.book_title or "",
        model=args.model,
        prompt_name=args.prompt_name,
        system_prompt=args.system_prompt,
        workers=args.workers,
        skip_existing=args.skip_existing,
    )


def cmd_batch_submit(args: argparse.Namespace) -> None:
    from palimpsest.batch import create_manifest, submit_batch, print_status

    output_dir = Path(args.output_dir)
    manifest_path = output_dir / "batch_manifest.json"

    if not manifest_path.exists():
        create_manifest(
            Path(args.image_dir),
            output_dir,
            source=args.source or "",
            book_title=args.book_title or "",
            model=args.model,
            prompt_name=args.prompt_name,
            system_prompt=args.system_prompt,
        )
        print(f"Manifest created: {manifest_path}")

    manifest = submit_batch(output_dir)
    print_status(manifest)


def cmd_batch_status(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, print_status

    manifest = poll_batch(Path(args.output_dir))
    print_status(manifest)


def cmd_batch_collect(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, collect_batch, print_status

    # Poll first to get latest state
    manifest = poll_batch(Path(args.output_dir))
    print_status(manifest)

    if manifest["status"] not in ("completed", "partial"):
        print(f"\nBatch not ready for collection (status: {manifest['status']})")
        return

    final_path = collect_batch(Path(args.output_dir))
    print(f"\nCollected to: {final_path}")


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transcribe", help="Single-call VLM transcription")
    sub = parser.add_subparsers(dest="transcribe_command", required=True)

    # --- Interactive run ---
    run_parser = sub.add_parser("run", help="Transcribe page images to JSONL (interactive, real-time)")
    run_parser.add_argument("--image-dir", required=True, help="Directory containing page images")
    run_parser.add_argument("--output", required=True, help="Output JSONL file path")
    run_parser.add_argument("--source", default="", help="Source identifier (default: parent dir name)")
    run_parser.add_argument("--book-title", default="", help="Book title (default: parent dir name)")
    run_parser.add_argument("--model", default=DEFAULT_MODEL_READING, help="VLM model name")
    run_parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME, help="Prompt template name")
    run_parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT, help="System instruction")
    run_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent workers")
    run_parser.add_argument("--skip-existing", action="store_true", help="Skip pages already in output file")
    run_parser.set_defaults(func=cmd_run)

    # --- Batch submit ---
    submit_parser = sub.add_parser("batch-submit", help="Submit a book's images as batch jobs (half price)")
    submit_parser.add_argument("--image-dir", required=True, help="Directory containing page images")
    submit_parser.add_argument("--output-dir", required=True, help="Output directory for manifest, shards, and JSONL")
    submit_parser.add_argument("--source", default="", help="Source identifier")
    submit_parser.add_argument("--book-title", default="", help="Book title")
    submit_parser.add_argument("--model", default=DEFAULT_MODEL_READING, help="VLM model name")
    submit_parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME, help="Prompt template name")
    submit_parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT, help="System instruction")
    submit_parser.set_defaults(func=cmd_batch_submit)

    # --- Batch status ---
    status_parser = sub.add_parser("batch-status", help="Poll and display batch job status")
    status_parser.add_argument("--output-dir", required=True, help="Output directory with batch manifest")
    status_parser.set_defaults(func=cmd_batch_status)

    # --- Batch collect ---
    collect_parser = sub.add_parser("batch-collect", help="Collect completed batch results into final JSONL")
    collect_parser.add_argument("--output-dir", required=True, help="Output directory with batch manifest")
    collect_parser.set_defaults(func=cmd_batch_collect)
