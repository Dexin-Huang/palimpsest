from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.book import assemble_book


def cmd_assemble(args: argparse.Namespace) -> None:
    index = assemble_book(Path(args.transcriptions_dir))
    print(f"Assembled {index['total_pages']} pages")
    if index.get("missing_final"):
        print(f"Missing final pages: {len(index['missing_final'])}")


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Book commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)

