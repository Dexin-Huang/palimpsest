#!/usr/bin/env python3
"""Wrapper for book assembly (use palimpsest CLI)."""

import sys

from palimpsest.commands.book import main


if __name__ == "__main__":
    main(["book", "assemble", *sys.argv[1:]])
