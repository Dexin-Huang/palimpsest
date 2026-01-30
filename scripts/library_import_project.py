#!/usr/bin/env python3
"""Import an existing project into the library layout."""

import argparse
import shutil
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT
from palimpsest.library.intake import ingest_document
from palimpsest.library.io import read_json
from palimpsest.library.metadata import update_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a legacy project into library/")
    parser.add_argument("--doc-id", required=True, help="Document ID")
    parser.add_argument("--manifest", required=True, help="IIIF manifest URL")
    parser.add_argument("--project-dir", required=True, help="Legacy project directory")
    parser.add_argument("--images-dir", help="Explicit images directory")
    parser.add_argument("--exports-dir", help="Explicit exports directory")
    parser.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--title")
    parser.add_argument("--date")
    parser.add_argument("--language")
    parser.add_argument("--collection")
    parser.add_argument("--source-url")
    return parser


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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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

    from palimpsest.library.iiif import build_page_list

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


if __name__ == "__main__":
    main()
