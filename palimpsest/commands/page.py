from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.config import DEFAULT_MODEL_TRIAGE
from palimpsest.config import DEFAULT_MODEL_VISION
from palimpsest.packets.continuity import run_page_handoff, run_window_synthesis
from palimpsest.packets.decode import run_layout_pipeline, run_packet_decode
from palimpsest.packets.doc_pages import load_doc_pages, packet_dir_for_page, select_doc_pages
from palimpsest.packets.packet import (
    attach_layout_probe,
    create_page_packet,
    ingest_page_reading,
    sync_packet_from_assembly,
)
from palimpsest.packets.state import load_packet_json, reconcile_packet_json
from palimpsest.packets.translate import run_packet_translation
from palimpsest.reader.packet import render_packet_workspace
from palimpsest.reconstruct.assembly import run_page_assembly
from palimpsest.reconstruct.prepare import prepare_image
from palimpsest.reconstruct.probe import run_page_layout_probe
from palimpsest.reconstruct.reading import run_page_reading, run_section_synthesis
from palimpsest.reconstruct.region_reads import run_region_reads
from palimpsest.reconstruct.resolve import run_box_cleanup, run_section_resolution
from palimpsest.reconstruct.validate import run_page_validate


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


def cmd_packet(args: argparse.Namespace) -> None:
    skip_cleanup = args.skip_box_cleanup or getattr(args, "skip_cleanup", False)
    packet, packet_path = create_page_packet(
        Path(args.image),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prepare=not args.raw,
        previous_packet_path=Path(args.previous_packet).resolve() if args.previous_packet else None,
        previous_handoff_path=Path(args.previous_handoff).resolve() if args.previous_handoff else None,
        window_synthesis_path=Path(args.window).resolve() if args.window else None,
    )
    print(f"packet: {packet_path}")
    print(f"page_unit: {packet.page_unit}")
    if packet.prepared_image_path:
        print(f"prepared_image: {packet.prepared_image_path}")
    if packet.continuity.previous_handoff_path:
        print(f"previous_handoff: {packet.continuity.previous_handoff_path}")
    if packet.continuity.window_synthesis_path:
        print(f"window_synthesis: {packet.continuity.window_synthesis_path}")
    if not args.no_layout_probe:
        layout = run_layout_pipeline(
            image_path=Path(args.image),
            probe_dir=packet_path.parent / "layout_probe",
            layout_model=args.layout_model,
            region_model=args.orient_model,
            cleanup_model=args.cleanup_model,
            run_regions=not args.no_orient,
            run_section_resolution_stage=not skip_cleanup,
            run_box_cleanup_stage=not skip_cleanup,
            run_assembly=True,
            retries=args.retries,
        )
        packet = attach_layout_probe(packet_path, layout.probe_artifact.output_dir)
        print(f"layout_probe: {layout.probe_artifact.layout_json_path}")
        print(f"layout_overlay: {layout.probe_artifact.overlay_path}")
        if layout.region_artifact is not None:
            print(f"region_reads: {layout.region_artifact.reads_path}")
        else:
            print(f"region_reads: {layout.probe_artifact.region_reads_path}")
        if layout.section_artifact is not None:
            print(f"section_resolution: {layout.section_artifact.resolution_json_path}")
        if layout.validation_artifact is not None:
            print(f"page_validation: {layout.validation_artifact.validation_json_path}")
        if layout.box_cleanup_artifact is not None:
            print(f"box_cleanup: {layout.box_cleanup_artifact.cleanup_json_path}")
        if layout.assembly_artifact is not None:
            print(f"page_assembly: {layout.assembly_artifact.assembly_json_path}")
    print(f"next_action: {packet.workflow.next_action}")


