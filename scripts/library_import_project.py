#!/usr/bin/env python3
"""Wrapper for library import-project (use palimpsest CLI)."""

import sys

from palimpsest.commands.library import main


if __name__ == "__main__":
    main(["library", "import-project", *sys.argv[1:]])
