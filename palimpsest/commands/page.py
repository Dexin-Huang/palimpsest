from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.page_prepare import prepare_image
from palimpsest.page_reading import run_page_reading, run_section_synthesis


def cmd_prepare(args: argparse.Namespace) -> None:
    artifact = prepare_image(
        Path(args.image),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
    )
    print(f"prepared: {artifact.prepared_image_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"bbox_px: {artifact.bbox_px}")


def cmd_read(args: argparse.Namespace) -> None:
    artifact = run_page_reading(
        Path(args.image),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
        prepare=not args.raw,
    )
    print(f"reading: {artifact.output_path}")
    print(f"meta: {artifact.meta_path}")
    if artifact.prepare_meta_path:
        print(f"prepare_meta: {artifact.prepare_meta_path}")
        print(f"prepared_image: {artifact.prepared_image_path}")
    print(f"model: {artifact.model}")
    if artifact.finish_reason:
        print(f"finish_reason: {artifact.finish_reason}")
    print(f"chars: {artifact.char_count}")


def cmd_synthesize(args: argparse.Namespace) -> None:
    artifact = run_section_synthesis(
        [Path(item) for item in args.input],
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
    )
    print(f"synthesis: {artifact.output_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")
    if artifact.finish_reason:
        print(f"finish_reason: {artifact.finish_reason}")
    print(f"inputs: {len(artifact.input_paths)}")
    print(f"chars: {artifact.char_count}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("page", help="Prepare, read, and synthesize page-level witness artifacts")
    sub = parser.add_subparsers(dest="page_cmd", required=True)

    prepare = sub.add_parser("prepare", help="Deterministically crop a page down to its manuscript content area")
    prepare.add_argument("--image", required=True, help="Source image to prepare")
    prepare.add_argument("--out-dir", help="Output directory for prepared image artifact")
    prepare.set_defaults(func=cmd_prepare)

    read = sub.add_parser("read", help="Run the focused witness prompt on one page image")
    read.add_argument("--image", required=True, help="Source image to read")
    read.add_argument("--out-dir", help="Output directory for reading artifact")
    read.add_argument("--prompt-file", help="Explicit prompt file path")
    read.add_argument("--raw", action="store_true", help="Skip automatic content preparation and read the raw image")
    read.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Multimodal reading model (default: {DEFAULT_MODEL_READING})",
    )
    read.set_defaults(func=cmd_read)

    synthesize = sub.add_parser("synthesize", help="Build a section-level synthesis from multiple page witness memos")
    synthesize.add_argument(
        "--input",
        dest="input",
        action="append",
        required=True,
        help="Input page witness memo markdown file; repeat for multiple pages",
    )
    synthesize.add_argument("--out-dir", help="Output directory for synthesis artifact")
    synthesize.add_argument("--prompt-file", help="Explicit synthesis prompt file path")
    synthesize.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Multimodal synthesis model (default: {DEFAULT_MODEL_READING})",
    )
    synthesize.set_defaults(func=cmd_synthesize)
