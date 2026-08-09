from __future__ import annotations

import argparse

from palimpsest.catalog.cli import add_catalog_commands
from palimpsest.factory.cli import add_commands
from palimpsest.survey import add_survey_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Palimpsest CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_catalog_commands(subparsers)
    add_commands(subparsers)
    add_survey_command(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
