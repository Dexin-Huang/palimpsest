"""Analysis-by-synthesis v0: the scribe rewrites their own page.

For every separated cell, find its cluster (unsupervised, v2 features)
and re-render the cell from the OTHER instances of the same cluster —
the scribe's own ink, written elsewhere on the page. Compose the
synthetic page and compare against the real ink, cell by cell.

High match = segmentation + clustering + placement all agree with the
photograph. Low match = a localized error in one of the three, visible
as a colored box. No fonts, no models, no labels — the scribe is the
generator.
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
DOC = HERE.parents[1] / "library" / "gallica_pelliot_chinois_3477"
CLUSTER_THRESHOLD = 0.62


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


refine_mod = load("refine_mod", HERE.parent / "char_inventory" / "refine.py")
features = load("features", HERE.parent / "separation2" / "features.py")
cluster_mod = load("cluster_mod", HERE.parent / "char_inventory" / "cluster.py")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(DOC / "page_image_clean" / f"{PID}.jpg"))
    columns, glyph_h, closed = refine_mod.page_cells_with_mask(image)
    refined, _ = refine_mod.refine(columns, closed, glyph_h)

    cells, masks, feats = [], [], []
    for column in refined:
        for cell in column:
            ink, _, junk = refine_mod.m2.clean_crop(image, cell.bbox())
            if junk or ink is None:
                continue
            f = features.feature_grad(np.where(ink, np.uint8(0), np.uint8(255)))
            if f is None:
                continue
            cells.append(cell)
            masks.append(ink)
            feats.append(f)
    matrix = np.stack(feats).astype(np.float32)
    labels = cluster_mod.cluster(matrix, CLUSTER_THRESHOLD)
    print(f"{len(cells)} cells, {len(set(labels))} clusters")

    height, width = image.shape[:2]
    synthetic = np.full((height, width), 255, np.uint8)
    scores = []
    for i, cell in enumerate(cells):
        peers = [j for j in np.where(labels == labels[i])[0] if j != i]
        if peers:
            sims = matrix[peers] @ matrix[i]
            donor = peers[int(np.argmax(sims))]
            source, self_written = masks[donor], False
        else:
            source, self_written = masks[i], True  # singleton: only witness
        x, y, w, h = cell.bbox()
        # place the donor ink into this cell's box
        sy, sx = np.nonzero(source)
        if sy.size == 0:
            continue
        tight = source[sy.min():sy.max() + 1, sx.min():sx.max() + 1]
        resized = cv2.resize((tight > 0).astype(np.uint8) * 255, (w, h),
                             interpolation=cv2.INTER_AREA) > 127
        region = synthetic[y:y + h, x:x + w]
        region[resized[:region.shape[0], :region.shape[1]]] = 0
        # score: does the rewritten character match the real ink?
        real = closed[y:y + h, x:x + w] > 0
        synth = resized[:real.shape[0], :real.shape[1]]
        union = (real | synth).sum()
        iou = float((real & synth).sum() / union) if union else 0.0
        scores.append((cell, iou, self_written))

    peer_scores = [s for _, s, self_w in scores if not self_w]
    print(f"peer-rewritten cells: {len(peer_scores)} "
          f"(singletons: {len(scores) - len(peer_scores)})")
    print(f"rewrite match IoU: median {np.median(peer_scores):.3f}, "
          f"p10 {np.percentile(peer_scores, 10):.3f}")

    verdict = image.copy()
    for cell, iou, self_w in scores:
        x, y, w, h = cell.bbox()
        if self_w:
            color = (180, 180, 180)
        elif iou >= 0.45:
            color = (60, 170, 60)
        elif iou >= 0.3:
            color = (30, 150, 230)
        else:
            color = (50, 50, 230)
        cv2.rectangle(verdict, (x, y), (x + w, y + h), color, 2)

    cv2.imwrite(str(OUT / f"{PID}_synthetic.png"), synthetic)
    cv2.imwrite(str(OUT / f"{PID}_verdict.png"), verdict)
    half = width // 2
    strip = np.hstack([
        cv2.resize(image[:, half:], None, fx=0.5, fy=0.5),
        cv2.resize(cv2.cvtColor(synthetic[:, half:], cv2.COLOR_GRAY2BGR),
                   None, fx=0.5, fy=0.5),
    ])
    cv2.imwrite(str(OUT / f"{PID}_sidebyside.png"), strip)
    (OUT / "report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{PID} — the scribe rewrites</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;
color:#2b2620;margin:1.5rem;max-width:72rem}}
img{{width:100%;border:1px solid #d9d2c4;margin-bottom:1rem}}
h2{{font-size:.95rem;font-family:Consolas,monospace}}</style></head><body>
<h1>Analysis-by-synthesis v0 — {PID} rewritten from its own ink</h1>
<p>{len(peer_scores)} characters rewritten by the scribe's other
instances of the same (clustered) character; median rewrite IoU
{np.median(peer_scores):.3f}. Verdict overlay: green = the rewrite
matches the ink; amber = marginal; red = the synthesis disagrees —
a localized segmentation/cluster error; gray = singleton (no peer to
rewrite from).</p>
<h2>real page (right half) | synthetic rewrite</h2>
<img src="{PID}_sidebyside.png">
<h2>verdict overlay</h2>
<img src="{PID}_verdict.png">
</body></html>""", encoding="utf-8")
    print(OUT / "report.html")


if __name__ == "__main__":
    main()
