#!/usr/bin/env python3
"""
Scanlation Generator - Create manga-style translated manuscript pages.

Takes original manuscript images + AI analysis and creates HTML pages
with English overlays and explanatory captions.

Usage:
    python scripts/scanlate.py --project projects/pal_lat_1985 --out exports/scanlation
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - {folio}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            background: #1a1a1a;
            color: #e0e0e0;
            font-family: 'Segoe UI', system-ui, sans-serif;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 0;
            border-bottom: 1px solid #333;
            margin-bottom: 20px;
        }}

        h1 {{
            font-size: 1.5rem;
            font-weight: 400;
        }}

        .nav {{
            display: flex;
            gap: 10px;
        }}

        .nav a {{
            color: #888;
            text-decoration: none;
            padding: 8px 16px;
            border: 1px solid #333;
            border-radius: 4px;
            transition: all 0.2s;
        }}

        .nav a:hover {{
            color: #fff;
            border-color: #666;
        }}

        .main-content {{
            display: grid;
            grid-template-columns: 1fr 400px;
            gap: 30px;
        }}

        @media (max-width: 1000px) {{
            .main-content {{
                grid-template-columns: 1fr;
            }}
        }}

        .image-panel {{
            position: relative;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
        }}

        .image-panel img {{
            width: 100%;
            height: auto;
            display: block;
        }}

        .info-panel {{
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}

        .card {{
            background: #252525;
            border-radius: 8px;
            padding: 20px;
        }}

        .card h2 {{
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #888;
            margin-bottom: 15px;
        }}

        .triumph-type {{
            font-size: 1.8rem;
            color: #c9a227;
            margin-bottom: 10px;
        }}

        .description {{
            line-height: 1.6;
            color: #aaa;
        }}

        .figure-list {{
            list-style: none;
        }}

        .figure-list li {{
            padding: 10px 0;
            border-bottom: 1px solid #333;
            display: grid;
            grid-template-columns: 120px 1fr;
            gap: 10px;
        }}

        .figure-list li:last-child {{
            border-bottom: none;
        }}

        .label-latin {{
            font-style: italic;
            color: #888;
        }}

        .label-english {{
            color: #e0e0e0;
        }}

        .figure-desc {{
            grid-column: 1 / -1;
            font-size: 0.9rem;
            color: #777;
            padding-top: 5px;
        }}

        .significance {{
            background: #1e2a1e;
            border-left: 3px solid #4a7c4a;
        }}

        footer {{
            margin-top: 40px;
            padding: 20px 0;
            border-top: 1px solid #333;
            text-align: center;
            color: #555;
            font-size: 0.85rem;
        }}

        footer a {{
            color: #888;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{title}</h1>
            <div class="nav">
                {nav_links}
            </div>
        </header>

        <div class="main-content">
            <div class="image-panel">
                <img src="{image_path}" alt="{folio}">
            </div>

            <div class="info-panel">
                <div class="card">
                    <h2>Folio {folio}</h2>
                    <div class="triumph-type">{triumph_type}</div>
                    <p class="description">{description}</p>
                </div>

                <div class="card">
                    <h2>Labeled Figures</h2>
                    <ul class="figure-list">
                        {figure_list}
                    </ul>
                </div>

                {significance_card}
            </div>
        </div>

        <footer>
            <p>Scanlation generated by <a href="https://github.com/anthropics/palimpsest">Palimpsest</a> |
               Source: <a href="{source_url}" target="_blank">Vatican Digital Library</a> |
               Generated: {date}</p>
        </footer>
    </div>
</body>
</html>
'''

INDEX_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Scanlation</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            background: #1a1a1a;
            color: #e0e0e0;
            font-family: 'Segoe UI', system-ui, sans-serif;
            min-height: 100vh;
            padding: 40px 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        h1 {{
            font-size: 2.5rem;
            font-weight: 300;
            margin-bottom: 10px;
        }}

        .subtitle {{
            color: #888;
            font-size: 1.1rem;
            margin-bottom: 40px;
        }}

        .metadata {{
            background: #252525;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 40px;
        }}

        .metadata p {{
            margin: 5px 0;
            color: #aaa;
        }}

        .metadata strong {{
            color: #c9a227;
        }}

        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px;
        }}

        .page-card {{
            background: #252525;
            border-radius: 8px;
            overflow: hidden;
            transition: transform 0.2s;
        }}

        .page-card:hover {{
            transform: translateY(-5px);
        }}

        .page-card a {{
            text-decoration: none;
            color: inherit;
        }}

        .page-card img {{
            width: 100%;
            height: 200px;
            object-fit: cover;
            object-position: top;
        }}

        .page-card .info {{
            padding: 15px;
        }}

        .page-card h3 {{
            font-size: 1rem;
            margin-bottom: 5px;
        }}

        .page-card .type {{
            color: #c9a227;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <p class="subtitle">{subtitle}</p>

        <div class="metadata">
            <p><strong>Shelfmark:</strong> {shelfmark}</p>
            <p><strong>Date:</strong> {date_range}</p>
            <p><strong>Contents:</strong> {contents}</p>
            <p><strong>Source:</strong> <a href="{source_url}" style="color: #888;">{source_url}</a></p>
        </div>

        <div class="gallery">
            {page_cards}
        </div>
    </div>
</body>
</html>
'''


def parse_analysis(analysis_path: Path) -> dict:
    """Parse AI analysis file into structured data."""
    text = analysis_path.read_text(encoding="utf-8")

    result = {
        "type": "unknown",
        "description": "",
        "figures": [],
        "significance": "",
    }

    # Extract TYPE
    type_match = re.search(r'TYPE:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if type_match:
        result["type"] = type_match.group(1).strip()

    # Extract CENTRAL ALLEGORY
    allegory_match = re.search(r'CENTRAL ALLEGORY:\s*\n(.+?)(?:\n\n|\nLABELED)', text, re.DOTALL | re.IGNORECASE)
    if allegory_match:
        result["description"] = allegory_match.group(1).strip()

    # Extract LABELED FIGURES
    figures_match = re.search(r'LABELED FIGURES:\s*\n(.+?)(?:\n\nUNLABELED|\n\nMYTHOLOGICAL|\n\nSETTING)', text, re.DOTALL | re.IGNORECASE)
    if figures_match:
        figures_text = figures_match.group(1)
        # Parse individual figures
        for line in figures_text.split('\n'):
            # Match pattern: N. "Latin" → English → Description
            fig_match = re.match(r'\d+\.\s*["\']?([^"\'→]+)["\']?\s*→\s*([^→]+)→\s*(.+)', line)
            if fig_match:
                result["figures"].append({
                    "latin": fig_match.group(1).strip(),
                    "english": fig_match.group(2).strip(),
                    "description": fig_match.group(3).strip(),
                })

    # Extract SIGNIFICANCE
    sig_match = re.search(r'SIGNIFICANCE:\s*\n(.+?)(?:\n```|$)', text, re.DOTALL | re.IGNORECASE)
    if sig_match:
        result["significance"] = sig_match.group(1).strip()

    return result


def generate_page_html(
    folio: str,
    image_path: str,
    analysis: dict,
    title: str,
    prev_folio: str = None,
    next_folio: str = None,
    source_url: str = "",
) -> str:
    """Generate HTML for a single scanlation page."""

    # Build navigation links
    nav_parts = []
    if prev_folio:
        nav_parts.append(f'<a href="{prev_folio}.html">← Previous</a>')
    nav_parts.append('<a href="index.html">Index</a>')
    if next_folio:
        nav_parts.append(f'<a href="{next_folio}.html">Next →</a>')
    nav_links = "\n".join(nav_parts)

    # Build figure list
    figure_items = []
    for fig in analysis.get("figures", []):
        figure_items.append(f'''
        <li>
            <span class="label-latin">"{fig["latin"]}"</span>
            <span class="label-english">{fig["english"]}</span>
            <span class="figure-desc">{fig["description"]}</span>
        </li>
        ''')
    figure_list = "\n".join(figure_items) if figure_items else "<li>No labeled figures identified</li>"

    # Significance card
    significance_card = ""
    if analysis.get("significance"):
        significance_card = f'''
        <div class="card significance">
            <h2>Significance</h2>
            <p class="description">{analysis["significance"]}</p>
        </div>
        '''

    # Determine triumph type display
    triumph_type = analysis.get("type", "Unknown")
    if "triumph" in triumph_type.lower():
        triumph_type = triumph_type.title()

    return HTML_TEMPLATE.format(
        title=title,
        folio=folio,
        image_path=image_path,
        nav_links=nav_links,
        triumph_type=triumph_type,
        description=analysis.get("description", "No description available."),
        figure_list=figure_list,
        significance_card=significance_card,
        source_url=source_url,
        date=datetime.now().strftime("%Y-%m-%d"),
    )


def generate_index_html(
    title: str,
    subtitle: str,
    shelfmark: str,
    date_range: str,
    contents: str,
    source_url: str,
    pages: list,
) -> str:
    """Generate index page HTML."""

    page_cards = []
    for page in pages:
        page_cards.append(f'''
        <div class="page-card">
            <a href="{page["folio"]}.html">
                <img src="{page["image"]}" alt="{page["folio"]}">
                <div class="info">
                    <h3>{page["folio"]}</h3>
                    <span class="type">{page["type"]}</span>
                </div>
            </a>
        </div>
        ''')

    return INDEX_TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        shelfmark=shelfmark,
        date_range=date_range,
        contents=contents,
        source_url=source_url,
        page_cards="\n".join(page_cards),
    )


def main():
    parser = argparse.ArgumentParser(description="Generate scanlation pages")
    parser.add_argument("--project", required=True, help="Project directory")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--title", default="Manuscript Scanlation", help="Title")
    parser.add_argument("--shelfmark", default="Unknown", help="Shelfmark")
    parser.add_argument("--source-url", default="", help="Source URL")

    args = parser.parse_args()

    project_dir = Path(args.project)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    images_dir = project_dir / "images"
    analysis_dir = project_dir / "analysis"

    if not images_dir.exists():
        print(f"Error: Images directory not found: {images_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all folio images
    folios = []
    for img in sorted(images_dir.glob("f0*.jpg")):
        folio = img.stem
        analysis_path = analysis_dir / f"{folio}_analysis.txt"

        if analysis_path.exists():
            analysis = parse_analysis(analysis_path)
        else:
            analysis = {"type": "Unknown", "description": "", "figures": [], "significance": ""}

        folios.append({
            "folio": folio,
            "image": img.name,
            "image_path": img,
            "analysis": analysis,
            "type": analysis.get("type", "Unknown"),
        })

    print(f"Found {len(folios)} folios to process")

    # Copy images to output
    images_out = out_dir / "images"
    images_out.mkdir(exist_ok=True)

    for f in folios:
        import shutil
        shutil.copy(f["image_path"], images_out / f["image"])

    print(f"Copied images to {images_out}")

    # Generate individual pages
    for i, f in enumerate(folios):
        prev_folio = folios[i-1]["folio"] if i > 0 else None
        next_folio = folios[i+1]["folio"] if i < len(folios)-1 else None

        html = generate_page_html(
            folio=f["folio"],
            image_path=f"images/{f['image']}",
            analysis=f["analysis"],
            title=args.title,
            prev_folio=prev_folio,
            next_folio=next_folio,
            source_url=args.source_url,
        )

        out_path = out_dir / f"{f['folio']}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"  Generated {out_path.name}")

    # Generate index
    index_html = generate_index_html(
        title=args.title,
        subtitle="First English Scanlation",
        shelfmark=args.shelfmark,
        date_range="c. 1565-1590",
        contents="Expanded Trionfi cycle with Triumphs of Fame, Avarice, Learning, Libido, and Death",
        source_url=args.source_url,
        pages=[{"folio": f["folio"], "image": f"images/{f['image']}", "type": f["type"]} for f in folios],
    )

    index_path = out_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    print(f"  Generated index.html")

    print(f"\nDone! Open {out_dir / 'index.html'} to view")


if __name__ == "__main__":
    main()
