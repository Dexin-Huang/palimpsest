"""CLI parser wiring surface.

This package root intentionally exposes only the subparser registration
functions used by ``palimpsest.cli``. It keeps those exports lazy so importing
``palimpsest.commands`` does not eagerly pull in the full command dependency
graph. Command workflow logic lives in the individual command modules and
should not be treated as a broad package API.
"""

from __future__ import annotations

import argparse


def add_discovery_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from .discovery import add_subparser

    add_subparser(subparsers)


def add_library_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from .library import add_subparser

    add_subparser(subparsers)


def add_transcribe_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from .transcribe import add_subparser

    add_subparser(subparsers)

__all__ = [
    "add_discovery_subparser",
    "add_library_subparser",
    "add_transcribe_subparser",
]
