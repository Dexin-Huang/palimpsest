#!/usr/bin/env python3
"""Wrapper for library run (use palimpsest CLI)."""

import sys

from palimpsest.commands.library import main


if __name__ == "__main__":
    main(["library", "run", *sys.argv[1:]])
