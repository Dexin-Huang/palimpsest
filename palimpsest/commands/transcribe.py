from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING, DEFAULT_MODEL_TRANSCRIPTION
from palimpsest.transcribe import DEFAULT_PROMPT_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_WORKERS


def _infer_output_dir(image_dir: Path) -> Path:
    """Infer transcription output dir as sibling of images/."""
    image_dir = image_dir.resolve()
    return image_dir.parent / "transcription"


def _resolve_output_dir(output_dir: str | None) -> Path:
    """Resolve output dir from arg or cwd."""
    if output_dir:
        return Path(output_dir).resolve()
    cwd = Path.cwd()
    if (cwd / "batch_manifest.json").exists() or (cwd / "transcriptions.jsonl").exists():
        return cwd
    raise ValueError("No --output-dir specified and current directory has no batch_manifest.json or transcriptions.jsonl")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> None:
    from palimpsest.transcribe import run_transcription_sync

    image_dir = Path(args.image_dir)
    output = Path(args.output) if args.output else _infer_output_dir(image_dir) / "transcriptions.jsonl"

    run_transcription_sync(
        image_dir,
        output_path=output,
        source=args.source or "",
        book_title=args.book_title or "",
        model=args.model,
        prompt_name=args.prompt_name,
        system_prompt=args.system_prompt,
        workers=args.workers,
        skip_existing=args.skip_existing,
    )


def cmd_batch_submit(args: argparse.Namespace) -> None:
    from palimpsest.batch import create_manifest, load_manifest, submit_batch, print_status

    image_dir = Path(args.image_dir)
    output_dir = Path(args.output_dir) if args.output_dir else _infer_output_dir(image_dir)
    manifest_path = output_dir / "batch_manifest.json"

    if manifest_path.exists():
        manifest = load_manifest(output_dir)
        if args.model != DEFAULT_MODEL_TRANSCRIPTION and args.model != manifest["config"]["model"]:
            print(f"WARNING: manifest already exists with model={manifest['config']['model']}, ignoring --model={args.model}")
            print(f"  To use a different model, delete {manifest_path} and re-submit.")
    else:
        create_manifest(
            image_dir, output_dir,
            source=args.source or "", book_title=args.book_title or "",
            model=args.model, prompt_name=args.prompt_name, system_prompt=args.system_prompt,
        )
        print(f"Manifest created: {manifest_path}")

    print(f"Output directory: {output_dir}")
    manifest = submit_batch(output_dir)
    print_status(manifest)


