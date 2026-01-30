#!/usr/bin/env python3
"""Wrapper for library intake (use palimpsest CLI)."""

import sys

from palimpsest.commands.library import main


if __name__ == "__main__":
    main(["library", "intake", *sys.argv[1:]])
