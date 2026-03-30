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


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transcribe", help="Single-call VLM transcription")
    sub = parser.add_subparsers(dest="transcribe_command", required=True)

    run_parser = sub.add_parser("run", help="Transcribe a directory of page images to JSONL")
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
