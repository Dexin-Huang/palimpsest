#!/usr/bin/env python3
"""Wrapper for transcription run (use palimpsest CLI)."""

import sys

from palimpsest.commands.transcription import main


if __name__ == "__main__":
    main(["transcribe", "run", *sys.argv[1:]])
