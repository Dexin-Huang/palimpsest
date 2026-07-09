"""Preview harness: render the preprocessing line + lassos for fast eyeballing.

``palimpsest factory preview --doc-id X --pages f001r,f002v`` writes one PNG
per page: a strip of every preprocessing stage side by side, ending with the
segment overlay (polygon lassos color-coded by kind, route stamped in the
corner). Thirty seconds of looking beats an hour of threshold archaeology —
run it on a few pages of any new corpus before spending tokens.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import artifact_path

DEFAULT_OUT_DIR = PROJECT_ROOT / "tmp" / "preview"
STAGES = ("page_image", "page_image_framed", "page_image_unmarked", "page_image_clean")
KIND_COLORS = {
    "main_text": (60, 160, 60),
    "block": (200, 120, 40),
    "marginalia": (60, 60, 220),
}
PANEL_HEIGHT = 900


def build(
    doc_id: str,
    page_ids: list[str],
    *,
    library_root: Path = LIBRARY_ROOT,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> list[Path]:
    out_dir = out_dir / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for page_id in page_ids:
        panels = []
        for kind in STAGES:
            path = artifact_path(doc_id, kind, page_id, library_root)
            if not path.exists():
                continue
            image = cv2.imread(str(path))
            if image is None:
                continue
            if kind == "page_image_clean":
                image = _overlay(image, doc_id, page_id, library_root)
            panels.append(_labeled_panel(image, kind))
        if not panels:
            continue
        out_path = out_dir / f"{page_id}.png"
        cv2.imwrite(str(out_path), np.hstack(panels))
        written.append(out_path)
    return written


def _overlay(image: np.ndarray, doc_id: str, page_id: str, library_root: Path) -> np.ndarray:
    regions_path = artifact_path(doc_id, "page_regions", page_id, library_root)
    if not regions_path.exists():
        return image
    plan = read_json(regions_path)
    viz = image.copy()
    thickness = max(2, image.shape[0] // 900)
    for region in plan.get("regions", []):
        x, y, bw, bh = region["bbox"]
        color = KIND_COLORS.get(region["kind"], (128, 128, 128))
        cv2.rectangle(viz, (x, y), (x + bw, y + bh), color, thickness)
        cv2.putText(viz, f"{region['region_id']} {region['kind']}",
                    (x, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    image.shape[0] / 2200, color, thickness)
    cv2.putText(viz, f"route: {plan.get('route', '?')}",
                (20, image.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX,
                image.shape[0] / 1400, (30, 30, 200), thickness + 1)
    return viz


def _labeled_panel(image: np.ndarray, label: str) -> np.ndarray:
    scale = PANEL_HEIGHT / image.shape[0]
    panel = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bar = np.full((36, panel.shape[1], 3), 24, np.uint8)
    cv2.putText(bar, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (220, 220, 220), 1)
    return np.vstack([bar, panel])
