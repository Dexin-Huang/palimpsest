#!/usr/bin/env python3
"""Master pipeline for Palimpsest manuscript processing.

Orchestrates all three efforts:
1. Discovery - Find and catalog manuscripts from IIIF sources
2. Vision Analysis - Multi-pass transcription and translation
3. Reconstruction - Generate English scanlation packages

Usage:
    # Full pipeline for a manuscript by shelfmark
    python scripts/palimpsest_pipeline.py --shelfmark Pal.lat.1985 --all

    # Discovery only
    python scripts/palimpsest_pipeline.py --shelfmark Pal.lat.1267 --discover

    # Vision analysis for existing project
    python scripts/palimpsest_pipeline.py --project projects/pal_lat_1985 --analyze

    # Reconstruction from analyzed pages
    python scripts/palimpsest_pipeline.py --project projects/pal_lat_1985 --reconstruct

    # Specific folios
    python scripts/palimpsest_pipeline.py --project projects/pal_lat_1985 --analyze --folios f001r,f001v,f002r
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env if present (for GEMINI_API_KEY)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    pass


def discover_manuscript(
    shelfmark: str,
    db_path: Path,
    output_dir: Path,
) -> dict:
    """Discover and catalog a manuscript.

    Args:
        shelfmark: Manuscript shelfmark (e.g., "Pal.lat.1985")
        db_path: Path to discovery database
        output_dir: Project output directory

    Returns:
        Manuscript info dict
    """
    from palimpsest.discovery import DiscoveryDB, Manuscript

    print(f"\n{'='*60}")
    print(f"  EFFORT 1: DISCOVERY")
    print(f"  Shelfmark: {shelfmark}")
    print(f"{'='*60}\n")

    db = DiscoveryDB(db_path)

    # Check if already in database
    ms = db.get_manuscript_by_shelfmark(shelfmark)

    if ms:
        print(f"Found in database: {ms.id}")
        print(f"  Status: {ms.status}")
        print(f"  Obscurity: {ms.obscurity_score}/10")
        print(f"  IIIF: {ms.iiif_manifest_url}")
    else:
        # Would need to fetch from IIIF API
        print(f"Not in database. Would fetch from IIIF API.")
        print("(IIIF discovery not implemented in this demo)")

        # Create placeholder
        ms_id = shelfmark.lower().replace(".", "_").replace(" ", "_")
        ms = Manuscript(
            id=ms_id,
            shelfmark=shelfmark,
            repository="BAV",
            status="discovered",
        )
        db.add_manuscript(ms, agent="pipeline")
        print(f"Created placeholder: {ms.id}")

    # Create project directory
    project_dir = output_dir / ms.id.replace(".", "_")
    for subdir in ["images", "pages", "reconstruction", "exports"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    print(f"Project directory: {project_dir}")

    db.close()

    return {
        "id": ms.id,
        "shelfmark": ms.shelfmark,
        "project_dir": str(project_dir),
        "iiif_manifest": ms.iiif_manifest_url,
    }


def analyze_project(
    project_dir: Path,
    folios: Optional[List[str]] = None,
    passes: Optional[List[str]] = None,
    save_agentic_trace: bool = False,
    trace_dir: Optional[str] = None,
) -> List[Path]:
    """Run vision analysis on project images.

    Args:
        project_dir: Path to project directory
        folios: Specific folios to process (None = all)
        passes: Which analysis passes to run

    Returns:
        List of generated page.json paths
    """
    from palimpsest.pipeline.vision_pipeline import VisionPipeline, PipelineConfig
    from palimpsest.models import Page

    print(f"\n{'='*60}")
    print(f"  EFFORT 2: VISION ANALYSIS")
    print(f"  Project: {project_dir}")
    print(f"{'='*60}\n")

    images_dir = project_dir / "images"
    pages_dir = project_dir / "pages"

    if not images_dir.exists():
        print(f"Images directory not found: {images_dir}")
        return []

    # Find images to process
    image_files = sorted(
        p for p in images_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff")
    )

    if folios:
        # Filter to specific folios
        folio_set = set(folios)
        image_files = [
            p for p in image_files
            if any(f in p.stem.lower() for f in folio_set)
        ]

    if not image_files:
        print("No images found to process.")
        return []

    print(f"Found {len(image_files)} images to analyze")

    # Initialize pipeline
    config = PipelineConfig(
        passes=passes,
        save_agentic_trace=save_agentic_trace,
        trace_dir=trace_dir,
    ) if passes else PipelineConfig(
        save_agentic_trace=save_agentic_trace,
        trace_dir=trace_dir,
    )
    pipeline = VisionPipeline(config=config)

    # Extract doc_id from project directory name
    doc_id = project_dir.name

    # Process each image
    page_paths = []
    for img_path in image_files:
        page_id = img_path.stem.lower()

        # Skip if already processed (unless forcing)
        page_json_path = pages_dir / f"{page_id}.page.json"
        if page_json_path.exists():
            print(f"[SKIP] {page_id} - already processed")
            page_paths.append(page_json_path)
            continue

        print(f"[PROCESSING] {page_id}...")

        try:
            page = pipeline.analyze_page(
                image_path=img_path,
                doc_id=doc_id,
                page_id=page_id,
            )

            # Save page JSON
            page.save(str(page_json_path))
            page_paths.append(page_json_path)
            print(f"[OK] {page_id} - {len(page.zones)} zones")

        except Exception as e:
            print(f"[ERROR] {page_id} - {e}")

    print(f"\nAnalyzed {len(page_paths)} pages")
    return page_paths


def reconstruct_project(
    project_dir: Path,
    folios: Optional[List[str]] = None,
    mode: str = "hybrid",
) -> List[Path]:
    """Generate reconstruction packages for project pages.

    Args:
        project_dir: Path to project directory
        folios: Specific folios to reconstruct (None = all)
        mode: Reconstruction mode

    Returns:
        List of reconstruction output directories
    """
    from palimpsest.pipeline.reconstruction import ReconstructionPipeline
    from palimpsest.models import Page

    print(f"\n{'='*60}")
    print(f"  EFFORT 3: RECONSTRUCTION")
    print(f"  Project: {project_dir}")
    print(f"  Mode: {mode}")
    print(f"{'='*60}\n")

    pages_dir = project_dir / "pages"
    images_dir = project_dir / "images"
    reconstruction_dir = project_dir / "reconstruction"

    if not pages_dir.exists():
        print(f"Pages directory not found: {pages_dir}")
        return []

    # Find page JSONs to process
    page_files = sorted(pages_dir.glob("*.page.json"))

    if folios:
        folio_set = set(folios)
        page_files = [
            p for p in page_files
            if any(f in p.stem.lower() for f in folio_set)
        ]

    if not page_files:
        print("No page.json files found to reconstruct.")
        return []

    print(f"Found {len(page_files)} pages to reconstruct")

    # Initialize pipeline
    pipeline = ReconstructionPipeline()

    output_dirs = []
    for page_path in page_files:
        page = Page.from_file(str(page_path))
        page_id = page.page_id

        # Find corresponding image
        image_path = None
        for ext in [".jpg", ".jpeg", ".png", ".tif", ".tiff"]:
            candidate = images_dir / f"{page_id}{ext}"
            if candidate.exists():
                image_path = candidate
                break

        if not image_path:
            print(f"[SKIP] {page_id} - image not found")
            continue

        # Output directory
        output_dir = reconstruction_dir / page_id

        # Skip if already reconstructed
        if (output_dir / "viewer.html").exists():
            print(f"[SKIP] {page_id} - already reconstructed")
            output_dirs.append(output_dir)
            continue

        print(f"[RECONSTRUCTING] {page_id}...")

        try:
            result = pipeline.reconstruct_page(
                original_image=image_path,
                page_json=page,
                mode=mode,
            )

            saved = result.save_package(output_dir)

            # Generate viewer
            from scripts.reconstruct_page import generate_viewer_html
            generate_viewer_html(output_dir, page, saved)

            output_dirs.append(output_dir)
            print(f"[OK] {page_id}")

        except Exception as e:
            print(f"[ERROR] {page_id} - {e}")

    print(f"\nReconstructed {len(output_dirs)} pages")
    return output_dirs


def run_full_pipeline(
    shelfmark: str,
    output_base: Path,
    db_path: Path,
    folios: Optional[List[str]] = None,
    mode: str = "hybrid",
):
    """Run the complete three-effort pipeline.

    Args:
        shelfmark: Manuscript shelfmark
        output_base: Base directory for projects
        db_path: Path to discovery database
        folios: Specific folios to process
        mode: Reconstruction mode
    """
    print("\n" + "="*60)
    print("  PALIMPSEST PIPELINE")
    print("  Three-Effort Manuscript Processing")
    print("="*60)

    # Effort 1: Discovery
    ms_info = discover_manuscript(shelfmark, db_path, output_base)
    project_dir = Path(ms_info["project_dir"])

    # Effort 2: Vision Analysis
    page_paths = analyze_project(project_dir, folios)

    # Effort 3: Reconstruction
    if page_paths:
        output_dirs = reconstruct_project(project_dir, folios, mode)
    else:
        output_dirs = []

    # Summary
    print("\n" + "="*60)
    print("  PIPELINE COMPLETE")
    print("="*60)
    print(f"  Manuscript:     {shelfmark}")
    print(f"  Project:        {project_dir}")
    print(f"  Pages analyzed: {len(page_paths)}")
    print(f"  Reconstructed:  {len(output_dirs)}")

    if output_dirs:
        print(f"\n  View results:")
        for od in output_dirs[:3]:
            print(f"    - {od / 'viewer.html'}")
        if len(output_dirs) > 3:
            print(f"    ... and {len(output_dirs) - 3} more")

    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Palimpsest Master Pipeline - Three-Effort Manuscript Processing"
    )

    # Input specification
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--shelfmark",
        help="Manuscript shelfmark (e.g., Pal.lat.1985)"
    )
    input_group.add_argument(
        "--project",
        help="Path to existing project directory"
    )

    # Operation modes
    parser.add_argument(
        "--all", action="store_true",
        help="Run full pipeline (discover + analyze + reconstruct)"
    )
    parser.add_argument(
        "--discover", action="store_true",
        help="Run discovery effort only"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Run vision analysis effort"
    )
    parser.add_argument(
        "--reconstruct", action="store_true",
        help="Run reconstruction effort"
    )

    # Options
    parser.add_argument(
        "--folios",
        help="Comma-separated list of folios to process (e.g., f001r,f001v)"
    )
    parser.add_argument(
        "--passes",
        help="Comma-separated analysis passes (segmentation,transcription,translation,claims)"
    )
    parser.add_argument(
        "--agentic-trace",
        action="store_true",
        help="Save agentic vision code execution traces"
    )
    parser.add_argument(
        "--agentic-trace-dir",
        help="Override trace output directory"
    )
    parser.add_argument(
        "--mode", choices=["overlay", "clean", "hybrid", "scholarly"],
        default="hybrid",
        help="Reconstruction mode (default: hybrid)"
    )
    parser.add_argument(
        "--output", default="projects",
        help="Base output directory (default: projects)"
    )
    parser.add_argument(
        "--db", default="discovery/manuscripts.db",
        help="Discovery database path"
    )

    args = parser.parse_args()

    # Parse folios
    folios = None
    if args.folios:
        folios = [f.strip() for f in args.folios.split(",")]

    # Parse passes
    passes = None
    if args.passes:
        passes = [p.strip() for p in args.passes.split(",")]

    # Paths
    output_base = Path(args.output)
    db_path = Path(args.db)

    # Determine what to run
    if args.shelfmark:
        if args.all:
            run_full_pipeline(
                shelfmark=args.shelfmark,
                output_base=output_base,
                db_path=db_path,
                folios=folios,
                mode=args.mode,
            )
        elif args.discover:
            discover_manuscript(args.shelfmark, db_path, output_base)
        else:
            parser.error("With --shelfmark, specify --all or --discover")

    elif args.project:
        project_dir = Path(args.project)
        if not project_dir.exists():
            print(f"Project directory not found: {project_dir}")
            sys.exit(1)

        if args.analyze:
            analyze_project(
                project_dir,
                folios,
                passes,
                save_agentic_trace=args.agentic_trace,
                trace_dir=args.agentic_trace_dir,
            )
        if args.reconstruct:
            reconstruct_project(project_dir, folios, args.mode)
        if not args.analyze and not args.reconstruct:
            parser.error("With --project, specify --analyze and/or --reconstruct")


if __name__ == "__main__":
    main()
