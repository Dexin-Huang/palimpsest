"""Protocol cycle 1: champion separation swept across the zh folio pool.

Stratified sample from every Chinese document in the library (raw images —
deliberately: the metrics must tell us where imaging prep matters). Pure
code. Emits per-folio metrics, a ranked CSV, and overlays for the worst
folios — the protocol's visual-audit queue.

Unsupervised health metrics (no transcription needed):
- kept cells + junk rate
- pitch_found: did the column-pitch autocorrelation lock on
- size_consistency: fraction of cells within [0.55, 1.6] x glyph height
- columns detected
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
LIB = HERE.parents[1] / "library"
PER_DOC = 6
ZH_KEYS = ("chinois", "idp", "borg_cin", "estr_or")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


refine_mod = load("refine_mod",
                  HERE.parent / "char_inventory" / "refine.py")
glyphs = refine_mod.glyphs


def separate(image: np.ndarray) -> dict:
    columns, glyph_h, closed = refine_mod.page_cells_with_mask(image)
    pitch = glyphs._column_pitch(glyphs.binarize(image))
    refined, _ = refine_mod.refine(columns, closed, glyph_h)
    kept = junked = 0
    heights = []
    cells_out = []
    for column in refined:
        for cell in column:
            _, _, junk = refine_mod.m2.clean_crop(image, cell.bbox())
            if junk:
                junked += 1
            else:
                kept += 1
                heights.append(cell.h)
                cells_out.append(cell)
    heights = np.array(heights) if heights else np.array([0.0])
    consistent = float(((heights >= 0.55 * glyph_h)
                        & (heights <= 1.6 * glyph_h)).mean())
    return {"kept": kept, "junked": junked,
            "junk_rate": junked / max(1, kept + junked),
            "glyph_h": round(float(glyph_h), 1),
            "pitch_found": pitch is not None,
            "size_consistency": round(consistent, 3),
            "columns": len(refined),
            "_cells": cells_out}


def overlay(image, cells) -> np.ndarray:
    vis = image.copy()
    for cell in cells:
        x, y, w, h = cell.bbox()
        cv2.rectangle(vis, (x, y), (x + w, y + h), (60, 170, 60), 2)
    return vis


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for doc in sorted(LIB.iterdir()):
        if not any(k in doc.name for k in ZH_KEYS):
            continue
        images = sorted((doc / "images").glob("*.jpg")) if (doc / "images").exists() else []
        if not images:
            continue
        step = max(1, len(images) // PER_DOC)
        for path in images[::step][:PER_DOC]:
            image = cv2.imread(str(path))
            if image is None:
                continue
            scale = 3200 / max(image.shape[:2])
            if scale < 1:
                image = cv2.resize(image, None, fx=scale, fy=scale)
            try:
                metrics = separate(image)
            except Exception as error:  # a folio must never kill the sweep
                rows.append({"doc": doc.name, "folio": path.stem,
                             "error": str(error)[:60]})
                continue
            health = (metrics["size_consistency"]
                      * (1 - metrics["junk_rate"])
                      * (1.0 if metrics["pitch_found"] else 0.6))
            rows.append({"doc": doc.name, "folio": path.stem,
                         "kept": metrics["kept"],
                         "junk_rate": round(metrics["junk_rate"], 3),
                         "glyph_h": metrics["glyph_h"],
                         "pitch": int(metrics["pitch_found"]),
                         "size_consistency": metrics["size_consistency"],
                         "columns": metrics["columns"],
                         "health": round(health, 3),
                         "_image": image, "_cells": metrics["_cells"]})
            print(f"{doc.name}/{path.stem}: kept={metrics['kept']} "
                  f"junk={metrics['junk_rate']:.0%} "
                  f"consist={metrics['size_consistency']:.2f} "
                  f"health={health:.2f}")

    scored = [r for r in rows if "health" in r]
    scored.sort(key=lambda r: r["health"])
    with (OUT / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [k for k in scored[0] if not k.startswith("_")]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(scored)

    gallery = []
    for tag, sample in (("worst", scored[:4]), ("best", scored[-2:])):
        for row in sample:
            name = f"{tag}_{row['doc']}_{row['folio']}.jpg"
            cv2.imwrite(str(OUT / name),
                        overlay(row["_image"], row["_cells"]),
                        [cv2.IMWRITE_JPEG_QUALITY, 82])
            gallery.append((tag, row, name))

    items = "\n".join(
        f"<h2>{tag} · {row['doc']}/{row['folio']} — health {row['health']}, "
        f"kept {row['kept']}, junk {row['junk_rate']:.0%}, "
        f"consistency {row['size_consistency']}</h2>"
        f'<img src="{name}" loading="lazy">'
        for tag, row, name in gallery)
    (OUT / "sweep_report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>separation sweep — cycle 1</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;
color:#2b2620;margin:1.5rem;max-width:70rem}}
img{{width:100%;border:1px solid #d9d2c4;margin-bottom:1rem}}
h2{{font-size:.95rem;font-family:Consolas,monospace}}</style></head><body>
<h1>Separation sweep — protocol cycle 1 ({len(scored)} folios,
{len(rows) - len(scored)} errors)</h1>
<p>Visual-audit queue: worst folios first, then the best for contrast.
Full table in metrics.csv.</p>
{items}</body></html>""", encoding="utf-8")

    print(f"\nswept {len(scored)} folios ({len(rows) - len(scored)} errors)")
    health = [r["health"] for r in scored]
    print(f"health: median {np.median(health):.2f}, "
          f"worst {min(health):.2f}, best {max(health):.2f}")
    print(OUT / "sweep_report.html")


if __name__ == "__main__":
    main()
