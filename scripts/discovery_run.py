#!/usr/bin/env python3
"""Wrapper for discovery run (use palimpsest CLI)."""

import sys

from palimpsest.commands.discovery import main


if __name__ == "__main__":
    main(["discovery", "run", *sys.argv[1:]])
