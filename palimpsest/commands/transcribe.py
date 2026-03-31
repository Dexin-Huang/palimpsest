from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING, DEFAULT_MODEL_TRANSCRIPTION
from palimpsest.transcribe import DEFAULT_PROMPT_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_WORKERS


def _infer_output_dir(image_dir: Path) -> Path:
    """Infer transcription output dir as sibling of images/."""
    image_dir = image_dir.resolve()
    if image_dir.name == "images":
        return image_dir.parent / "transcription"
    return image_dir.parent / "transcription"


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
        # Warn if user passed config flags that will be ignored
        manifest = load_manifest(output_dir)
        if args.model != DEFAULT_MODEL_TRANSCRIPTION and args.model != manifest["config"]["model"]:
            print(f"WARNING: manifest already exists with model={manifest['config']['model']}, ignoring --model={args.model}")
            print(f"  To use a different model, delete {manifest_path} and re-submit.")
    else:
        create_manifest(
            image_dir,
            output_dir,
            source=args.source or "",
            book_title=args.book_title or "",
            model=args.model,
            prompt_name=args.prompt_name,
            system_prompt=args.system_prompt,
        )
        print(f"Manifest created: {manifest_path}")

    print(f"Output directory: {output_dir}")
    manifest = submit_batch(output_dir)
    print_status(manifest)


