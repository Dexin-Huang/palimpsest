"""Three segmentation fixes, judged on one page's overlay (see NOTES).

- split: cells taller than ~1.5 glyphs cut at their interior ink valley
- recover: column gaps a glyph tall with real ink inside become cells
- ghosts: cells with almost no ink (margin stains) are dropped

Run: .venv python experiments/char_inventory/refine.py  -> before/after
overlays + side-by-side HTML for page_0001.
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


inv = load("inv", HERE / "candidate.py")
glyphs = load("glyphs_prod", HERE.parents[1] / "palimpsest" / "factory" / "glyphs.py")
m2 = load("m2c", HERE.parent / "m2_exemplars" / "candidate.py")


def page_cells_with_mask(image):
    mask = glyphs.binarize(image)
    rough = glyphs.ink_blobs(mask)
    pitch = glyphs._column_pitch(mask)
    glyph_h = (
        pitch * glyphs._GLYPH_OF_PITCH if pitch else glyphs._main_glyph_height(rough)
    )
    fuse = max(3, int(glyph_h * 0.15))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((fuse, fuse), np.uint8))
    blobs = glyphs.ink_blobs(closed)
    main = [b for b in blobs if b.h >= glyph_h * glyphs._SMALL_GLYPH_FRAC]
    bands = glyphs.column_bands(closed, glyph_h)
    columns = [c for c in (glyphs._band_cells(main, b, glyph_h) for b in bands) if c]
    columns.sort(key=lambda col: -max(c.x1 for c in col))
    return columns, glyph_h, closed


def ink_area(closed, cell) -> int:
    return int((closed[cell.y0:cell.y1, cell.x0:cell.x1] > 0).sum())


def split_tall(cell, closed, glyph_h):
    """Recursive valley split for cells that swallowed a neighbor."""
    if cell.h <= glyph_h * 1.7:
        return [cell]
    region = closed[cell.y0:cell.y1, cell.x0:cell.x1]
    rows = region.sum(axis=1).astype(np.float64)
    margin = max(2, int(glyph_h * 0.42))
    if len(rows) <= 2 * margin:
        return [cell]
    interior = rows[margin:-margin]
    cut = margin + int(np.argmin(interior))
    top_peak = rows[:cut].max()
    bottom_peak = rows[cut:].max()
    if rows[cut] > 0.18 * min(top_peak, bottom_peak):
        return [cell]
    upper = glyphs.Cell(cell.x0, cell.y0, cell.x1, cell.y0 + cut)
    lower = glyphs.Cell(cell.x0, cell.y0 + cut, cell.x1, cell.y1)
    return split_tall(upper, closed, glyph_h) + split_tall(lower, closed, glyph_h)


def recover_gaps(column, closed, glyph_h):
    """A glyph-tall gap between consecutive cells with real ink inside is a
    missed character."""
    if len(column) < 2:
        return column
    x0 = min(c.x0 for c in column)
    x1 = max(c.x1 for c in column)
    recovered = []
    for prev, nxt in zip(column, column[1:]):
        gap = nxt.y0 - prev.y1
        if gap < glyph_h * 0.7:
            continue
        region = closed[prev.y1:nxt.y0, x0:x1]
        if region.size == 0:
            continue
        density = (region > 0).sum() / region.size
        if density < 0.10:
            continue
        ys, xs = np.nonzero(region > 0)
        cell = glyphs.Cell(
            x0 + int(xs.min()), prev.y1 + int(ys.min()),
            x0 + int(xs.max()) + 1, prev.y1 + int(ys.max()) + 1)
        if cell.h >= glyph_h * 0.45 and (cell.x1 - cell.x0) >= glyph_h * 0.35:
            recovered.append(cell)
    return sorted(column + recovered, key=lambda c: c.y0)


def refine(columns, closed, glyph_h):
    stats = {"ghosts": 0, "splits": 0, "recovered": 0}
    floor = 0.03 * glyph_h * glyph_h
    refined = []
    for column in columns:
        solid = [c for c in column if ink_area(closed, c) >= floor]
        stats["ghosts"] += len(column) - len(solid)
        split = []
        for cell in solid:
            pieces = split_tall(cell, closed, glyph_h)
            stats["splits"] += len(pieces) - 1
            split.extend(pieces)
        before = len(split)
        column_cells = recover_gaps(sorted(split, key=lambda c: c.y0),
                                    closed, glyph_h)
        stats["recovered"] += len(column_cells) - before
        refined.append(column_cells)
    return refined, stats


def overlay(image, columns, closed, glyph_h):
    vis = image.copy()
    kept = junked = 0
    for column in columns:
        for cell in column:
            ink, _, junk = m2.clean_crop(image, cell.bbox())
            x, y, w, h = cell.bbox()
            if junk:
                junked += 1
                cv2.rectangle(vis, (x, y), (x + w, y + h), (60, 60, 230), 2)
            else:
                kept += 1
                cv2.rectangle(vis, (x, y), (x + w, y + h), (60, 170, 60), 2)
    return vis, kept, junked


def main() -> None:
    image = cv2.imread(str(inv.DOC / "page_image_clean" / f"{PID}.jpg"))
    columns, glyph_h, closed = page_cells_with_mask(image)

    before_vis, before_kept, before_junk = overlay(image, columns, closed, glyph_h)
    refined, stats = refine(columns, closed, glyph_h)
    after_vis, after_kept, after_junk = overlay(image, refined, closed, glyph_h)

    cv2.imwrite(str(OUT / f"{PID}_before.png"), before_vis)
    cv2.imwrite(str(OUT / f"{PID}_after.png"), after_vis)
    h, w = after_vis.shape[:2]
    cv2.imwrite(str(OUT / f"{PID}_after_right.png"),
                cv2.resize(after_vis[:, w // 2:], None, fx=0.85, fy=0.85))

    (OUT / "refine_report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{PID} — segmentation before/after</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;color:#2b2620;
margin:1.5rem}} .wrap{{display:flex;gap:12px}} .wrap div{{flex:1}}
img{{width:100%;border:1px solid #d9d2c4}} h2{{font-size:1rem;
font-family:Consolas,monospace}}</style></head><body>
<h1>{PID} — character locations, before / after refinement</h1>
<p>before: {before_kept} kept + {before_junk} junked &nbsp;·&nbsp;
after: <b>{after_kept} kept</b> + {after_junk} junked &nbsp;·&nbsp;
splits {stats["splits"]}, recovered {stats["recovered"]},
ghosts dropped {stats["ghosts"]} &nbsp;·&nbsp;
transcription says this page has 607 characters</p>
<div class="wrap"><div><h2>before</h2><img src="{PID}_before.png"></div>
<div><h2>after</h2><img src="{PID}_after.png"></div></div>
</body></html>""", encoding="utf-8")

    print(f"before: kept={before_kept} junked={before_junk}")
    print(f"after:  kept={after_kept} junked={after_junk} "
          f"(splits={stats['splits']} recovered={stats['recovered']} "
          f"ghosts={stats['ghosts']})")
    print(OUT / "refine_report.html")


if __name__ == "__main__":
    main()
