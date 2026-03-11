from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.book import assemble_book
from palimpsest.canonical import export_canonical_pages
from palimpsest.reader import build_packet_book_site
from palimpsest.restoration import assemble_diplomatic_book
from palimpsest.reader import build_witness_reader_site


def cmd_assemble(args: argparse.Namespace) -> None:
    index = assemble_book(Path(args.transcriptions_dir))
    print(f"Assembled {index['total_pages']} pages")
    if index.get("missing_final"):
        print(f"Missing final pages: {len(index['missing_final'])}")


def cmd_canonicalize(args: argparse.Namespace) -> None:
    manifest = export_canonical_pages(
        transcriptions_dir=Path(args.transcriptions_dir),
        image_dir=Path(args.images_dir) if args.images_dir else None,
        out_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print(f"Canonical pages exported: {manifest['total_pages']}")


def cmd_restore(args: argparse.Namespace) -> None:
    index = assemble_diplomatic_book(
        Path(args.pages_dir),
        Path(args.output_dir) if args.output_dir else None,
    )
    print(f"Restored {index['total_pages']} pages")
    print(f"Book outputs: {index['outputs']['book']['json']}")


def cmd_site(args: argparse.Namespace) -> None:
    packet_paths: list[Path] = []
    if args.packets_dir:
        packet_paths.extend(sorted(Path(args.packets_dir).resolve().rglob("packet.json")))
    if args.packet:
        packet_paths.extend(Path(item).resolve() for item in args.packet)
    unique_paths = []
    seen: set[Path] = set()
    for path in packet_paths:
        if path in seen:
            continue
        seen.add(path)
        unique_paths.append(path)
    artifact = build_packet_book_site(
        unique_paths,
        out_dir=Path(args.output_dir).resolve(),
        title=args.title,
    )
    print(f"index: {artifact.index_path}")
    print(f"contents: {artifact.contents_path}")
    print(f"ending: {artifact.ending_path}")
    print(f"folios: {len(artifact.folio_paths)}")
    print(f"meta: {artifact.meta_path}")


def cmd_reader(args: argparse.Namespace) -> None:
    artifact = build_witness_reader_site(
        Path(args.doc_dir).resolve(),
        out_dir=Path(args.output_dir).resolve(),
        title=args.title,
    )
    print(f"index: {artifact.index_path}")
    print(f"contents: {artifact.contents_path}")
    print(f"ending: {artifact.ending_path}")
    print(f"folios: {len(artifact.folio_paths)}")
    print(f"meta: {artifact.meta_path}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("book", help="Book assembly utilities")
    sub = parser.add_subparsers(dest="book_cmd", required=True)

    assemble = sub.add_parser("assemble", help="Assemble book-level outputs")
    assemble.add_argument(
        "--transcriptions-dir",
        required=True,
        help="Directory containing *_final.json files",
    )
    assemble.set_defaults(func=cmd_assemble)

    canonicalize = sub.add_parser(
        "canonicalize",
        help="Export canonical.page JSON files from legacy *_final.json transcriptions",
    )
    canonicalize.add_argument(
        "--transcriptions-dir",
        required=True,
        help="Directory containing *_final.json files",
    )
    canonicalize.add_argument(
        "--images-dir",
        help="Optional directory containing source images used by transcription",
    )
    canonicalize.add_argument(
        "--output-dir",
        help="Optional output directory for canonical.page artifacts",
    )
    canonicalize.set_defaults(func=cmd_canonicalize)

    restore = sub.add_parser(
        "restore",
        help="Assemble diplomatic restoration outputs from canonical.page JSON files",
    )
    restore.add_argument(
        "--pages-dir",
        required=True,
        help="Directory containing canonical.page JSON files",
    )
    restore.add_argument(
        "--output-dir",
        help="Optional output directory for restoration artifacts",
    )
    restore.set_defaults(func=cmd_restore)

    site = sub.add_parser(
        "site",
        help="Build a static HTML folio site from page packets",
    )
    site.add_argument(
        "--packets-dir",
        help="Directory to scan recursively for packet.json files",
    )
    site.add_argument(
        "--packet",
        action="append",
        help="Explicit packet.json path; repeat for multiple pages",
    )
    site.add_argument(
        "--output-dir",
        required=True,
        help="Output directory for the generated site",
    )
    site.add_argument(
        "--title",
        help="Optional title override for the generated book/site",
    )
    site.set_defaults(func=cmd_site)

    reader = sub.add_parser(
        "reader",
        help="Build a static HTML reader directly from page witness artifacts",
    )
    reader.add_argument(
        "--doc-dir",
        required=True,
        help="Library document directory containing images, page_list.json, and experiments/",
    )
    reader.add_argument(
        "--output-dir",
        required=True,
        help="Output directory for the generated reader site",
    )
    reader.add_argument(
        "--title",
        help="Optional title override for the generated reader",
    )
    reader.set_defaults(func=cmd_reader)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Book commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
