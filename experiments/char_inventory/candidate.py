"""Segmentation-first character inventory: locations without labels.

Pure CV. Runs the existing geometry (binarize -> blobs -> columns ->
cells) and emits EVERY cell as an unlabeled character crop, post-processed
and junk-gated. No transcription involved: this is the full inventory of
"a character sits here" facts, decoupled from the noisy question of which
character each one is. Labeling then becomes a separate, swappable step
(alignment / shape clustering / per-crop model call).
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
DOC = HERE.parents[1] / "library" / "gallica_pelliot_chinois_3477"
PAGES = ("page_0000", "page_0001", "page_0002")
TILE = 48


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def page_cells(glyphs, image: np.ndarray):
    """The geometry pipeline up to cells — no transcription anywhere."""
    mask = glyphs.binarize(image)
    rough = glyphs.ink_blobs(mask)
    if not rough:
        return [], 0.0
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
    return columns, glyph_h


def main() -> None:
    glyphs = load("glyphs_prod", HERE.parents[1] / "palimpsest" / "factory" / "glyphs.py")
    m2 = load("candidate", HERE.parent / "m2_exemplars" / "candidate.py")
    (OUT / "crops").mkdir(parents=True, exist_ok=True)

    index = []
    tiles = []
    junked = {}
    for pid in PAGES:
        image = cv2.imread(str(DOC / "page_image_clean" / f"{pid}.jpg"))
        columns, glyph_h = page_cells(glyphs, image)
        for col_index, column in enumerate(columns):
            for pos, cell in enumerate(column):
                bbox = cell.bbox()
                ink, gray, junk = m2.clean_crop(image, bbox)
                if junk:
                    junked[junk] = junked.get(junk, 0) + 1
                    continue
                crop_id = f"{pid}_c{col_index:02d}_p{pos:02d}"
                cv2.imwrite(str(OUT / "crops" / f"{crop_id}.png"), 255 - ink)
                index.append({"crop_id": crop_id, "page": pid,
                              "column": col_index, "position": pos,
                              "bbox": bbox})
                tile = cv2.resize((255 - ink), (TILE, TILE),
                                  interpolation=cv2.INTER_AREA)
                tiles.append(tile)

    (OUT / "index.json").write_text(
        json.dumps(index, indent=1), encoding="utf-8")

    width = 34
    rows = []
    for start in range(0, len(tiles), width):
        row = tiles[start:start + width]
        row += [np.full((TILE, TILE), 200, np.uint8)] * (width - len(row))
        rows.append(np.hstack([np.pad(t, 1, constant_values=140) for t in row]))
    sheet = np.vstack(rows)
    cv2.imwrite(str(OUT / "inventory_sheet.png"), sheet)

    print(f"character locations: {len(index)} crops "
          f"({', '.join(f'{k}={v}' for k, v in sorted(junked.items()))} junked)")
    per_page = {}
    for entry in index:
        per_page[entry["page"]] = per_page.get(entry["page"], 0) + 1
    for pid, count in sorted(per_page.items()):
        print(f"  {pid}: {count}")
    print(OUT / "inventory_sheet.png")


if __name__ == "__main__":
    main()
