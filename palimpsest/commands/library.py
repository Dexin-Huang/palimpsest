from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.download import download_pages
from palimpsest.library.iiif import build_page_list
from palimpsest.library.intake import ingest_document
from palimpsest.library.io import read_json
from palimpsest.library.metadata import update_metadata
from palimpsest.library.run import run_document


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


def cmd_run(args: argparse.Namespace) -> None:
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
        master_path=Path(args.master) if args.master else None,
    )


def cmd_status(args: argparse.Namespace) -> None:
    registry_path = Path(args.library_root) / "index.jsonl"
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


def _copy_tree(src: Path, dst: Path, overwrite: bool) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            continue
        shutil.copy2(item, target)


def _resolve_images_dir(project_dir: Path, override: str | None) -> Path:
    if override:
        return Path(override)
    images_dir = project_dir / "images"
    if not images_dir.exists():
        raise ValueError("images/ not found; use --images-dir")
    subdirs = [p for p in images_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]
    if any(p.is_file() for p in images_dir.iterdir()):
        return images_dir
    raise ValueError("images/ has multiple subfolders; use --images-dir")


def _resolve_exports_dir(project_dir: Path, override: str | None) -> Path:
    if override:
        return Path(override)
    exports_dir = project_dir / "exports"
    if not exports_dir.exists():
        raise ValueError("exports/ not found; use --exports-dir")
    return exports_dir


def cmd_import_project(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir)
    images_src = _resolve_images_dir(project_dir, args.images_dir)
    exports_src = _resolve_exports_dir(project_dir, args.exports_dir)

    metadata = {
        "title": args.title or "",
        "date": args.date or "",
        "language": args.language or "",
        "collection": args.collection or "",
        "source_url": args.source_url or args.manifest,
        "status": "ingested",
    }

    page_list = build_page_list(args.manifest, size="max")
    doc_dir = ingest_document(
        doc_id=args.doc_id,
        metadata=metadata,
        page_list=page_list,
        library_root=Path(args.library_root),
    )

    _copy_tree(images_src, doc_dir / "images", args.overwrite)
    _copy_tree(exports_src / "transcriptions_full", doc_dir / "exports" / "transcriptions_full", args.overwrite)
    _copy_tree(exports_src / "book", doc_dir / "exports" / "book", args.overwrite)

    book_index = doc_dir / "exports" / "book" / "book_index.json"
    if book_index.exists():
        index = read_json(book_index)
        missing_final = index.get("missing_final", [])
        status = "assembled_partial" if missing_final else "assembled"
        update_metadata(
            doc_dir,
            {"status": status, "missing_final_count": len(missing_final)},
        )
    else:
        update_metadata(doc_dir, {"status": "assembled_missing"})

    print(f"Imported {args.doc_id} into {doc_dir}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("library", help="Library utilities")
    sub = parser.add_subparsers(dest="library_cmd", required=True)

    intake = sub.add_parser("intake", help="Create a library entry")
    intake.add_argument("--doc-id", required=True, help="Document ID")
    intake.add_argument("--metadata", help="Path to metadata JSON")
    intake.add_argument("--page-list", help="Path to page_list JSON")
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

    run = sub.add_parser("run", help="Run download + transcription + assembly")
    run.add_argument("--doc-id", required=True, help="Document ID")
    run.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    run.add_argument("--prompt-set", default="transcription_json", help="Prompt set name")
    run.add_argument("--pass-mode", default="both", choices=["both", "pass1", "pass2"])
    run.add_argument("--workers", type=int, default=10)
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--delay", type=float, default=2.0)
    run.add_argument("--auto-skip-non-text", action="store_true")
    run.add_argument("--skip-download", action="store_true")
    run.add_argument("--pattern", default="*.jpg", help="Image glob pattern (default: *.jpg)")
    run.add_argument("--master", help="Master discovery JSONL to sync (optional)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="Summarize library status")
    status.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    status.add_argument("--status", help="Filter by status")
    status.add_argument("--doc-id", help="Show a single document entry")
    status.add_argument("--list", action="store_true", help="List doc_id and status")
    status.add_argument("--limit", type=int, default=50, help="Max rows when listing")
    status.set_defaults(func=cmd_status)

    imp = sub.add_parser("import-project", help="Import a legacy project into library/")
    imp.add_argument("--doc-id", required=True, help="Document ID")
    imp.add_argument("--manifest", required=True, help="IIIF manifest URL")
    imp.add_argument("--project-dir", required=True, help="Legacy project directory")
    imp.add_argument("--images-dir", help="Explicit images directory")
    imp.add_argument("--exports-dir", help="Explicit exports directory")
    imp.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    imp.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    imp.add_argument("--title")
    imp.add_argument("--date")
    imp.add_argument("--language")
    imp.add_argument("--collection")
    imp.add_argument("--source-url")
    imp.set_defaults(func=cmd_import_project)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Library commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)

