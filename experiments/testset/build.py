"""The consistent test set: frozen, stratified, corpus-spanning.

Picks per zh document its median-health folio and (for larger documents)
its worst folio from the cycle-1 sweep, so the set spans both the typical
and the hard case of every corpus. Frozen by manifest — the images stay
in the library; the manifest pins exact files, and materialize() copies
them into place for any run. Every candidate is validated on exactly
this set, forever, until the manifest version bumps.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
LIB = HERE.parents[1] / "library"
SWEEP = HERE.parent / "separation_sweep" / "out" / "metrics.csv"
MANIFEST = HERE / "manifest.json"


def build_manifest() -> dict:
    rows = list(csv.DictReader(SWEEP.open(encoding="utf-8")))
    by_doc: dict[str, list[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["doc"], []).append(row)
    folios = []
    for doc, entries in sorted(by_doc.items()):
        entries.sort(key=lambda r: float(r["health"]))
        median = entries[len(entries) // 2]
        picks = {median["folio"]: median}
        if len(entries) >= 4:  # larger corpora also contribute their worst
            picks.setdefault(entries[0]["folio"], entries[0])
        for folio, row in picks.items():
            folios.append({
                "doc": doc, "folio": folio,
                "source": f"library/{doc}/images/{folio}.jpg",
                "cycle1_health": float(row["health"]),
            })
    return {"version": 1, "purpose":
            "consistent validation set for unsupervised character "
            "separation (SEPARATION.md); stratified median+worst per corpus",
            "folios": folios}


def materialize(manifest: dict) -> Path:
    target = HERE / "folios"
    target.mkdir(exist_ok=True)
    for entry in manifest["folios"]:
        dst = target / f"{entry['doc']}__{entry['folio']}.jpg"
        if not dst.exists():
            shutil.copy(HERE.parents[1] / entry["source"], dst)
    return target


if __name__ == "__main__":
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        print(f"manifest v{manifest['version']} already frozen "
              f"({len(manifest['folios'])} folios) — materializing only")
    else:
        manifest = build_manifest()
        MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"froze manifest v1: {len(manifest['folios'])} folios")
    path = materialize(manifest)
    docs = {e["doc"] for e in manifest["folios"]}
    print(f"{len(docs)} corpora represented -> {path}")
