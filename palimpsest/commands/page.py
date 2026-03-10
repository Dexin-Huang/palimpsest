from __future__ import annotations

import argparse
from pathlib import Path
import time

from palimpsest.config import DEFAULT_MODEL_READING
from palimpsest.config import DEFAULT_MODEL_TRIAGE
from palimpsest.config import DEFAULT_MODEL_VISION
from palimpsest.page_continuity import run_page_handoff, run_window_synthesis
from palimpsest.page_layout import (
    run_overlap_resolution,
    run_page_assembly,
    run_page_layout_probe,
    run_region_reads,
)
from palimpsest.page_packet import attach_layout_probe, create_page_packet, ingest_page_reading
from palimpsest.page_prepare import prepare_image
from palimpsest.page_reading import run_page_reading, run_section_synthesis
from palimpsest.packet_scholar import repair_packet_json
from palimpsest.packet_render import render_packet_edition
from palimpsest.packet_web import render_packet_folio_html


def _run_layout_pipeline(
    *,
    image_path: Path,
    probe_dir: Path,
    layout_model: str,
    region_model: str,
    overlap_model: str,
    run_regions: bool,
    run_overlap: bool,
    run_assembly: bool,
    retries: int = 2,
):
    probe_artifact = None
    region_artifact = None
    overlap_artifact = None
    assembly_artifact = None

    def _retry(stage_name: str, fn):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                if attempt == 0:
                    print(f"{stage_name}: running", flush=True)
                return fn()
            except Exception as exc:  # pragma: no cover - defensive runtime retry path
                last_exc = exc
                if attempt >= retries:
                    raise
                print(f"{stage_name}: retry {attempt + 1}/{retries} after {exc.__class__.__name__}", flush=True)
                time.sleep(min(2 + attempt, 5))
        raise last_exc  # pragma: no cover

    probe_artifact = _retry(
        "layout_probe",
        lambda: run_page_layout_probe(
            image_path,
            out_dir=probe_dir,
            model=layout_model,
            orient_model=region_model,
            orient_regions=False,
        ),
    )

    if run_regions:
        region_artifact = _retry(
            "region_read",
            lambda: run_region_reads(
                probe_artifact.output_dir,
                model=region_model,
            ),
        )

    if run_overlap:
        overlap_artifact = _retry(
            "resolve_overlap",
            lambda: run_overlap_resolution(
                probe_artifact.output_dir,
                model=overlap_model,
            ),
        )

    if run_assembly:
        assembly_artifact = run_page_assembly(probe_artifact.output_dir)

    return probe_artifact, region_artifact, overlap_artifact, assembly_artifact


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
        probe_artifact, region_artifact, overlap_artifact, assembly_artifact = _run_layout_pipeline(
            image_path=Path(args.image),
            probe_dir=packet_path.parent / "layout_probe",
            layout_model=args.layout_model,
            region_model=args.orient_model,
            overlap_model=args.overlap_model,
            run_regions=not args.no_orient,
            run_overlap=not args.skip_overlap_resolution,
            run_assembly=True,
            retries=args.retries,
        )
        packet = attach_layout_probe(packet_path, probe_artifact.output_dir)
        print(f"layout_probe: {probe_artifact.layout_json_path}")
        print(f"layout_overlay: {probe_artifact.overlay_path}")
        if region_artifact is not None:
            print(f"region_orientations: {region_artifact.reads_path}")
        else:
            print(f"region_orientations: {probe_artifact.orientations_path}")
        if overlap_artifact is not None:
            print(f"overlap_resolution: {overlap_artifact.resolution_json_path}")
        if assembly_artifact is not None:
            print(f"page_assembly: {assembly_artifact.assembly_json_path}")
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


