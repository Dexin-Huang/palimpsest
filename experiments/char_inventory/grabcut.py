"""Lightweight lasso refinement: OpenCV GrabCut, zero new dependencies.

GrabCut is the classical box-prompted segmenter (graph cut on color) —
SAM's ancestor, already inside OpenCV. Each refined cell's box becomes
the prompt; GrabCut returns a foreground mask from the *color* image, so
faint strokes that global thresholding drops can survive. Judged against
the incumbent Otsu-component lassos on the same page overlay.
"""

from __future__ import annotations

import importlib.util
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
lasso_mod = load("lasso_mod", HERE / "lasso.py")


def grabcut_mask(image: np.ndarray, cell) -> np.ndarray | None:
    pad = max(6, int(cell.h * 0.18))
    x0, y0 = max(0, cell.x0 - pad), max(0, cell.y0 - pad)
    crop = image[y0:cell.y1 + pad, x0:cell.x1 + pad]
    if crop.shape[0] < 12 or crop.shape[1] < 12:
        return None
    mask = np.zeros(crop.shape[:2], np.uint8)
    rect = (cell.x0 - x0, cell.y0 - y0,
            cell.x1 - cell.x0, cell.y1 - cell.y0)
    try:
        cv2.grabCut(crop, mask, rect, np.zeros((1, 65), np.float64),
                    np.zeros((1, 65), np.float64), 3, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0)
    return fg.astype(np.uint8), (x0, y0)


def main() -> None:
    image = cv2.imread(str(refine_mod.inv.DOC / "page_image_clean" / f"{PID}.jpg"))
    columns, glyph_h, closed = refine_mod.page_cells_with_mask(image)
    refined, _ = refine_mod.refine(columns, closed, glyph_h)

    vis = image.copy()
    done = changed = failed = 0
    for column in refined:
        for cell in column:
            ink, _, junk = refine_mod.m2.clean_crop(image, cell.bbox())
            if junk:
                continue
            result = grabcut_mask(image, cell)
            if result is None:
                failed += 1
                continue
            fg, (x0, y0) = result
            done += 1
            # compare against the incumbent (thresholded page mask region)
            region = closed[y0:y0 + fg.shape[0], x0:x0 + fg.shape[1]]
            both = (fg > 0) | (region > 0)
            iou = ((fg > 0) & (region > 0)).sum() / max(1, both.sum())
            if iou < 0.75:
                changed += 1
            contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                if cv2.contourArea(contour) < 12:
                    continue
                epsilon = 0.012 * cv2.arcLength(contour, True)
                poly = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
                cv2.polylines(vis, [poly + [x0, y0]], True, (170, 60, 170), 2)

    cv2.imwrite(str(OUT / f"{PID}_grabcut.png"), vis)
    h, w = vis.shape[:2]
    cv2.imwrite(str(OUT / f"{PID}_grabcut_right.png"),
                cv2.resize(vis[:, w // 2:], None, fx=0.85, fy=0.85))
    (OUT / "grabcut_report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{PID} — Otsu lassos vs GrabCut</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;color:#2b2620;
margin:1.5rem}} .wrap{{display:flex;gap:12px}} .wrap div{{flex:1}}
img{{width:100%;border:1px solid #d9d2c4}} h2{{font-size:1rem;
font-family:Consolas,monospace}}</style></head><body>
<h1>{PID} — incumbent lassos vs GrabCut ({done} cells, {changed} materially
different, {failed} failed)</h1>
<div class="wrap"><div><h2>incumbent (Otsu components)</h2>
<img src="{PID}_lasso.png"></div>
<div><h2>challenger (GrabCut, box-prompted)</h2>
<img src="{PID}_grabcut.png"></div></div>
</body></html>""", encoding="utf-8")
    print(f"grabcut: {done} cells, materially different: {changed}, "
          f"failed: {failed}")
    print(OUT / "grabcut_report.html")


if __name__ == "__main__":
    main()
