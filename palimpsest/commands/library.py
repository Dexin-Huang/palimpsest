from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from palimpsest.contracts import (
    CLEANED_IMAGES_DIRNAME,
    PAGE_LIST_FILENAME,
    library_registry_path,
)
from palimpsest.library.clean import CleanConfig, clean_document
from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.download import download_pages
from palimpsest.library.iiif import build_page_list
from palimpsest.library.intake import ingest_document


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_intake(args: argparse.Namespace) -> None:
    metadata: dict = {}
    if args.metadata:
        metadata.update(_load_json(Path(args.metadata)))

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
        page_list = _load_json(Path(args.page_list))
    elif args.manifest:
        page_list = build_page_list(args.manifest, size=args.size)
        if "source_url" not in metadata:
            metadata["source_url"] = args.manifest
        manifest_summary = page_list.get("manifest_summary") or {}
        if manifest_summary:
            metadata.setdefault("source_catalog", manifest_summary)
            if manifest_summary.get("label"):
                metadata.setdefault("source_label", manifest_summary["label"])
            if manifest_summary.get("title") and not manifest_summary.get("title_is_shelfmark"):
                metadata.setdefault("title", manifest_summary["title"])
            if manifest_summary.get("date"):
                metadata.setdefault("date", manifest_summary["date"])
            if manifest_summary.get("language"):
                metadata.setdefault("language", manifest_summary["language"])
            if manifest_summary.get("place"):
                metadata.setdefault("place", manifest_summary["place"])
    else:
        raise SystemExit("Provide --page-list or --manifest")

    library_root = Path(args.library_root)
    doc_dir = ingest_document(
        doc_id=args.doc_id,
        metadata=metadata,
        page_list=page_list,
        library_root=library_root,
    )

    print(f"Created library entry: {doc_dir}")
    print(f"Pages: {len(page_list.get('pages', []))}")


def cmd_download(args: argparse.Namespace) -> None:
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


def cmd_clean(args: argparse.Namespace) -> None:
    doc_dir = Path(args.library_root) / args.doc_id
    if not doc_dir.exists():
        raise SystemExit(f"Missing document folder: {doc_dir}")

    config = CleanConfig(
        k_high=args.k_high,
        k_low=args.k_low,
        hysteresis_window=args.hysteresis_window,
        sauvola_k=args.sauvola_k,
        sauvola_window=args.sauvola_window,
        sauvola_median=args.sauvola_median,
        hysteresis_weight=args.hysteresis_weight,
        sauvola_weight=args.sauvola_weight,
        binarization_weight=args.binarization_weight,
        use_docres=not args.no_docres,
        docres_max_dim=args.docres_max_dim,
    )

    result = clean_document(
        doc_dir=doc_dir,
        output_subdir=args.output_dir,
        pattern=args.pattern,
        workers=args.workers,
        limit=args.limit,
        overwrite=args.overwrite,
        config=config,
    )

    print(
        f"Cleaned {result['processed']} pages "
        f"(failed: {result.get('failed', 0)}) -> {result.get('output_dir', '')}"
    )


def cmd_status(args: argparse.Namespace) -> None:
    registry_path = library_registry_path(Path(args.library_root))
    entries = []
    if registry_path.exists():
        for line in registry_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if args.doc_id:
        for entry in entries:
            if entry.get("doc_id") == args.doc_id:
                print(json.dumps(entry, indent=2))
                return
        raise SystemExit(f"doc_id not found: {args.doc_id}")

    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]

    counts = Counter(e.get("status", "unknown") for e in entries)
    total = sum(counts.values())
    print(f"Total: {total}")
    for status, count in counts.most_common():
        print(f"{status}: {count}")

    if args.list:
        for entry in entries[: args.limit]:
            print(f"{entry.get('doc_id','')}  {entry.get('status','')}")

