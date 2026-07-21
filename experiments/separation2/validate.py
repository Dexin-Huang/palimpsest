"""Validation: baseline vs integrated candidate on the frozen test set.

Same 28 folios, same metrics, two arms. Health per folio =
size_consistency x (1 - junk_share-of-found) x pitch factor; a folio the
candidate declares blank scores 1.0 for the candidate (correct separation
of a blank page is zero cells) and is reported separately so the
comparison stays honest. Emits per-corpus rollup, CSV, and overlays for
the largest deltas.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
MANIFEST = HERE.parent / "testset" / "manifest.json"
FOLIOS = HERE.parent / "testset" / "folios"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sweep_mod = load("sweep_mod", HERE.parent / "separation_sweep" / "candidate.py")
sep2 = load("sep2", HERE / "separate.py")


def corpus_of(doc: str) -> str:
    for key in ("gallica", "idp", "borg_cin", "estr_or"):
        if key in doc:
            return key
    return "other"


def health(metrics: dict) -> float:
    if metrics.get("blank"):
        return 1.0
    found = metrics["kept"] + metrics.get("junked", 0) + metrics.get("prior_killed", 0)
    junk_share = (metrics.get("junked", 0) + metrics.get("prior_killed", 0)) / max(1, found)
    pitch = 1.0 if metrics.get("pitch_found") else 0.6
    return metrics["size_consistency"] * (1 - junk_share) * pitch


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["folios"]:
        path = FOLIOS / f"{entry['doc']}__{entry['folio']}.jpg"
        image = cv2.imread(str(path))
        if image is None:
            continue
        scale = 3200 / max(image.shape[:2])
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale)

        base = sweep_mod.separate(image)
        base_h = (base["size_consistency"]
                  * (1 - base["junk_rate"])
                  * (1.0 if base["pitch_found"] else 0.6))
        cand = sep2.separate(image)
        cand_h = health(cand)
        rows.append({
            "corpus": corpus_of(entry["doc"]), "doc": entry["doc"],
            "folio": entry["folio"],
            "base_health": round(base_h, 3), "cand_health": round(cand_h, 3),
            "delta": round(cand_h - base_h, 3),
            "base_kept": base["kept"], "cand_kept": cand["kept"],
            "blank": cand.get("blank", False),
            "prior_killed": cand.get("prior_killed", 0),
            "_image": image, "_cand": cand,
        })
        print(f"{entry['doc']}/{entry['folio']}: base {base_h:.2f} -> "
              f"cand {cand_h:.2f}  (kept {base['kept']}->{cand['kept']}"
              f"{', BLANK' if cand.get('blank') else ''})")

    with (OUT / "validation.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [k for k in rows[0] if not k.startswith("_")]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print("\nper-corpus rollup (median health, base -> candidate):")
    summary = []
    for corpus in sorted({r["corpus"] for r in rows}):
        sub = [r for r in rows if r["corpus"] == corpus]
        b = float(np.median([r["base_health"] for r in sub]))
        c = float(np.median([r["cand_health"] for r in sub]))
        summary.append((corpus, len(sub), b, c))
        print(f"  {corpus:10s} n={len(sub):2d}  {b:.2f} -> {c:.2f}")
    b_all = float(np.median([r["base_health"] for r in rows]))
    c_all = float(np.median([r["cand_health"] for r in rows]))
    print(f"  {'ALL':10s} n={len(rows):2d}  {b_all:.2f} -> {c_all:.2f}")

    rows.sort(key=lambda r: -abs(r["delta"]))
    gallery = []
    for row in rows[:4]:
        cand = row["_cand"]
        vis = (cand["_page"].copy() if not cand.get("blank")
               else row["_image"].copy())
        for cell in cand.get("cells", []):
            x, y, w, h = cell.bbox()
            cv2.rectangle(vis, (x, y), (x + w, y + h), (60, 170, 60), 2)
        name = f"delta_{row['doc']}_{row['folio']}.jpg"
        cv2.imwrite(str(OUT / name), vis, [cv2.IMWRITE_JPEG_QUALITY, 82])
        gallery.append((row, name))

    items = "\n".join(
        f"<h2>{r['doc']}/{r['folio']} — {r['base_health']} → "
        f"<b>{r['cand_health']}</b> (Δ{r['delta']:+}, kept {r['cand_kept']}"
        f"{', declared BLANK' if r['blank'] else ''})</h2>"
        f'<img src="{n}" loading="lazy">' for r, n in gallery)
    table = "\n".join(
        f"<tr><td>{c}</td><td>{n}</td><td>{b:.2f}</td><td><b>{v:.2f}</b></td></tr>"
        for c, n, b, v in summary)
    (OUT / "validation_report.html").write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>separation validation</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;
color:#2b2620;margin:1.5rem;max-width:70rem}}
img{{width:100%;border:1px solid #d9d2c4;margin-bottom:1rem}}
h2{{font-size:.95rem;font-family:Consolas,monospace}}
table{{border-collapse:collapse}}td,th{{border-bottom:1px solid #d9d2c4;
padding:.3rem .8rem}}</style></head><body>
<h1>Frozen test set: baseline vs integrated candidate</h1>
<p>{len(rows)} folios, {len(summary)} corpora. Overall median health
{b_all:.2f} → <b>{c_all:.2f}</b>.</p>
<table><tr><th>corpus</th><th>n</th><th>baseline</th><th>candidate</th></tr>
{table}</table>
<h1>Largest deltas — the audit queue</h1>
{items}</body></html>""", encoding="utf-8")
    print(OUT / "validation_report.html")


if __name__ == "__main__":
    main()
