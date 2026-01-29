#!/usr/bin/env python3
"""Render page.json as a beautiful scanlation HTML.

Usage:
  python scripts/render_scanlation.py \
    --page projects/vatican_alchemy/pages/pal_lat_1267_f020r.page.json \
    --out projects/vatican_alchemy/exports/scanlation/pal_lat_1267_f020r.html \
    --layer en_interpreted
"""
import argparse
import json
from pathlib import Path
from typing import Optional
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from palimpsest.models import Page, Zone

# Template and CSS paths
TEMPLATE_DIR = Path(__file__).parent.parent / "palimpsest" / "templates"
TEMPLATE_PATH = TEMPLATE_DIR / "scanlation.html"
CSS_PATH = TEMPLATE_DIR / "scanlation.css"

# Available text layers with display labels
LAYER_OPTIONS = [
    ("la_diplomatic", "Latin (Diplomatic)"),
    ("la_normalized", "Latin (Normalized)"),
    ("es_diplomatic", "Spanish (Diplomatic)"),
    ("es_normalized", "Spanish (Normalized)"),
    ("en_literal", "English (Literal)"),
    ("en_interpreted", "English (Interpreted)"),
]


def get_zone_style_classes(zone: Zone) -> str:
    """Build CSS class string for zone styling."""
    classes = [f"zone zone-{zone.type}"]

    if zone.style:
        if zone.style.color == "faded":
            classes.append("style-faded")
        if zone.style.font_size == "large":
            classes.append("style-large")
        elif zone.style.font_size == "small":
            classes.append("style-small")
        if zone.style.color == "initial_blue":
            pass  # handled by zone-initial class
        elif zone.style.decoration == "gilded":
            classes.append("gold")

    return " ".join(classes)


def get_zone_position_style(zone: Zone) -> str:
    """Build inline style for positioned zones (marginalia)."""
    if zone.type == "marginalia":
        x, y, w, h = zone.bbox_norm
        # Determine left vs right margin based on x position
        side_class = "left" if x < 0.15 else "right"
        top_pct = y * 100
        return f"top: {top_pct:.1f}%", side_class
    return "", ""


def render_zone(zone: Zone, layer: str) -> str:
    """Render a single zone as HTML."""
    text = zone.text.get_layer(layer) or zone.text.primary_text() or ""

    classes = get_zone_style_classes(zone)
    pos_style, side_class = get_zone_position_style(zone)

    if side_class:
        classes += f" {side_class}"

    style_attr = f' style="{pos_style}"' if pos_style else ""

    return f'<span class="{classes}" data-zone-id="{zone.zone_id}"{style_attr}>{escape_html(text)}</span>'


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_scanlation(
    page_path: Path,
    output_path: Path,
    layer: str = "en_interpreted",
    image_path: Optional[str] = None,
) -> None:
    """Render page.json as scanlation HTML."""
    # Load page
    page = Page.from_file(str(page_path))

    # Load template and CSS
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    # Determine available layers for this page
    available_layers = []
    for layer_name, layer_label in LAYER_OPTIONS:
        # Check if any zone has this layer
        for zone in page.zones:
            if zone.text.get_layer(layer_name):
                available_layers.append((layer_name, layer_label))
                break

    # Default to first available layer if requested layer not available
    if layer not in [l[0] for l in available_layers] and available_layers:
        layer = available_layers[0][0]

    # Resolve image path
    if image_path is None:
        image_path = page.image.path
        # Make relative to output directory
        output_dir = output_path.parent
        page_dir = page_path.parent.parent.parent  # projects/<proj>/pages -> projects/<proj>
        img_full = page_dir / ".." / ".." / page.image.path
        try:
            image_path = str(img_full.resolve().relative_to(output_dir.resolve()))
        except ValueError:
            image_path = str(img_full.resolve())

    # Sort zones by reading order
    zones = page.zones_by_order()

    # Build zone HTML
    zones_html = "\n          ".join(render_zone(z, layer) for z in zones)

    # Build the HTML by simple string replacement
    # This avoids Jinja2 dependency while still being flexible
    html = template

    # Replace template variables
    html = html.replace("{{ page.page_id }}", page.page_id)
    html = html.replace("{{ css }}", css)
    html = html.replace("{{ image_path }}", image_path)
    html = html.replace("{{ page_json | safe }}", page.model_dump_json())
    html = html.replace(
        "{{ available_layers_json | safe }}", json.dumps(available_layers)
    )

    # Replace layer select options
    layer_options_html = "\n          ".join(
        f'<option value="{ln}"{" selected" if ln == layer else ""}>{ll}</option>'
        for ln, ll in available_layers
    )
    # Find and replace the layer select block
    import re

    html = re.sub(
        r"{%\s*for layer_name, layer_label in available_layers\s*%}.*?{%\s*endfor\s*%}",
        layer_options_html,
        html,
        flags=re.DOTALL,
    )

    # Replace zone rendering loop
    html = re.sub(
        r"{%\s*for zone in zones\s*%}.*?{%\s*endfor\s*%}",
        zones_html,
        html,
        flags=re.DOTALL,
    )

    # Replace layout conditionals
    columns = page.layout.columns if page.layout else 1
    col_class = "two-column" if columns == 2 else "single-column"
    html = re.sub(
        r"{%\s*if layout and layout\.columns == 2\s*%}two-column{%\s*else\s*%}single-column{%\s*endif\s*%}",
        col_class,
        html,
    )

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write output
    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Rendered scanlation: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Render page.json as scanlation HTML"
    )
    parser.add_argument(
        "--page", required=True, help="Path to page.json file"
    )
    parser.add_argument(
        "--out", required=True, help="Output HTML path"
    )
    parser.add_argument(
        "--layer",
        default="en_interpreted",
        choices=[l[0] for l in LAYER_OPTIONS],
        help="Default text layer to display",
    )
    parser.add_argument(
        "--image",
        help="Override image path (default: from page.json)",
    )

    args = parser.parse_args()

    render_scanlation(
        page_path=Path(args.page),
        output_path=Path(args.out),
        layer=args.layer,
        image_path=args.image,
    )


if __name__ == "__main__":
    main()
