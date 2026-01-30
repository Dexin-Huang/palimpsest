#!/usr/bin/env python3
"""Wrapper for sharded transcription runs (use palimpsest CLI)."""

import sys

from palimpsest.commands.transcription import main


if __name__ == "__main__":
    main(["transcribe", "shards", *sys.argv[1:]])
