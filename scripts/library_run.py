#!/usr/bin/env python3
"""Run download + transcription + assembly for a library document."""

import argparse
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.run import run_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a full library pipeline for a document")
    parser.add_argument("--doc-id", required=True, help="Document ID")
    parser.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    parser.add_argument("--prompt-set", default="transcription_json", help="Prompt set name")
    parser.add_argument("--pass-mode", default="both", choices=["both", "pass1", "pass2"])
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--auto-skip-non-text", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--pattern", default="*.jpg", help="Image glob pattern (default: *.jpg)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    doc_dir = Path(args.library_root) / args.doc_id
    if not doc_dir.exists():
        raise SystemExit(f"Missing document folder: {doc_dir}")

    run_document(
        doc_dir=doc_dir,
        prompt_set=args.prompt_set,
        pass_mode=args.pass_mode,
        workers=args.workers,
        max_attempts=args.max_attempts,
        delay=args.delay,
        auto_skip_non_text=args.auto_skip_non_text,
        download_first=not args.skip_download,
        pattern=args.pattern,
    )


if __name__ == "__main__":
    main()