def cmd_ingest_reading(args: argparse.Namespace) -> None:
    packet = ingest_page_reading(
        Path(args.packet),
        Path(args.reading),
    )
    print(f"packet: {args.packet}")
    print(f"witness: {packet.files['witness'].path}")
    print(f"translation: {packet.files['translation'].path}")
    print(f"notes: {packet.files['notes'].path}")
    print(f"terms: {packet.files['terms'].path}")
    print(f"questions: {packet.files['questions'].path}")
    print(f"next_action: {packet.workflow.next_action}")


def cmd_sync_packet(args: argparse.Namespace) -> None:
    packet = sync_packet_from_assembly(
        Path(args.packet),
        Path(args.assembly).resolve() if args.assembly else None,
    )
    print(f"packet: {args.packet}")
    print(f"witness: {packet.files['witness'].path}")
    print(f"translation: {packet.files['translation'].path}")
    print(f"notes: {packet.files['notes'].path}")
    print(f"next_action: {packet.workflow.next_action}")


def cmd_sync_doc_packets(args: argparse.Namespace) -> None:
    doc_dir = Path(args.doc_dir).resolve()
    pages = select_doc_pages(
        load_doc_pages(doc_dir),
        start_page=args.start_page,
        end_page=args.end_page,
        limit=args.limit,
    )
    total = len(pages)
    if total == 0:
        print("sync_doc_packets: no pages selected")
        return

    print(f"doc_dir: {doc_dir}")
    print(f"selected_pages: {total}")

    synced = 0
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
            if packet.files["witness"].status in {"draft", "reviewed", "complete"}:
                skipped += 1
                print(f"[{index}/{total}] {page_id}: skip", flush=True)
                continue

        print(f"[{index}/{total}] {page_id}: sync", flush=True)
        try:
            packet = sync_packet_from_assembly(packet_path)
            synced += 1
            print(f"  witness: {packet.files['witness'].path}", flush=True)
            print(f"  next_action: {packet.workflow.next_action}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{total}] {page_id}: failed ({exc.__class__.__name__}: {exc})", flush=True)
            if args.fail_fast:
                raise

    print("sync_doc_packets: done")
    print(f"synced: {synced}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")


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


