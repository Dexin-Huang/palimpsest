from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from palimpsest.transcription import DEFAULT_MODEL, PromptConfig, RunConfig, run_batch, run_single


def _build_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run = subparsers.add_parser("run", help="Run two-pass transcription")
    input_group = run.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--image", help="Single image file to transcribe")
    input_group.add_argument("--image-dir", help="Directory of images to transcribe")

    run.add_argument("--out-dir", required=True, help="Output directory for transcriptions")
    run.add_argument("--prompt", help="Legacy prompt base name (loads <name>.txt and <name>_refine.txt)")
    run.add_argument(
        "--prompt-set",
        default=None,
        help="Prompt set folder under palimpsest/prompts/sets (pass1.txt + pass2.txt). "
        "Defaults to transcription_json when none is provided.",
    )
    run.add_argument("--prompt-pass1", help="Explicit path to pass1 prompt file")
    run.add_argument("--prompt-pass2", help="Explicit path to pass2 prompt file")
    run.add_argument("--pattern", default="*.jpg", help="Glob pattern for images in batch mode (default: *.jpg)")
    run.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    run.add_argument("--output-format", default="json", choices=["json"], help="Output format (json only)")
    run.add_argument(
        "--pass-mode",
        default="both",
        choices=["both", "pass1", "pass2"],
        help="Which passes to run (default: both)",
    )
    run.add_argument("--workers", type=int, default=10, help="Number of parallel workers (default: 10)")
    run.add_argument("--max-attempts", type=int, default=3, help="Max attempts per pass (default: 3)")
    run.add_argument("--no-trace", action="store_true", help="Disable trace capture for faster runs")
    run.add_argument("--auto-skip-non-text", action="store_true", help="Auto-skip pass2 for low-text pages")
    run.add_argument("--shard-count", type=int, default=1, help="Total number of shards (default: 1)")
    run.add_argument("--shard-index", type=int, default=0, help="Shard index (0-based, default: 0)")
    run.add_argument("--skip-existing", action="store_true", help="Skip pages that already have output files")
    run.add_argument("--delay", type=float, default=2.0, help="Delay between API calls in seconds (default: 2.0)")
    run.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    run.set_defaults(func=cmd_run)


def _build_shards_parser(subparsers: argparse._SubParsersAction) -> None:
    shards = subparsers.add_parser("shards", help="Run sharded transcription passes")
    shards.add_argument("--image-dir", required=True, help="Directory of images to transcribe")
    shards.add_argument("--out-dir", required=True, help="Output directory for transcriptions")
    shards.add_argument("--prompt", help="Legacy prompt base name")
    shards.add_argument(
        "--prompt-set",
        default=None,
        help="Prompt set folder under palimpsest/prompts/sets. Defaults to transcription_json when none is provided.",
    )
    shards.add_argument("--prompt-pass1", help="Explicit path to pass1 prompt file")
    shards.add_argument("--prompt-pass2", help="Explicit path to pass2 prompt file")
    shards.add_argument("--pattern", default="*.jpg", help="Glob pattern for images (default: *.jpg)")
    shards.add_argument("--model", default=None, help="Override model")
    shards.add_argument("--pass-mode", default="both", choices=["both", "pass1", "pass2"])
    shards.add_argument("--two-phase", action="store_true", help="Run pass1 across shards, then pass2")
    shards.add_argument("--shards", type=int, default=1, help="Number of shards (default: 1)")
    shards.add_argument("--workers-per-shard", type=int, default=10, help="Workers per shard (default: 10)")
    shards.add_argument("--max-attempts", type=int, default=3, help="Max attempts per pass")
    shards.add_argument("--no-trace", action="store_true", help="Disable trace capture")
    shards.add_argument("--skip-existing", action="store_true", help="Skip pages with existing outputs")
    shards.add_argument("--verbose", action="store_true", help="Verbose output")
    shards.set_defaults(func=cmd_shards)