def cmd_batch_status(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, print_status
    print_status(poll_batch(_resolve_output_dir(args.output_dir)))


def cmd_batch_collect(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, collect_batch, print_status

    output_dir = _resolve_output_dir(args.output_dir)
    manifest = poll_batch(output_dir)
    print_status(manifest)

    if manifest["status"] not in ("completed", "partial"):
        print(f"\nBatch not ready for collection (status: {manifest['status']})")
        return

    print(f"\nCollected to: {collect_batch(output_dir)}")


def cmd_unpack(args: argparse.Namespace) -> None:
    from palimpsest.batch import unpack_transcription

    summary = unpack_transcription(_resolve_output_dir(args.output_dir))
    if summary["flagged_pages"]:
        print(f"\nFlagged pages:")
        for page_id, flags in summary["flags"].items():
            print(f"  {page_id}: {', '.join(flags)}")


def cmd_survey(args: argparse.Namespace) -> None:
    from palimpsest.survey import run_survey_sync

    input_path = Path(args.input) if args.input else _resolve_output_dir(args.output_dir) / "transcriptions.jsonl"
    output_path = Path(args.output) if args.output else input_path.parent / "translation_brief.json"

    print(f"Input: {input_path}")
    run_survey_sync(input_path, output_path, model=args.model)


def cmd_enrich(args: argparse.Namespace) -> None:
    from palimpsest.enrich import run_batch_translation_sync

    input_path = Path(args.input) if args.input else _resolve_output_dir(args.output_dir) / "transcriptions.jsonl"
    brief_path = Path(args.brief) if args.brief else input_path.parent / "translation_brief.json"
    output_path = Path(args.output) if args.output else input_path.parent / "enriched.jsonl"

    if not brief_path.exists():
        print(f"No translation brief at {brief_path}. Running survey first...")
        from palimpsest.survey import run_survey_sync
        run_survey_sync(input_path, brief_path, model=args.model)

    print(f"Input: {input_path}")
    print(f"Brief: {brief_path}")
    print(f"Output: {output_path}")

    run_batch_translation_sync(
        input_path, output_path, brief_path,
        model=args.model, workers=args.workers, skip_existing=args.skip_existing,
    )


def cmd_publish(args: argparse.Namespace) -> None:
    from palimpsest.publish import build_book_site

    input_path = Path(args.input) if args.input else _resolve_output_dir(args.output_dir) / "enriched.jsonl"
    if not input_path.exists():
        fallback = input_path.parent / "transcriptions.jsonl"
        if fallback.exists():
            input_path = fallback
            print(f"No enriched.jsonl found, using transcriptions.jsonl")

    out_dir = Path(args.output_dir) if args.output_dir else input_path.parent / "site"
    image_dir = Path(args.image_dir) if args.image_dir else None

    print(f"Input: {input_path}")
    print(f"Output: {out_dir}")
    print(f"Published: {build_book_site(input_path, out_dir=out_dir, title=args.title, image_dir=image_dir)}")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transcribe", help="Single-call VLM transcription")
    sub = parser.add_subparsers(dest="transcribe_command", required=True)

    # Interactive run
    run_p = sub.add_parser("run", help="Transcribe page images to JSONL (interactive, real-time)")
    run_p.add_argument("--image-dir", required=True)
    run_p.add_argument("--output", default=None, help="Output JSONL (default: inferred)")
    run_p.add_argument("--source", default="")
    run_p.add_argument("--book-title", default="")
    run_p.add_argument("--model", default=DEFAULT_MODEL_TRANSCRIPTION)
    run_p.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME)
    run_p.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    run_p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run_p.add_argument("--skip-existing", action="store_true")
    run_p.set_defaults(func=cmd_run)

    # Batch submit
    bs = sub.add_parser("batch-submit", help="Submit batch transcription jobs (half price)")
    bs.add_argument("--image-dir", required=True)
    bs.add_argument("--output-dir", default=None)
    bs.add_argument("--source", default="")
    bs.add_argument("--book-title", default="")
    bs.add_argument("--model", default=DEFAULT_MODEL_TRANSCRIPTION)
    bs.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME)
    bs.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    bs.set_defaults(func=cmd_batch_submit)

    # Batch status
    bst = sub.add_parser("batch-status", help="Poll batch job status")
    bst.add_argument("--output-dir", default=None)
    bst.set_defaults(func=cmd_batch_status)

    # Batch collect
    bc = sub.add_parser("batch-collect", help="Collect batch results into JSONL")
    bc.add_argument("--output-dir", default=None)
    bc.set_defaults(func=cmd_batch_collect)

    # Unpack
    up = sub.add_parser("unpack", help="Unpack JSONL into per-page text + full text + quality flags")
    up.add_argument("--output-dir", default=None)
    up.set_defaults(func=cmd_unpack)

    # Survey
    sv = sub.add_parser("survey", help="Build translation brief (glossary, outline, terms)")
    sv.add_argument("--input", default=None)
    sv.add_argument("--output", default=None)
    sv.add_argument("--output-dir", default=None)
    sv.add_argument("--model", default=DEFAULT_MODEL_READING)
    sv.set_defaults(func=cmd_survey)

    # Enrich (translate with brief + overlap + repair)
    en = sub.add_parser("enrich", help="Translate with glossary brief, overlap context, and boundary repair")
    en.add_argument("--input", default=None)
    en.add_argument("--output", default=None)
    en.add_argument("--brief", default=None, help="Translation brief JSON (auto-runs survey if missing)")
    en.add_argument("--output-dir", default=None)
    en.add_argument("--model", default=DEFAULT_MODEL_READING)
    en.add_argument("--workers", type=int, default=8)
    en.add_argument("--skip-existing", action="store_true")
    en.set_defaults(func=cmd_enrich)

    # Publish
    pub = sub.add_parser("publish", help="Generate static HTML book site from JSONL")
    pub.add_argument("--input", default=None)
    pub.add_argument("--output-dir", default=None)
    pub.add_argument("--image-dir", default=None)
    pub.add_argument("--title", default=None)
    pub.set_defaults(func=cmd_publish)
