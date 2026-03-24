#!/usr/bin/env python3
"""Generate a self-contained HTML overlay viewer for a given page JSON.

Usage:
  python scripts/render_html_single.py \
    --page projects/pasajeros_a_indias/pages/<page>.page.json \
    --out  projects/pasajeros_a_indias/exports/html/<page>.html
"""
import argparse, json
from pathlib import Path

TEMPLATE_PATH = Path('projects/pasajeros_a_indias/exports/html/demo_viewer.html')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    page = json.loads(Path(args.page).read_text(encoding='utf-8'))
    html = TEMPLATE_PATH.read_text(encoding='utf-8')
    # Replace embedded JSON by crude string splice: locate "const PAGE = " line.
    marker = "const PAGE = "
    i = html.find(marker)
    if i < 0:
        raise SystemExit("Template missing PAGE marker")
    j = html.find(";", i)
    if j < 0:
        raise SystemExit("Template missing PAGE terminator")
    html2 = html[:i+len(marker)] + json.dumps(page, ensure_ascii=False) + html[j:]
    Path(args.out).write_text(html2, encoding='utf-8')
    print("[OK]", args.out)

if __name__ == '__main__':
    main()
