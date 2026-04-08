from __future__ import annotations

import argparse

from palimpsest.commands import (
    add_discovery_subparser,
    add_library_subparser,
    add_transcribe_subparser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Palimpsest CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_discovery_subparser(subparsers)
    add_library_subparser(subparsers)
    add_transcribe_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
