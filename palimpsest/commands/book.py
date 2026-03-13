from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.contracts import EXPERIMENTS_DIRNAME, PACKET_FILENAME, PAGE_LIST_FILENAME
from palimpsest.reader import build_packet_book_site
from palimpsest.reader import build_witness_reader_site


def cmd_site(args: argparse.Namespace) -> None:
    packet_paths: list[Path] = []
    if args.packets_dir:
        packet_paths.extend(sorted(Path(args.packets_dir).resolve().rglob(PACKET_FILENAME)))
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
    parser = subparsers.add_parser(
        "book",
        help="Canonical reader and packet-site commands",
        description="Canonical book commands are `site` and `reader`.",
    )
    sub = parser.add_subparsers(dest="book_cmd", required=True)

    site = sub.add_parser(
        "site",
        help="Build the canonical static HTML folio site from page packets",
    )
    site.add_argument(
        "--packets-dir",
        help=f"Directory to scan recursively for {PACKET_FILENAME} files",
    )
    site.add_argument(
        "--packet",
        action="append",
        help=f"Explicit {PACKET_FILENAME} path; repeat for multiple pages",
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
        help="Build the canonical static HTML reader directly from page witness artifacts",
    )
    reader.add_argument(
        "--doc-dir",
        required=True,
        help=f"Library document directory containing images, {PAGE_LIST_FILENAME}, and {EXPERIMENTS_DIRNAME}/",
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