def cmd_batch_status(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, print_status

    output_dir = _resolve_output_dir(args.output_dir)
    manifest = poll_batch(output_dir)
    print_status(manifest)


def cmd_batch_collect(args: argparse.Namespace) -> None:
    from palimpsest.batch import poll_batch, collect_batch, print_status

    output_dir = _resolve_output_dir(args.output_dir)
    manifest = poll_batch(output_dir)
    print_status(manifest)

    if manifest["status"] not in ("completed", "partial"):
        print(f"\nBatch not ready for collection (status: {manifest['status']})")
        return

    final_path = collect_batch(output_dir)
    print(f"\nCollected to: {final_path}")


def cmd_unpack(args: argparse.Namespace) -> None:
    from palimpsest.batch import unpack_transcription

    output_dir = _resolve_output_dir(args.output_dir)
    summary = unpack_transcription(output_dir)
    if summary["flagged_pages"]:
        print(f"\nFlagged pages:")
        for page_id, flags in summary["flags"].items():
            print(f"  {page_id}: {', '.join(flags)}")


def cmd_enrich(args: argparse.Namespace) -> None:
    from palimpsest.enrich import run_enrichment_sync

    input_path = Path(args.input) if args.input else _resolve_output_dir(args.output_dir) / "transcriptions.jsonl"
    output_path = Path(args.output) if args.output else input_path.parent / "enriched.jsonl"

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")

    run_enrichment_sync(
        input_path,
        output_path,
        model=args.model,
        workers=args.workers,
        skip_existing=args.skip_existing,
    )


def cmd_publish(args: argparse.Namespace) -> None:
    from palimpsest.publish import build_book_site

    input_path = Path(args.input) if args.input else _resolve_output_dir(args.output_dir) / "enriched.jsonl"
    if not input_path.exists():
        # Fall back to transcriptions.jsonl if no enriched version
        fallback = input_path.parent / "transcriptions.jsonl"
        if fallback.exists():
            input_path = fallback
            print(f"No enriched.jsonl found, using transcriptions.jsonl")

    out_dir = Path(args.output_dir) if args.output_dir else input_path.parent / "site"
    image_dir = Path(args.image_dir) if args.image_dir else None

    print(f"Input: {input_path}")
    print(f"Output: {out_dir}")

    index_path = build_book_site(
        input_path,
        out_dir=out_dir,
        title=args.title,
        image_dir=image_dir,
    )
    print(f"Published: {index_path}")


def _resolve_output_dir(output_dir: str | None) -> Path:
    """Resolve output dir from arg or cwd."""
    if output_dir:
        return Path(output_dir).resolve()
    cwd = Path.cwd()
    if (cwd / "batch_manifest.json").exists() or (cwd / "transcriptions.jsonl").exists():
        return cwd
    raise ValueError("No --output-dir specified and current directory has no batch_manifest.json or transcriptions.jsonl")


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transcribe", help="Single-call VLM transcription")
    sub = parser.add_subparsers(dest="transcribe_command", required=True)

    # --- Interactive run ---
    run_parser = sub.add_parser("run", help="Transcribe page images to JSONL (interactive, real-time)")
    run_parser.add_argument("--image-dir", required=True, help="Directory containing page images")
    run_parser.add_argument("--output", default=None, help="Output JSONL file path (default: sibling transcription/transcriptions.jsonl)")
    run_parser.add_argument("--source", default="", help="Source identifier (default: parent dir name)")
    run_parser.add_argument("--book-title", default="", help="Book title (default: parent dir name)")
    run_parser.add_argument("--model", default=DEFAULT_MODEL_TRANSCRIPTION, help="VLM model name")
    run_parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME, help="Prompt template name")
    run_parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT, help="System instruction")
    run_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent workers")
    run_parser.add_argument("--skip-existing", action="store_true", help="Skip pages already in output file")
    run_parser.set_defaults(func=cmd_run)

    # --- Batch submit ---
    submit_parser = sub.add_parser("batch-submit", help="Submit a book's images as batch jobs (half price)")
    submit_parser.add_argument("--image-dir", required=True, help="Directory containing page images")
    submit_parser.add_argument("--output-dir", default=None, help="Output directory (default: sibling transcription/)")
    submit_parser.add_argument("--source", default="", help="Source identifier")
    submit_parser.add_argument("--book-title", default="", help="Book title")
    submit_parser.add_argument("--model", default=DEFAULT_MODEL_TRANSCRIPTION, help="VLM model name")
    submit_parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME, help="Prompt template name")
    submit_parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT, help="System instruction")
    submit_parser.set_defaults(func=cmd_batch_submit)

    # --- Batch status ---
    status_parser = sub.add_parser("batch-status", help="Poll and display batch job status")
    status_parser.add_argument("--output-dir", default=None, help="Output directory (default: cwd if manifest exists)")
    status_parser.set_defaults(func=cmd_batch_status)

    # --- Batch collect ---
    collect_parser = sub.add_parser("batch-collect", help="Collect completed batch results into final JSONL")
    collect_parser.add_argument("--output-dir", default=None, help="Output directory (default: cwd if manifest exists)")
    collect_parser.set_defaults(func=cmd_batch_collect)

    # --- Unpack ---
    unpack_parser = sub.add_parser("unpack", help="Unpack JSONL into per-page text files and stitched full text")
    unpack_parser.add_argument("--output-dir", default=None, help="Transcription output directory (default: cwd if JSONL exists)")
    unpack_parser.set_defaults(func=cmd_unpack)

    # --- Enrich ---
    enrich_parser = sub.add_parser("enrich", help="Translate transcriptions via LLM, producing enriched.jsonl")
    enrich_parser.add_argument("--input", default=None, help="Input transcriptions.jsonl (default: output-dir/transcriptions.jsonl)")
    enrich_parser.add_argument("--output", default=None, help="Output enriched.jsonl (default: sibling of input)")
    enrich_parser.add_argument("--output-dir", default=None, help="Transcription directory (for inferring input/output paths)")
    enrich_parser.add_argument("--model", default=DEFAULT_MODEL_READING, help="LLM model for translation")
    enrich_parser.add_argument("--workers", type=int, default=8, help="Concurrent workers")
    enrich_parser.add_argument("--skip-existing", action="store_true", help="Skip pages already in output")
    enrich_parser.set_defaults(func=cmd_enrich)

    # --- Publish ---
    publish_parser = sub.add_parser("publish", help="Generate a static HTML book site from JSONL")
    publish_parser.add_argument("--input", default=None, help="Input JSONL (default: output-dir/enriched.jsonl or transcriptions.jsonl)")
    publish_parser.add_argument("--output-dir", default=None, help="Output directory for HTML site")
    publish_parser.add_argument("--image-dir", default=None, help="Directory containing page images")
    publish_parser.add_argument("--title", default=None, help="Book title override")
    publish_parser.set_defaults(func=cmd_publish)