def cmd_render(args: argparse.Namespace) -> None:
    artifact = render_packet_edition(
        Path(args.packet),
        engine=args.engine,
        keep_logs=args.keep_logs,
    )
    print(f"pdf: {artifact.pdf_path}")
    print(f"tex: {artifact.tex_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"engine: {artifact.engine_path}")


def cmd_render_html(args: argparse.Namespace) -> None:
    artifact = render_packet_folio_html(
        Path(args.packet),
        out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
        book_title=args.title,
    )
    print(f"html: {artifact.html_path}")
    print(f"folio_render: {artifact.folio_render_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_refresh_packet(args: argparse.Namespace) -> None:
    packet_path = Path(args.packet).resolve()
    packet = repair_packet_json(packet_path)
    probe_dir = packet_path.parent / "layout_probe"

    if not args.skip_layout_probe:
        probe_artifact, region_artifact, overlap_artifact, assembly_artifact = _run_layout_pipeline(
            image_path=Path(packet.source_image_path),
            probe_dir=probe_dir,
            layout_model=args.layout_model,
            region_model=args.orient_model,
            overlap_model=args.overlap_model,
            run_regions=not args.no_orient,
            run_overlap=not args.skip_overlap_resolution,
            run_assembly=not args.skip_assembly,
            retries=args.retries,
        )
        print(f"layout_probe: {probe_artifact.layout_json_path}")
        print(f"layout_overlay: {probe_artifact.overlay_path}")
        if region_artifact is not None:
            print(f"region_orientations: {region_artifact.reads_path}")
        if overlap_artifact is not None:
            print(f"overlap_resolution: {overlap_artifact.resolution_json_path}")
        if assembly_artifact is not None:
            print(f"page_assembly: {assembly_artifact.assembly_json_path}")
    else:
        if not args.skip_overlap_resolution:
            overlap_artifact = run_overlap_resolution(
                probe_dir,
                model=args.overlap_model,
            )
            print(f"overlap_resolution: {overlap_artifact.resolution_json_path}")

        if not args.skip_assembly:
            assembly_artifact = run_page_assembly(probe_dir)
            print(f"page_assembly: {assembly_artifact.assembly_json_path}")

    packet = attach_layout_probe(packet_path, probe_dir)
    print(f"packet: {packet_path}")

    if args.render_html:
        artifact = render_packet_folio_html(
            packet_path,
            out_dir=Path(args.out_dir).resolve() if args.out_dir else None,
            book_title=args.title,
        )
        print(f"html: {artifact.html_path}")
        print(f"folio_render: {artifact.folio_render_path}")
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
    print(f"orientations: {artifact.orientations_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")
    print(f"orientation_model: {artifact.orientation_model}")
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


def cmd_assemble(args: argparse.Namespace) -> None:
    artifact = run_page_assembly(Path(args.probe_dir))
    print(f"assembly_json: {artifact.assembly_json_path}")
    print(f"assembly_md: {artifact.assembly_md_path}")
    print(f"meta: {artifact.meta_path}")


def cmd_resolve_overlap(args: argparse.Namespace) -> None:
    artifact = run_overlap_resolution(
        Path(args.probe_dir),
        model=args.model,
        prompt_file=Path(args.prompt_file).resolve() if args.prompt_file else None,
    )
    print(f"overlap_resolution: {artifact.resolution_json_path}")
    print(f"meta: {artifact.meta_path}")
    print(f"model: {artifact.model}")


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("page", help="Prepare, packetize, read, and synthesize page-level witness artifacts")
    sub = parser.add_subparsers(dest="page_cmd", required=True)

    prepare = sub.add_parser("prepare", help="Deterministically crop a page down to its manuscript content area")
    prepare.add_argument("--image", required=True, help="Source image to prepare")
    prepare.add_argument("--out-dir", help="Output directory for prepared image artifact")
    prepare.set_defaults(func=cmd_prepare)

    packet = sub.add_parser("packet", help="Create a scholar-facing page packet with stubs and edition template")
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
        help=f"Model for region orientation reads (default: {DEFAULT_MODEL_READING})",
    )
    packet.add_argument("--no-orient", action="store_true", help="Skip region orientation reads during the layout probe")
    packet.add_argument("--skip-overlap-resolution", action="store_true", help="Skip overlap adjudication between inclusive regions")
    packet.add_argument(
        "--overlap-model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for overlap adjudication (default: {DEFAULT_MODEL_TRIAGE})",
    )
    packet.add_argument("--retries", type=int, default=2, help="Retry count for model-backed packet build stages")
    packet.set_defaults(func=cmd_packet)

    ingest = sub.add_parser("ingest-reading", help="Deterministically ingest a page read markdown into a page packet")
    ingest.add_argument("--packet", required=True, help="Path to packet.json")
    ingest.add_argument("--reading", required=True, help="Path to page reading markdown")
    ingest.set_defaults(func=cmd_ingest_reading)

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

    render = sub.add_parser("render", help="Deterministically compile a packet edition PDF with tectonic")
    render.add_argument("--packet", required=True, help="Path to packet.json")
    render.add_argument("--engine", help="Explicit path to the tectonic binary")
    render.add_argument("--keep-logs", action="store_true", help="Ask tectonic to keep LaTeX log files")
    render.set_defaults(func=cmd_render)

    render_html = sub.add_parser("render-html", help="Render an HTML folio edition from one packet")
    render_html.add_argument("--packet", required=True, help="Path to packet.json")
    render_html.add_argument("--out-dir", help="Optional output directory for HTML folio artifacts")
    render_html.add_argument("--title", help="Optional book/manuscript title override")
    render_html.set_defaults(func=cmd_render_html)

    refresh_packet = sub.add_parser("refresh-packet", help="Backfill layout/assembly for an existing packet and optionally rerender HTML")
    refresh_packet.add_argument("--packet", required=True, help="Path to packet.json")
    refresh_packet.add_argument("--out-dir", help="Optional output directory for HTML folio artifacts")
    refresh_packet.add_argument("--title", help="Optional book/manuscript title override")
    refresh_packet.add_argument("--skip-layout-probe", action="store_true", help="Reuse existing layout probe artifacts")
    refresh_packet.add_argument("--skip-overlap-resolution", action="store_true", help="Reuse existing overlap resolution artifact")
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
        help=f"Model for region orientation reads (default: {DEFAULT_MODEL_READING})",
    )
    refresh_packet.add_argument("--no-orient", action="store_true", help="Skip region orientation reads during the layout probe")
    refresh_packet.add_argument(
        "--overlap-model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for overlap adjudication (default: {DEFAULT_MODEL_TRIAGE})",
    )
    refresh_packet.add_argument("--retries", type=int, default=2, help="Retry count for model-backed refresh stages")
    refresh_packet.set_defaults(func=cmd_refresh_packet)

    layout_probe = sub.add_parser("layout-probe", help="Run a fast layout+bbox probe and generate overlay/crops")
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
        help=f"Model for region orientation reads (default: {DEFAULT_MODEL_READING})",
    )
    layout_probe.add_argument("--no-orient", action="store_true", help="Skip the second-pass region orientation reads")
    layout_probe.set_defaults(func=cmd_layout_probe)

    region_read = sub.add_parser("region-read", help="Run or rerun crop-level witness reads from a layout probe")
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

    resolve_overlap = sub.add_parser("resolve-overlap", help="Adjudicate duplicate text across overlapping coarse regions")
    resolve_overlap.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    resolve_overlap.add_argument("--prompt-file", help="Optional prompt file override")
    resolve_overlap.add_argument(
        "--model",
        default=DEFAULT_MODEL_TRIAGE,
        help=f"Model for overlap adjudication (default: {DEFAULT_MODEL_TRIAGE})",
    )
    resolve_overlap.set_defaults(func=cmd_resolve_overlap)

    assemble = sub.add_parser("assemble", help="Assemble region reads from a layout probe into one page object")
    assemble.add_argument("--probe-dir", required=True, help="Path to the layout_probe artifact directory")
    assemble.set_defaults(func=cmd_assemble)

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
