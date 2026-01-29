#!/usr/bin/env python3
"""CLI for page reconstruction.

Generates English scanlation reconstructions from page.json files.

Usage:
    python scripts/reconstruct_page.py \
        --page projects/pal_lat_1985/pages/f002r.page.json \
        --image projects/pal_lat_1985/images/f002r.jpg \
        --out projects/pal_lat_1985/reconstruction/f002r/ \
        --mode hybrid

    python scripts/reconstruct_page.py \
        --page projects/pal_lat_1985/pages/f002r.page.json \
        --image projects/pal_lat_1985/images/f002r.jpg \
        --overlay-only \
        --out overlay.png
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from palimpsest.models import Page
from palimpsest.pipeline.reconstruction import (
    ReconstructionPipeline,
    ReconstructionMode,
)


def generate_viewer_html(
    output_dir: Path,
    page: Page,
    saved_files: dict,
) -> Path:
    """Generate interactive HTML viewer for reconstruction."""
    # Read the template
    template_path = Path(__file__).parent.parent / "palimpsest" / "templates" / "reconstruction_viewer.html"

    if template_path.exists():
        html_content = template_path.read_text(encoding="utf-8")
    else:
        # Fallback inline template
        html_content = _get_default_viewer_template()

    # Replace placeholders
    html_content = html_content.replace("{{PAGE_ID}}", page.page_id)
    html_content = html_content.replace("{{DOC_ID}}", page.doc_id)

    # File paths (relative to viewer)
    original_path = "original.jpg" if "original" in saved_files else ""
    overlay_path = "overlay.png" if "overlay" in saved_files else ""
    reconstructed_path = "reconstructed.png" if "reconstructed" in saved_files else ""
    comparison_path = "comparison.png" if "comparison" in saved_files else ""

    html_content = html_content.replace("{{ORIGINAL_PATH}}", original_path)
    html_content = html_content.replace("{{OVERLAY_PATH}}", overlay_path)
    html_content = html_content.replace("{{RECONSTRUCTED_PATH}}", reconstructed_path)
    html_content = html_content.replace("{{COMPARISON_PATH}}", comparison_path)

    # Page JSON for interactivity
    html_content = html_content.replace("{{PAGE_JSON}}", page.to_json())

    viewer_path = output_dir / "viewer.html"
    viewer_path.write_text(html_content, encoding="utf-8")
    return viewer_path


def _get_default_viewer_template() -> str:
    """Return default viewer HTML template."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{PAGE_ID}} | Reconstruction Viewer</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Georgia', 'Times New Roman', serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
        }
        .header {
            background: #16213e;
            padding: 1rem 2rem;
            border-bottom: 2px solid #e94560;
        }
        .header h1 {
            font-size: 1.5rem;
            font-weight: normal;
        }
        .header .doc-id {
            color: #888;
            font-size: 0.9rem;
        }
        .controls {
            background: #0f3460;
            padding: 1rem 2rem;
            display: flex;
            gap: 2rem;
            flex-wrap: wrap;
            align-items: center;
        }
        .controls label {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .controls select, .controls input[type="range"] {
            padding: 0.3rem;
            border-radius: 4px;
            border: 1px solid #444;
            background: #1a1a2e;
            color: #eee;
        }
        .view-buttons {
            display: flex;
            gap: 0.5rem;
        }
        .view-buttons button {
            padding: 0.5rem 1rem;
            border: 1px solid #e94560;
            background: transparent;
            color: #e94560;
            cursor: pointer;
            border-radius: 4px;
            transition: all 0.2s;
        }
        .view-buttons button.active,
        .view-buttons button:hover {
            background: #e94560;
            color: white;
        }
        .viewer {
            display: flex;
            justify-content: center;
            padding: 2rem;
            min-height: calc(100vh - 200px);
        }
        .image-container {
            position: relative;
            max-width: 100%;
            max-height: 80vh;
        }
        .image-container img {
            max-width: 100%;
            max-height: 80vh;
            display: block;
        }
        .overlay-img {
            position: absolute;
            top: 0;
            left: 0;
            pointer-events: none;
        }
        .hidden { display: none; }
        .side-by-side {
            display: flex;
            gap: 1rem;
        }
        .side-by-side .image-container {
            flex: 1;
        }
        .opacity-slider {
            width: 150px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>{{PAGE_ID}}</h1>
        <div class="doc-id">{{DOC_ID}}</div>
    </div>

    <div class="controls">
        <div class="view-buttons">
            <button id="btn-original" class="active">Original</button>
            <button id="btn-overlay">With Translation</button>
            <button id="btn-reconstructed">Reconstructed</button>
            <button id="btn-compare">Side by Side</button>
        </div>

        <label>
            Overlay Opacity:
            <input type="range" id="opacity" class="opacity-slider" min="0" max="100" value="100">
        </label>
    </div>

    <div class="viewer">
        <!-- Original view -->
        <div id="view-original" class="image-container">
            <img src="{{ORIGINAL_PATH}}" alt="Original manuscript page">
        </div>

        <!-- Overlay view -->
        <div id="view-overlay" class="image-container hidden">
            <img src="{{ORIGINAL_PATH}}" alt="Original">
            <img src="{{OVERLAY_PATH}}" alt="Translation overlay" class="overlay-img" id="overlay-img">
        </div>

        <!-- Reconstructed view -->
        <div id="view-reconstructed" class="image-container hidden">
            <img src="{{RECONSTRUCTED_PATH}}" alt="Reconstructed page">
        </div>

        <!-- Comparison view -->
        <div id="view-compare" class="side-by-side hidden">
            <div class="image-container">
                <img src="{{ORIGINAL_PATH}}" alt="Original">
            </div>
            <div class="image-container">
                <img src="{{RECONSTRUCTED_PATH}}" alt="Reconstructed">
            </div>
        </div>
    </div>

    <script>
        const PAGE = {{PAGE_JSON}};

        // View switching
        const views = ['original', 'overlay', 'reconstructed', 'compare'];
        views.forEach(view => {
            const btn = document.getElementById(`btn-${view}`);
            if (btn) {
                btn.addEventListener('click', () => {
                    views.forEach(v => {
                        document.getElementById(`view-${v}`)?.classList.add('hidden');
                        document.getElementById(`btn-${v}`)?.classList.remove('active');
                    });
                    document.getElementById(`view-${view}`)?.classList.remove('hidden');
                    btn.classList.add('active');
                });
            }
        });

        // Opacity control
        const opacitySlider = document.getElementById('opacity');
        const overlayImg = document.getElementById('overlay-img');
        if (opacitySlider && overlayImg) {
            opacitySlider.addEventListener('input', (e) => {
                overlayImg.style.opacity = e.target.value / 100;
            });
        }
    </script>
</body>
</html>
'''


def main():
    parser = argparse.ArgumentParser(
        description="Generate English scanlation reconstruction from page.json"
    )
    parser.add_argument(
        "--page", required=True,
        help="Path to page.json file"
    )
    parser.add_argument(
        "--image",
        help="Path to original image (if not in page.json)"
    )
    parser.add_argument(
        "--out", required=True,
        help="Output directory or file path"
    )
    parser.add_argument(
        "--mode", choices=["overlay", "clean", "hybrid", "scholarly"],
        default="hybrid",
        help="Reconstruction mode (default: hybrid)"
    )
    parser.add_argument(
        "--layer", default="en_interpreted",
        help="Text layer to use (default: en_interpreted)"
    )
    parser.add_argument(
        "--overlay-only", action="store_true",
        help="Only generate overlay PNG (--out is the file path)"
    )
    parser.add_argument(
        "--no-viewer", action="store_true",
        help="Skip generating HTML viewer"
    )
    parser.add_argument(
        "--model", default="gemini-3-pro-image-preview",
        help="Model for image generation"
    )

    args = parser.parse_args()

    # Load page JSON
    page_path = Path(args.page)
    if not page_path.exists():
        print(f"Page file not found: {page_path}", file=sys.stderr)
        sys.exit(1)

    page = Page.from_file(str(page_path))
    print(f"Loaded page: {page.page_id} from {page.doc_id}")

    # Determine image path
    if args.image:
        image_path = Path(args.image)
    else:
        image_path = Path(page.image.path)
        if not image_path.is_absolute():
            # Try relative to page file
            image_path = page_path.parent / image_path

    if not image_path.exists():
        print(f"Image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Using image: {image_path}")

    # Initialize pipeline
    pipeline = ReconstructionPipeline(model=args.model)

    # Overlay-only mode
    if args.overlay_only:
        print("Generating overlay only...")
        overlay = pipeline.generate_text_overlay(page, args.layer)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(overlay)
        print(f"Saved overlay: {out_path}")
        return

    # Full reconstruction
    print(f"Generating {args.mode} reconstruction...")
    result = pipeline.reconstruct_page(
        original_image=image_path,
        page_json=page,
        mode=args.mode,
        text_layer=args.layer,
    )

    # Save package
    output_dir = Path(args.out)
    saved = result.save_package(output_dir)

    print(f"\nSaved reconstruction package to {output_dir}:")
    for name, path in saved.items():
        print(f"  - {name}: {path.name}")

    # Generate viewer
    if not args.no_viewer:
        viewer_path = generate_viewer_html(output_dir, page, saved)
        print(f"  - viewer: {viewer_path.name}")

    print(f"\nReconstruction complete!")
    print(f"Open {output_dir / 'viewer.html'} in a browser to view.")


if __name__ == "__main__":
    main()
