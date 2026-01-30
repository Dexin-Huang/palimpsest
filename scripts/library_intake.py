#!/usr/bin/env python3
"""Create a library entry from metadata and a page list (or IIIF manifest)."""

import argparse
import json
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.iiif import build_page_list
from palimpsest.library.intake import ingest_document


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a library entry")
    parser.add_argument("--doc-id", required=True, help="Document ID")
    parser.add_argument("--metadata", help="Path to metadata JSON")
    parser.add_argument("--page-list", help="Path to page_list JSON")
    parser.add_argument("--manifest", help="IIIF manifest URL")
    parser.add_argument("--size", default="max", help="IIIF image size (default: max)")
    parser.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    parser.add_argument("--title")
    parser.add_argument("--date")
    parser.add_argument("--language")
    parser.add_argument("--collection")
    parser.add_argument("--source-url")
    parser.add_argument("--triage-score", type=float)
    parser.add_argument("--triage-reason")
    parser.add_argument("--status", default="ingested")

    digitized = parser.add_mutually_exclusive_group()
    digitized.add_argument("--newly-digitized", action="store_true")
    digitized.add_argument("--not-newly-digitized", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    metadata: dict = {}
    if args.metadata:
        metadata.update(load_json(Path(args.metadata)))

    if args.title:
        metadata["title"] = args.title
    if args.date:
        metadata["date"] = args.date
    if args.language:
        metadata["language"] = args.language
    if args.collection:
        metadata["collection"] = args.collection
    if args.source_url:
        metadata["source_url"] = args.source_url
    if args.triage_score is not None:
        metadata["triage_score"] = args.triage_score
    if args.triage_reason:
        metadata["triage_reason"] = args.triage_reason
    if args.status:
        metadata["status"] = args.status
    if args.newly_digitized:
        metadata["newly_digitized"] = True
    if args.not_newly_digitized:
        metadata["newly_digitized"] = False

    if args.page_list:
        page_list = load_json(Path(args.page_list))
    elif args.manifest:
        page_list = build_page_list(args.manifest, size=args.size)
        if "source_url" not in metadata:
            metadata["source_url"] = args.manifest
    else:
        parser.error("Provide --page-list or --manifest")

    library_root = Path(args.library_root)
    doc_dir = ingest_document(
        doc_id=args.doc_id,
        metadata=metadata,
        page_list=page_list,
        library_root=library_root,
    )

    print(f"Created library entry: {doc_dir}")
    print(f"Pages: {len(page_list.get('pages', []))}")


if __name__ == "__main__":
    main()