def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "library",
        help="Canonical library intake utilities",
        description="Canonical library commands are `intake`, `download`, `status`, and `clean`.",
    )
    sub = parser.add_subparsers(dest="library_cmd", required=True)

    intake = sub.add_parser("intake", help="Create a canonical library entry")
    intake.add_argument("--doc-id", required=True, help="Document ID")
    intake.add_argument("--metadata", help="Path to metadata JSON")
    intake.add_argument("--page-list", help=f"Path to {PAGE_LIST_FILENAME}")
    intake.add_argument("--manifest", help="IIIF manifest URL")
    intake.add_argument("--size", default="max", help="IIIF image size (default: max)")
    intake.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    intake.add_argument("--title")
    intake.add_argument("--date")
    intake.add_argument("--language")
    intake.add_argument("--collection")
    intake.add_argument("--source-url")
    intake.add_argument("--triage-score", type=float)
    intake.add_argument("--triage-reason")
    intake.add_argument("--status", default="ingested")
    digitized = intake.add_mutually_exclusive_group()
    digitized.add_argument("--newly-digitized", action="store_true")
    digitized.add_argument("--not-newly-digitized", action="store_true")
    intake.set_defaults(func=cmd_intake)

    download = sub.add_parser("download", help="Download images for a library document")
    download.add_argument("--doc-id", required=True, help="Document ID")
    download.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    download.add_argument("--overwrite", action="store_true", help="Redownload existing files")
    download.add_argument("--delay", type=float, default=0.5, help="Delay between downloads (seconds)")
    download.add_argument("--timeout", type=float, default=30.0, help="Request timeout (seconds)")
    download.set_defaults(func=cmd_download)

    status = sub.add_parser("status", help="Summarize library status")
    status.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    status.add_argument("--status", help="Filter by status")
    status.add_argument("--doc-id", help="Show a single document entry")
    status.add_argument("--list", action="store_true", help="List doc_id and status")
    status.add_argument("--limit", type=int, default=50, help="Max rows when listing")
    status.set_defaults(func=cmd_status)

    # Clean subparser
    clean = sub.add_parser("clean", help="Clean bleed-through from manuscript images")
    clean.add_argument("--doc-id", required=True, help="Document ID")
    clean.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    clean.add_argument("--output-dir", default=CLEANED_IMAGES_DIRNAME, help="Output subfolder name")
    clean.add_argument("--pattern", default="*.jpg", help="Image glob pattern")
    clean.add_argument("--limit", type=int, help="Max images to process")
    clean.add_argument("--workers", type=int, default=4, help="Parallel workers (CPU)")
    clean.add_argument("--overwrite", action="store_true", help="Overwrite existing cleaned images")
    # Hysteresis parameters
    clean.add_argument("--k-high", type=float, default=0.10, help="Hysteresis high threshold k")
    clean.add_argument("--k-low", type=float, default=0.20, help="Hysteresis low threshold k")
    clean.add_argument("--hysteresis-window", type=int, default=25, help="Hysteresis window size")
    # Sauvola parameters
    clean.add_argument("--sauvola-k", type=float, default=0.30, help="Sauvola k parameter")
    clean.add_argument("--sauvola-window", type=int, default=25, help="Sauvola window size")
    clean.add_argument("--sauvola-median", type=int, default=3, help="Sauvola median filter size")
    # Blend weights
    clean.add_argument("--hysteresis-weight", type=float, default=0.33, help="Hysteresis blend weight")
    clean.add_argument("--sauvola-weight", type=float, default=0.34, help="Sauvola blend weight")
    clean.add_argument("--binarization-weight", type=float, default=0.33, help="DocRes blend weight")
    # DocRes options
    clean.add_argument("--no-docres", action="store_true", help="Skip DocRes (CPU-only mode)")
    clean.add_argument("--docres-max-dim", type=int, default=1800, help="Max dimension for DocRes")
    clean.set_defaults(func=cmd_clean)



def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Library commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
