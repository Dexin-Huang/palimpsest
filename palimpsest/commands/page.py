from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.packets.continuity import run_page_handoff, run_window_synthesis
from palimpsest.packets.doc_pages import load_doc_pages, packet_dir_for_page, select_doc_pages
from palimpsest.packets.state import load_packet_json
from palimpsest.packets.translate import run_packet_translation
from palimpsest.reader.packet import render_packet_workspace


def cmd_translate_packet(args: argparse.Namespace) -> None:
    artifact = run_packet_translation(
        Path(args.packet),
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
    )
    print(f"packet: {artifact.packet_path}")
    print(f"translation: {artifact.output_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")
    if artifact.finish_reason:
        print(f"finish_reason: {artifact.finish_reason}")
    print(f"chars: {artifact.char_count}")


def cmd_translate_doc_packets(args: argparse.Namespace) -> None:
    doc_dir = Path(args.doc_dir).resolve()
    pages = select_doc_pages(
        load_doc_pages(doc_dir),
        start_page=args.start_page,
        end_page=args.end_page,
        limit=args.limit,
    )
    total = len(pages)
    if total == 0:
        print("translate_doc_packets: no pages selected")
        return

    print(f"doc_dir: {doc_dir}")
    print(f"selected_pages: {total}")
    print(f"model: {args.model}")

    completed = 0
    skipped = 0
    failed = 0

    for index, page in enumerate(pages, start=1):
        page_id = page["page_id"]
        packet_dir = packet_dir_for_page(doc_dir, page_id)
        packet_path = packet_dir / "packet.json"
        if not packet_path.exists():
            failed += 1
            print(f"[{index}/{total}] {page_id}: missing packet", flush=True)
            if args.fail_fast:
                raise FileNotFoundError(f"missing packet: {packet_path}")
            continue

        if args.skip_existing:
            packet = load_packet_json(packet_path)
            translation_status = packet.files["translation"].status
            if translation_status in {"draft", "reviewed", "complete"}:
                skipped += 1
                print(f"[{index}/{total}] {page_id}: skip", flush=True)
                continue

        print(f"[{index}/{total}] {page_id}: translate", flush=True)
        try:
            artifact = run_packet_translation(
                packet_path,
                prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
                model=args.model,
            )
            completed += 1
            print(f"  translation: {artifact.output_path}", flush=True)
            print(f"  chars: {artifact.char_count}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{total}] {page_id}: failed ({exc.__class__.__name__}: {exc})", flush=True)
            if args.fail_fast:
                raise

    print("translate_doc_packets: done")
    print(f"completed: {completed}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")


def cmd_render_html(args: argparse.Namespace) -> None:
    artifact = render_packet_workspace(
        Path(args.packet),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        book_title=args.title,
    )
    print(f"html: {artifact.html_path}")
    print(f"render: {artifact.folio_render_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_handoff(args: argparse.Namespace) -> None:
    artifact = run_page_handoff(
        Path(args.packet),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
        next_page_id=args.next_page_id,
        previous_handoff_path=Path(args.previous_handoff).resolve() if args.previous_handoff else None,
    )
    print(f"handoff_json: {artifact.json_path}")
    print(f"handoff_md: {artifact.markdown_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def cmd_window(args: argparse.Namespace) -> None:
    artifact = run_window_synthesis(
        [Path(item) for item in args.packet],
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
        center_page_id=args.center_page_id,
    )
    print(f"window_json: {artifact.json_path}")
    print(f"window_md: {artifact.markdown_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("page", help="Page-level packet operations: translate, render, and continuity")
    sub = parser.add_subparsers(dest="page_cmd", required=True)

    translate_packet = sub.add_parser("translate-packet", help="Translate one packet witness into translation.md using the canonical reading model")
    translate_packet.add_argument("--packet", required=True, help="Path to packet.json")
    translate_packet.add_argument("--prompt-file", help="Optional explicit translation prompt file")
    translate_packet.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Translation model (default: {DEFAULT_MODEL_READING})",
    )
    translate_packet.set_defaults(func=cmd_translate_packet)

    translate_doc = sub.add_parser("translate-doc-packets", help="Translate packet witnesses across a document tranche")
    translate_doc.add_argument("--doc-dir", required=True, help="Document directory containing page_list.json and experiments/")
    translate_doc.add_argument("--start-page", help="Optional starting page_id")
    translate_doc.add_argument("--end-page", help="Optional ending page_id")
    translate_doc.add_argument("--limit", type=int, help="Optional maximum number of pages")
    translate_doc.add_argument("--prompt-file", help="Optional explicit translation prompt file")
    translate_doc.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Translation model (default: {DEFAULT_MODEL_READING})",
    )
    translate_doc.add_argument("--skip-existing", action="store_true", help="Skip packets whose translation status is already draft/reviewed/complete")
    translate_doc.add_argument("--fail-fast", action="store_true", help="Stop on the first failed packet translation")
    translate_doc.set_defaults(func=cmd_translate_doc_packets)

    render_html = sub.add_parser("render-html", help="Render an HTML folio edition from one packet")
    render_html.add_argument("--packet", required=True, help="Path to packet.json")
    render_html.add_argument("--out-dir", help="Optional output directory for HTML folio artifacts")
    render_html.add_argument("--title", help="Optional book/manuscript title override")
    render_html.set_defaults(func=cmd_render_html)

    handoff = sub.add_parser("handoff", help="Generate a compact forward handoff from one completed page packet")
    handoff.add_argument("--packet", required=True, help="Path to packet.json")
    handoff.add_argument("--next-page-id", help="Optional next page id to carry in the handoff")
    handoff.add_argument("--previous-handoff", help="Optional previous handoff markdown or JSON to compress with this page")
    handoff.add_argument("--out-dir", help="Output directory for handoff artifacts")
    handoff.add_argument("--prompt-file", help="Explicit handoff prompt file path")
    handoff.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Multimodal model for continuity compression (default: {DEFAULT_MODEL_READING})",
    )
    handoff.set_defaults(func=cmd_handoff)

    window = sub.add_parser("window", help="Generate a compact sliding-window synthesis from adjacent page packets")
    window.add_argument(
        "--packet",
        action="append",
        required=True,
        help="Input packet.json path in reading order; repeat for each packet in the window",
    )
    window.add_argument("--center-page-id", help="Optional explicit center page id for the window")
    window.add_argument("--out-dir", help="Output directory for window synthesis artifacts")
    window.add_argument("--prompt-file", help="Explicit window prompt file path")
    window.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Multimodal model for window synthesis (default: {DEFAULT_MODEL_READING})",
    )
    window.set_defaults(func=cmd_window)
