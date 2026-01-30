#!/usr/bin/env python3
"""Wrapper for discovery master commands (use palimpsest CLI)."""

import sys

from palimpsest.commands.discovery import main


if __name__ == "__main__":
    main(["discovery", "master", *sys.argv[1:]])
