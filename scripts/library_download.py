#!/usr/bin/env python3
"""Download images for a library document based on page_list.json."""

import argparse
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.download import download_pages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download images for a library document")
    parser.add_argument("--doc-id", required=True, help="Document ID")
    parser.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    parser.add_argument("--overwrite", action="store_true", help="Redownload existing files")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between downloads (seconds)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout (seconds)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    doc_dir = Path(args.library_root) / args.doc_id
    if not doc_dir.exists():
        raise SystemExit(f"Missing document folder: {doc_dir}")

    summary = download_pages(
        doc_dir=doc_dir,
        overwrite=args.overwrite,
        delay=args.delay,
        timeout=args.timeout,
    )

    print(
        f"Downloaded {summary['downloaded']} / {summary['total']} "
        f"(skipped {summary['skipped']}, failed {summary['failed']})"
    )


if __name__ == "__main__":
    main()