def _build_status_parser(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser("status", help="Show transcription run status")
    status.add_argument(
        "--status",
        required=True,
        help="Path to status.json (e.g., exports/transcriptions_full/_runs/status.json)",
    )
    status.set_defaults(func=cmd_status)


def _build_run_config(args: argparse.Namespace) -> RunConfig:
    prompt = PromptConfig(
        prompt_name=args.prompt,
        prompt_set=args.prompt_set,
        prompt_pass1=args.prompt_pass1,
        prompt_pass2=args.prompt_pass2,
    )
    return RunConfig(
        prompt=prompt,
        model=args.model,
        output_format=args.output_format,
        pass_mode=args.pass_mode,
        skip_existing=args.skip_existing,
        verbose=args.verbose,
        delay=args.delay,
        workers=args.workers,
        max_attempts=args.max_attempts,
        trace=not args.no_trace,
        auto_skip_non_text=args.auto_skip_non_text,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
    )


def cmd_run(args: argparse.Namespace) -> None:
    if not (args.prompt or args.prompt_set or (args.prompt_pass1 and args.prompt_pass2)):
        args.prompt_set = "transcription_json"
    if args.prompt or (args.prompt_pass1 and args.prompt_pass2):
        args.prompt_set = None
    run_config = _build_run_config(args)
    if run_config.shard_count < 1:
        raise SystemExit("Error: --shard-count must be >= 1")
    if run_config.shard_index < 0 or run_config.shard_index >= run_config.shard_count:
        raise SystemExit("Error: --shard-index must be within [0, shard-count)")

    if args.image:
        result = run_single(
            image_path=Path(args.image),
            out_dir=Path(args.out_dir),
            run_config=run_config,
        )
        if result["status"] != "complete":
            raise SystemExit(f"Failed: {result['status']}")
    else:
        run_batch(
            image_dir=Path(args.image_dir),
            out_dir=Path(args.out_dir),
            pattern=args.pattern,
            run_config=run_config,
        )


def _build_base_args(args: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, "-m", "palimpsest", "transcribe", "run"]
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


def _run_phase(args: argparse.Namespace, pass_mode: str) -> int:
    base = _build_base_args(args)
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


def cmd_shards(args: argparse.Namespace) -> None:
    if not (args.prompt or args.prompt_set or (args.prompt_pass1 and args.prompt_pass2)):
        args.prompt_set = "transcription_json"
    if args.prompt or (args.prompt_pass1 and args.prompt_pass2):
        args.prompt_set = None
    if args.shards < 1:
        raise SystemExit("Error: --shards must be >= 1")

    if args.two_phase:
        code = _run_phase(args, "pass1")
        if code != 0:
            raise SystemExit(code)
        code = _run_phase(args, "pass2")
        raise SystemExit(code)

    code = _run_phase(args, args.pass_mode)
    raise SystemExit(code)


def cmd_status(args: argparse.Namespace) -> None:
    status_path = Path(args.status)
    if not status_path.exists():
        raise SystemExit(f"Status file not found: {status_path}")

    status = json.loads(status_path.read_text(encoding="utf-8"))

    print(f"Updated: {status.get('generated_at')}")
    print(f"Expected: {status.get('expected_total')}")
    print(f"Pass1: {status.get('pass1_total')}")
    print(f"Final: {status.get('final_total')}")
    print(f"Missing pass1: {len(status.get('missing_pass1', []))}")
    print(f"Missing pass2: {len(status.get('missing_pass2', []))}")

    if status.get("missing_pass2"):
        sample = status["missing_pass2"][:10]
        print("Missing pass2 sample:")
        for item in sample:
            print(f"  - {item}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("transcribe", help="Transcription commands")
    sub = parser.add_subparsers(dest="transcribe_cmd", required=True)
    _build_run_parser(sub)
    _build_shards_parser(sub)
    _build_status_parser(sub)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transcription commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
