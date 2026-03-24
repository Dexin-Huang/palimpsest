#!/usr/bin/env python3
"""Render a searchable facsimile PDF from a Rescript page JSON.

Strategy:
- Draw scan as page background.
- Draw invisible (or near-invisible) text overlays positioned by bbox_norm.

Note:
- ReportLab can set text rendering mode to invisible in a TextObject (PDF text render mode 3).
- Exact font matching is out-of-scope for v1; use a neutral font and accept slight drift.

Usage:
  python scripts/render_pdf_overlay.py \
    --page projects/pasajeros_a_indias/pages/<page>.page.json \
    --out  projects/pasajeros_a_indias/exports/pdf/<page>.pdf \
    --layer es_normalized
"""

import argparse
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import portrait
from reportlab.lib.utils import ImageReader

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--layer', default='es_normalized')
    ap.add_argument('--render_mode', type=int, default=3, help='PDF text render mode (3=invisible)')
    args = ap.parse_args()

    page = json.loads(Path(args.page).read_text(encoding='utf-8'))
    img_path = Path(page['image']['path'])
    # Resolve relative to repo root (assuming script run from repo root)
    if not img_path.exists():
        img_path = Path('.') / img_path

    iw = page['image']['width_px']
    ih = page['image']['height_px']
    pdf_w, pdf_h = iw, ih  # 1 px -> 1 pt for simplicity (good enough for overlay)
    c = canvas.Canvas(args.out, pagesize=(pdf_w, pdf_h))

    # background scan
    c.drawImage(ImageReader(str(img_path)), 0, 0, width=pdf_w, height=pdf_h)

    # overlays
    for z in page.get('zones', []):
        txt = (z.get('text') or {}).get(args.layer, '')
        if not txt:
            continue
        x, y, w, h = z['bbox_norm']
        X = x * pdf_w
        Y_top = (1.0 - y) * pdf_h
        H = h * pdf_h
        # PDF origin is bottom-left; convert top-left y to bottom-left
        Y = Y_top - H
        W = w * pdf_w

        t = c.beginText()
        # Place near top-left of bbox; tweak baseline later if needed
        t.setTextOrigin(X + 2, Y + H - 12)
        t.setFont('Helvetica', 10)
        try:
            t.setTextRenderMode(args.render_mode)  # invisible by default
        except Exception:
            pass
        # naive wrapping: one line per zone
        t.textLine(txt.replace('\n', ' '))
        c.drawText(t)

    c.showPage()
    c.save()

if __name__ == '__main__':
    main()
