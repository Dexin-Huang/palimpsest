"""Lassos instead of boxes: per-character ink outlines (pure code).

The box is the shadow of a mask we already compute — clean_crop keeps
exactly the ink components belonging to the character. This script stores
that outline as a simplified polygon per character and renders page_0001
with lassos beside the box overlay, so the upgrade is judged by eye on
the same fixture.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
PID = "page_0001"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


refine_mod = load("refine_mod", HERE / "refine.py")
glyphs = refine_mod.glyphs


def cell_lasso(closed: np.ndarray, cell) -> list[list[int]] | None:
    """Ink outline for one cell: components of the page mask whose centroid
    falls inside the cell, contoured and simplified. Absolute coordinates."""
    pad = max(3, int(cell.h * 0.08))
    x0, y0 = max(0, cell.x0 - pad), max(0, cell.y0 - pad)
    region = closed[y0:cell.y1 + pad, x0:cell.x1 + pad]
    if region.size == 0:
        return None
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(region, 8)
    keep = np.zeros(count, bool)
    for i in range(1, count):
        cx, cy = centroids[i]
        keep[i] = (cell.x0 - x0 - 2 <= cx <= cell.x1 - x0 + 2
                   and cell.y0 - y0 - 2 <= cy <= cell.y1 - y0 + 2
                   and stats[i, cv2.CC_STAT_AREA] >= 6)
    mask = np.where(keep[labels], np.uint8(255), np.uint8(0))
    if mask.sum() == 0:
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for contour in contours:
        if cv2.contourArea(contour) < 12:
            continue
        epsilon = 0.012 * cv2.arcLength(contour, True)
        poly = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        polygons.append((poly + [x0, y0]).tolist())
    return polygons or None


def main() -> None:
    image = cv2.imread(str(refine_mod.inv.DOC / "page_image_clean" / f"{PID}.jpg"))
    columns, glyph_h, closed = refine_mod.page_cells_with_mask(image)
    refined, _ = refine_mod.refine(columns, closed, glyph_h)

    vis = image.copy()
    index = []
    drawn = 0
    for col_index, column in enumerate(refined):
        for pos, cell in enumerate(column):
            ink, _, junk = refine_mod.m2.clean_crop(image, cell.bbox())
            if junk:
                continue
            polygons = cell_lasso(closed, cell)
            if not polygons:
                continue
            drawn += 1
            index.append({"page": PID, "column": col_index, "position": pos,
                          "bbox": cell.bbox(), "lasso": polygons})
            for poly in polygons:
                cv2.polylines(vis, [np.array(poly, np.int32)], True,
                              (30, 140, 220), 2)

    (OUT / f"{PID}_lassos.json").write_text(
        json.dumps(index), encoding="utf-8")
    cv2.imwrite(str(OUT / f"{PID}_lasso.png"), vis)
    h, w = vis.shape[:2]
    cv2.imwrite(str(OUT / f"{PID}_lasso_right.png"),
                cv2.resize(vis[:, w // 2:], None, fx=0.85, fy=0.85))

    (OUT / "lasso_report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{PID} — boxes vs lassos</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;color:#2b2620;
margin:1.5rem}} .wrap{{display:flex;gap:12px}} .wrap div{{flex:1}}
img{{width:100%;border:1px solid #d9d2c4}} h2{{font-size:1rem;
font-family:Consolas,monospace}}</style></head><body>
<h1>{PID} — boxes vs lassos ({drawn} characters outlined)</h1>
<p>Same refined cells; left the bounding boxes, right the actual ink
outlines those cells contain. The lasso is what the exemplar library,
the hand font, and tap-to-ink actually want — the box was its shadow.</p>
<div class="wrap"><div><h2>boxes</h2><img src="{PID}_after.png"></div>
<div><h2>lassos</h2><img src="{PID}_lasso.png"></div></div>
</body></html>""", encoding="utf-8")
    print(f"lassos drawn: {drawn}")
    print(OUT / "lasso_report.html")


if __name__ == "__main__":
    main()
