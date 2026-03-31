from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.contracts import EXPERIMENTS_DIRNAME, PAGE_LIST_FILENAME
from palimpsest.reader import build_witness_reader_site


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
        help="Canonical reader commands",
        description="Canonical book commands: `reader`.",
    )
    sub = parser.add_subparsers(dest="book_cmd", required=True)

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
