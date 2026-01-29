#!/usr/bin/env python3
"""Orchestrate sharded transcription runs."""

import argparse
import subprocess
import sys
from pathlib import Path

from palimpsest.transcription import DEFAULT_MODEL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sharded transcription passes")
    parser.add_argument("--image-dir", required=True, help="Directory of images to transcribe")
    parser.add_argument("--out-dir", required=True, help="Output directory for transcriptions")
    parser.add_argument("--prompt", help="Legacy prompt base name")
    parser.add_argument("--prompt-set", help="Prompt set folder under palimpsest/prompts/sets")
    parser.add_argument("--prompt-pass1", help="Explicit path to pass1 prompt file")
    parser.add_argument("--prompt-pass2", help="Explicit path to pass2 prompt file")
    parser.add_argument("--pattern", default="*.jpg", help="Glob pattern for images (default: *.jpg)")
    parser.add_argument("--model", default=None, help="Override model (gemini-3-flash-preview only)")
    parser.add_argument("--pass-mode", default="both", choices=["both", "pass1", "pass2"], help="Pass mode when not using --two-phase")
    parser.add_argument("--two-phase", action="store_true", help="Run pass1 across shards, then pass2")
    parser.add_argument("--shards", type=int, default=1, help="Number of shards (default: 1)")
    parser.add_argument("--workers-per-shard", type=int, default=10, help="Workers per shard (default: 10)")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max attempts per pass")
    parser.add_argument("--no-trace", action="store_true", help="Disable trace capture")
    parser.add_argument("--skip-existing", action="store_true", help="Skip pages with existing outputs")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser


def build_base_args(args) -> list[str]:
    cmd = [sys.executable, "scripts/transcribe_manuscript.py"]
    cmd += ["--image-dir", args.image_dir]
    cmd += ["--out-dir", args.out_dir]
    cmd += ["--pattern", args.pattern]
    cmd += ["--workers", str(args.workers_per_shard)]
    cmd += ["--max-attempts", str(args.max_attempts)]
    if args.model:
        cmd += ["--model", args.model]
    if args.prompt:
        cmd += ["--prompt", args.prompt]
    if args.prompt_set:
        cmd += ["--prompt-set", args.prompt_set]
    if args.prompt_pass1:
        cmd += ["--prompt-pass1", args.prompt_pass1]
    if args.prompt_pass2:
        cmd += ["--prompt-pass2", args.prompt_pass2]
    if args.no_trace:
        cmd += ["--no-trace"]
    if args.skip_existing:
        cmd += ["--skip-existing"]
    if args.verbose:
        cmd += ["--verbose"]
    return cmd


def run_phase(args, pass_mode: str) -> int:
    base = build_base_args(args)
    procs = []
    for shard in range(args.shards):
        cmd = list(base)
        cmd += ["--pass-mode", pass_mode]
        cmd += ["--shard-count", str(args.shards)]
        cmd += ["--shard-index", str(shard)]
        procs.append(subprocess.Popen(cmd))
    exit_codes = [p.wait() for p in procs]
    failed = [code for code in exit_codes if code != 0]
    return 1 if failed else 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not (args.prompt or args.prompt_set or (args.prompt_pass1 and args.prompt_pass2)):
        raise SystemExit("Error: provide --prompt, --prompt-set, or both --prompt-pass1/--prompt-pass2")
    if args.shards < 1:
        raise SystemExit("Error: --shards must be >= 1")

    if args.model and args.model != DEFAULT_MODEL:
        raise SystemExit(f"Error: only model allowed is {DEFAULT_MODEL}")

    if args.two_phase:
        code = run_phase(args, "pass1")
        if code != 0:
            raise SystemExit(code)
        code = run_phase(args, "pass2")
        raise SystemExit(code)

    code = run_phase(args, args.pass_mode)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