def cmd_render_html(args: argparse.Namespace) -> None:
    artifact = render_packet_workspace(
        Path(args.packet),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        book_title=args.title,
    )
    print(f"html: {artifact.html_path}")
    print(f"render: {artifact.folio_render_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_refresh_packet(args: argparse.Namespace) -> None:
    skip_cleanup = args.skip_box_cleanup or getattr(args, "skip_cleanup", False)
    packet_path = Path(args.packet).resolve()
    packet = reconcile_packet_json(packet_path)
    probe_dir = packet_path.parent / "layout_probe"

    if not args.skip_layout_probe:
        layout = run_layout_pipeline(
            image_path=Path(packet.source_image_path),
            probe_dir=probe_dir,
            layout_model=args.layout_model,
            region_model=args.orient_model,
            cleanup_model=args.cleanup_model,
            run_regions=not args.no_orient,
            run_section_resolution_stage=not skip_cleanup,
            run_box_cleanup_stage=not skip_cleanup,
            run_assembly=not args.skip_assembly,
            retries=args.retries,
        )
        print(f"layout_probe: {layout.probe_artifact.layout_json_path}")
        print(f"layout_overlay: {layout.probe_artifact.overlay_path}")
        if layout.region_artifact is not None:
            print(f"region_reads: {layout.region_artifact.reads_path}")
        if layout.section_artifact is not None:
            print(f"section_resolution: {layout.section_artifact.resolution_json_path}")
        if layout.validation_artifact is not None:
            print(f"page_validation: {layout.validation_artifact.validation_json_path}")
        if layout.box_cleanup_artifact is not None:
            print(f"box_cleanup: {layout.box_cleanup_artifact.cleanup_json_path}")
        if layout.assembly_artifact is not None:
            print(f"page_assembly: {layout.assembly_artifact.assembly_json_path}")
    else:
        if not skip_cleanup:
            section_artifact = run_section_resolution(
                probe_dir,
                model=args.cleanup_model,
            )
            print(f"section_resolution: {section_artifact.resolution_json_path}")
            validation_artifact = run_page_validate(probe_dir)
            print(f"page_validation: {validation_artifact.validation_json_path}")
            box_cleanup_artifact = run_box_cleanup(
                probe_dir,
                model=args.cleanup_model,
            )
            print(f"box_cleanup: {box_cleanup_artifact.cleanup_json_path}")

        if not args.skip_assembly:
            assembly_artifact = run_page_assembly(probe_dir)
            print(f"page_assembly: {assembly_artifact.assembly_json_path}")

    packet = attach_layout_probe(packet_path, probe_dir)
    print(f"packet: {packet_path}")

    if args.render_html:
        artifact = render_packet_workspace(
            packet_path,
            out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
            book_title=args.title,
        )
        print(f"html: {artifact.html_path}")
        print(f"render: {artifact.folio_render_path}")
        print(f"meta: {artifact.meta_path}")

    print(f"next_action: {packet.workflow.next_action}")


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


def cmd_layout_probe(args: argparse.Namespace) -> None:
    artifact = run_page_layout_probe(
        Path(args.image),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
        model=args.model,
        orient_model=args.orient_model,
        orient_regions=not args.no_orient,
    )
    print(f"layout_json: {artifact.layout_json_path}")
    print(f"overlay: {artifact.overlay_path}")
    print(f"crops_dir: {artifact.crops_dir}")
    print(f"region_reads: {artifact.region_reads_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")
    print(f"region_read_model: {artifact.region_read_model}")
    if artifact.finish_reason:
        print(f"finish_reason: {artifact.finish_reason}")


def cmd_region_read(args: argparse.Namespace) -> None:
    artifact = run_region_reads(
        Path(args.probe_dir),
        model=args.model,
        region_ids=args.region_id or None,
    )
    print(f"reads: {artifact.reads_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def cmd_section_resolution(args: argparse.Namespace) -> None:
    artifact = run_section_resolution(
        Path(args.probe_dir),
        model=args.model,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
    )
    print(f"section_resolution: {artifact.resolution_json_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def cmd_assemble(args: argparse.Namespace) -> None:
    artifact = run_page_assembly(Path(args.probe_dir))
    print(f"assembly_json: {artifact.assembly_json_path}")
    print(f"assembly_md: {artifact.assembly_md_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_box_cleanup(args: argparse.Namespace) -> None:
    artifact = run_box_cleanup(
        Path(args.probe_dir),
        model=args.model,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
    )
    print(f"box_cleanup: {artifact.cleanup_json_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def cmd_validate(args: argparse.Namespace) -> None:
    artifact = run_page_validate(Path(args.probe_dir))
    print(f"validation_json: {artifact.validation_json_path}")
    print(f"validation_md: {artifact.validation_md_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_decode_doc(args: argparse.Namespace) -> None:
    doc_dir = Path(args.doc_dir).resolve()
    pages = select_doc_pages(
        load_doc_pages(doc_dir),
        start_page=args.start_page,
        end_page=args.end_page,
        limit=args.limit,
    )
    total = len(pages)
    if total == 0:
        print("decode_doc: no pages selected")
        return

    print(f"doc_dir: {doc_dir}")
    print(f"selected_pages: {total}")
    if args.skip_existing:
        print("skip_existing: true")

    completed = 0
    skipped = 0
    failed = 0

    for index, page in enumerate(pages, start=1):
        page_id = page["page_id"]
        image_path = doc_dir / "images" / page["filename"]
        packet_dir = packet_dir_for_page(doc_dir, page_id)
        packet_path = packet_dir / "packet.json"
        assembly_path = packet_dir / "layout_probe" / "page_assembly.json"
        render_path = packet_dir / "index.html"
        if args.skip_existing and packet_path.exists() and assembly_path.exists() and (not args.render_html or render_path.exists()):
            skipped += 1
            print(f"[{index}/{total}] {page_id}: skip", flush=True)
            continue
        print(f"[{index}/{total}] {page_id}: decode", flush=True)
        try:
            result = run_packet_decode(
                image_path=image_path,
                packet_dir=packet_dir,
                raw=args.raw,
                layout_model=args.layout_model,
                region_model=args.orient_model,
                cleanup_model=args.cleanup_model,
                retries=args.retries,
            )
            completed += 1
            status = "created" if result.created else "refreshed"
            print(f"[{index}/{total}] {page_id}: {status}", flush=True)
            print(f"  packet: {result.packet_path}", flush=True)
            print(f"  assembly: {result.layout.assembly_artifact.assembly_json_path}", flush=True)
            if args.render_html:
                render_artifact = render_packet_workspace(
                    result.packet_path,
                    out_dir=packet_dir,
                    book_title=args.title,
                )
                print(f"  html: {render_artifact.html_path}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{total}] {page_id}: failed ({exc.__class__.__name__}: {exc})", flush=True)
            if args.fail_fast:
                raise

    print("decode_doc: done")
    print(f"completed: {completed}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("page", help="Prepare, packetize, read, and synthesize page-level witness artifacts")
    sub = parser.add_subparsers(dest="page_cmd", required=True)

    prepare = sub.add_parser("prepare", help="Deterministically crop a page down to its manuscript content area")
    prepare.add_argument("--image", required=True, help="Source image to prepare")
    prepare.add_argument("--out-dir", help="Output directory for prepared image artifact")
    prepare.set_defaults(func=cmd_prepare)

    packet = sub.add_parser("packet", help="Create a page packet and run the canonical reconstruction ladder")
    packet.add_argument("--image", required=True, help="Source image to packetize")
    packet.add_argument("--out-dir", help="Output directory for page packet")
    packet.add_argument("--raw", action="store_true", help="Skip automatic preparation and packetize the raw image")
    packet.add_argument("--previous-packet", help="Optional previous packet.json for continuity")
    packet.add_argument("--previous-handoff", help="Optional previous page_handoff.json or .md for continuity")
    packet.add_argument("--window", help="Optional local window_synthesis.json or .md for continuity")
    packet.add_argument("--no-layout-probe", action="store_true", help="Skip the default coarse layout probe")
    packet.add_argument(
        "--layout-model",
        default=DEFAULT_MODEL_VISION,
        help=f"Vision model for the layout probe (default: {DEFAULT_MODEL_VISION})",
    )
    packet.add_argument(
        "--orient-model",
        default=DEFAULT_MODEL_READING,
        help=f"Model for full transcription reads per region (default: {DEFAULT_MODEL_READING})",
    )
    packet.add_argument("--no-orient", "--no-region-read", dest="no_orient", action="store_true", help="Skip the region-read stage during packet creation")
    packet.add_argument("--skip-box-cleanup", action="store_true", help="Skip canonical cleanup stages (section-resolution, validate, and box-cleanup)")
    packet.add_argument("--skip-cleanup", action="store_true", help="Alias for --skip-box-cleanup")
    packet.add_argument(
        "--cleanup-model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for canonical cleanup and repair (default: {DEFAULT_MODEL_TRIAGE})",
    )
    packet.add_argument("--retries", type=int, default=2, help="Retry count for model-backed packet build stages")
    packet.set_defaults(func=cmd_packet)

    ingest = sub.add_parser("ingest-reading", help="Deterministically ingest a page read markdown into a page packet")
    ingest.add_argument("--packet", required=True, help="Path to packet.json")
    ingest.add_argument("--reading", required=True, help="Path to page reading markdown")
    ingest.set_defaults(func=cmd_ingest_reading)

    sync_packet = sub.add_parser("sync-packet", help="Deterministically sync page_assembly.json into witness/translation packet files")
    sync_packet.add_argument("--packet", required=True, help="Path to packet.json")
    sync_packet.add_argument("--assembly", help="Optional explicit page_assembly.json path")
    sync_packet.set_defaults(func=cmd_sync_packet)

    sync_doc = sub.add_parser("sync-doc-packets", help="Sync page assemblies into packet witness files across a document tranche")
    sync_doc.add_argument("--doc-dir", required=True, help="Document directory containing page_list.json and experiments/")
    sync_doc.add_argument("--start-page", help="Optional starting page_id")
    sync_doc.add_argument("--end-page", help="Optional ending page_id")
    sync_doc.add_argument("--limit", type=int, help="Optional maximum number of pages")
    sync_doc.add_argument("--skip-existing", action="store_true", help="Skip packets whose witness status is already draft/reviewed/complete")
    sync_doc.add_argument("--fail-fast", action="store_true", help="Stop on the first failed packet sync")
    sync_doc.set_defaults(func=cmd_sync_doc_packets)

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

    render_html = sub.add_parser("render-html", help="Render an HTML folio edition from one packet")
    render_html.add_argument("--packet", required=True, help="Path to packet.json")
    render_html.add_argument("--out-dir", help="Optional output directory for HTML folio artifacts")
    render_html.add_argument("--title", help="Optional book/manuscript title override")
    render_html.set_defaults(func=cmd_render_html)

    refresh_packet = sub.add_parser("refresh-packet", help="Rerun the canonical reconstruction ladder for an existing packet and optionally rerender HTML")
    refresh_packet.add_argument("--packet", required=True, help="Path to packet.json")
    refresh_packet.add_argument("--out-dir", help="Optional output directory for HTML folio artifacts")
    refresh_packet.add_argument("--title", help="Optional book/manuscript title override")
    refresh_packet.add_argument("--skip-layout-probe", action="store_true", help="Reuse existing layout probe artifacts")
    refresh_packet.add_argument("--skip-box-cleanup", action="store_true", help="Reuse existing canonical cleanup artifacts")
    refresh_packet.add_argument("--skip-cleanup", action="store_true", help="Alias for --skip-box-cleanup")
    refresh_packet.add_argument("--skip-assembly", action="store_true", help="Reuse existing page assembly artifact")
    refresh_packet.add_argument("--render-html", action="store_true", help="Render HTML after refreshing the packet")
    refresh_packet.add_argument(
        "--layout-model",
        default=DEFAULT_MODEL_VISION,
        help=f"Vision model for the layout probe (default: {DEFAULT_MODEL_VISION})",
    )
    refresh_packet.add_argument(
        "--orient-model",
        default=DEFAULT_MODEL_READING,
        help=f"Model for full transcription reads per region (default: {DEFAULT_MODEL_READING})",
    )
    refresh_packet.add_argument("--no-orient", "--no-region-read", dest="no_orient", action="store_true", help="Skip the region-read stage while refreshing the packet")
    refresh_packet.add_argument(
        "--cleanup-model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for canonical cleanup and repair (default: {DEFAULT_MODEL_TRIAGE})",
    )
    refresh_packet.add_argument("--retries", type=int, default=2, help="Retry count for model-backed refresh stages")
    refresh_packet.set_defaults(func=cmd_refresh_packet)

    layout_probe = sub.add_parser("layout-probe", help="Advanced: run only the coarse semantic box pass and generate overlay/crops")
    layout_probe.add_argument("--image", required=True, help="Source image to probe")
    layout_probe.add_argument("--out-dir", help="Output directory for layout probe artifacts")
    layout_probe.add_argument("--prompt-file", help="Optional prompt file override")
    layout_probe.add_argument(
        "--model",
        default=DEFAULT_MODEL_VISION,
        help=f"Vision model for the layout pass (default: {DEFAULT_MODEL_VISION})",
    )
    layout_probe.add_argument(
        "--orient-model",
        default=DEFAULT_MODEL_READING,
        help=f"Model for full transcription reads per region (default: {DEFAULT_MODEL_READING})",
    )
    layout_probe.add_argument("--no-orient", "--no-region-read", dest="no_orient", action="store_true", help="Skip the follow-on region-read stage")
    layout_probe.set_defaults(func=cmd_layout_probe)

    region_read = sub.add_parser("region-read", help="Advanced: run or rerun full transcription reads for the current coarse regions")
    region_read.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    region_read.add_argument(
        "--region-id",
        action="append",
        help="Optional region id to rerun; repeat to target multiple regions",
    )
    region_read.add_argument(
        "--model",
        default=DEFAULT_MODEL_READING,
        help=f"Model for crop-level region reads (default: {DEFAULT_MODEL_READING})",
    )
    region_read.set_defaults(func=cmd_region_read)

    section_resolution = sub.add_parser("section-resolution", help="Advanced: assign one canonical text block to each region before validation")
    section_resolution.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    section_resolution.add_argument("--prompt-file", help="Optional prompt file override")
    section_resolution.add_argument(
        "--model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for canonical box-to-text assignment (default: {DEFAULT_MODEL_TRIAGE})",
    )
    section_resolution.set_defaults(func=cmd_section_resolution)

    box_cleanup = sub.add_parser("box-cleanup", help="Advanced: repair only the flagged neighboring box pairs for a page")
    box_cleanup.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    box_cleanup.add_argument("--prompt-file", help="Optional prompt file override")
    box_cleanup.add_argument(
        "--model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for targeted box cleanup (default: {DEFAULT_MODEL_TRIAGE})",
    )
    box_cleanup.set_defaults(func=cmd_box_cleanup)

    validate = sub.add_parser("validate", help="Advanced: run structural QA and flag pages that need targeted repair")
    validate.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    validate.set_defaults(func=cmd_validate)

    assemble = sub.add_parser("assemble", help="Advanced: assemble the cleaned region texts into one canonical page object")
    assemble.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    assemble.set_defaults(func=cmd_assemble)

    decode_doc = sub.add_parser("decode-doc", help="Run the canonical reconstruction ladder across a manuscript page list")
    decode_doc.add_argument("--doc-dir", required=True, help="Path to a library/<doc_id> directory containing images/ and page_list.json")
    decode_doc.add_argument("--start-page", help="Optional page_id to start from")
    decode_doc.add_argument("--end-page", help="Optional page_id to stop at")
    decode_doc.add_argument("--limit", type=int, help="Optional max page count to decode")
    decode_doc.add_argument("--raw", action="store_true", help="Skip automatic preparation and decode the raw images")
    decode_doc.add_argument("--render-html", action="store_true", help="Render each packet folio after decoding")
    decode_doc.add_argument("--title", help="Optional book/manuscript title override for HTML render")
    decode_doc.add_argument("--skip-existing", action="store_true", help="Skip pages that already have packet + assembly (+ HTML when requested)")
    decode_doc.add_argument("--fail-fast", action="store_true", help="Stop on the first page failure")
    decode_doc.add_argument(
        "--layout-model",
        default=DEFAULT_MODEL_VISION,
        help=f"Vision model for the layout probe (default: {DEFAULT_MODEL_VISION})",
    )
    decode_doc.add_argument(
        "--orient-model",
        default=DEFAULT_MODEL_READING,
        help=f"Model for full transcription reads per region (default: {DEFAULT_MODEL_READING})",
    )
    decode_doc.add_argument(
        "--cleanup-model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for canonical cleanup and repair (default: {DEFAULT_MODEL_TRIAGE})",
    )
    decode_doc.add_argument("--retries", type=int, default=2, help="Retry count for model-backed decode stages")
    decode_doc.set_defaults(func=cmd_decode_doc)

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
