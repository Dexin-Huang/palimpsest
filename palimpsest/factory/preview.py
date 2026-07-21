"""Preview + tune: look at the CV line without spending a token.

``factory preview`` renders EXISTING artifacts (one strip per page: every
preprocessing stage side by side, ending with the lasso overlay + route).

``factory tune`` computes the whole chain IN MEMORY — deframe, dewatermark,
flatten, and segment — from canonical ``page_image`` artifacts, renders the
same strips, and prints a routing table, optionally sanity-scored against a
reference transcription JSONL. No ledger writes, no network, no model calls:
this is the offline optimization loop for the lasso system.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory.config import LIBRARY_ROOT, PROJECT_ROOT
from palimpsest.factory.imaging import (
    attenuate_light_marks,
    encode_png,
    flatten_illumination,
    parchment_frame,
    remove_overlay_marks,
    to_gray,
    trim_gutter,
)
from palimpsest.factory.stations.segment import analyze
from palimpsest.factory.workspace.io import atomic_write_bytes, read_json
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
        atomic_write_bytes(out_path, encode_png(np.hstack(panels)))
        written.append(out_path)
    return written


def tune(
    doc_id: str,
    page_ids: list[str],
    *,
    library_root: Path = LIBRARY_ROOT,
    out_dir: Path = DEFAULT_OUT_DIR,
    reference: Path | None = None,
) -> list[dict]:
    """Compute the CV chain in memory for each page and render strips + stats."""
    out_dir = out_dir / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    reference_lengths = _reference_lengths(reference) if reference else {}

    rows = []
    for page_id in page_ids:
        source = _find_source_image(doc_id, page_id, library_root)
        original = cv2.imread(str(source))
        if original is None:
            raise ValueError(f"Unreadable image: {source}")

        x0, y0, x1, y1 = parchment_frame(to_gray(original))
        framed = original[y0:y1, x0:x1]
        gx0, gx1 = trim_gutter(to_gray(framed))
        framed = framed[:, gx0:gx1]
        unmarked = remove_overlay_marks(framed)
        clean = attenuate_light_marks(flatten_illumination(unmarked))
        plan = analyze(clean, {})

        strip = np.hstack(
            [
                _labeled_panel(original, "original"),
                _labeled_panel(framed, "deframe"),
                _labeled_panel(unmarked, "dewatermark"),
                _labeled_panel(_draw_plan(clean, plan), "flatten + lassos"),
            ]
        )
        atomic_write_bytes(out_dir / f"{page_id}.png", encode_png(strip))

        kinds = [r["kind"] for r in plan["regions"]]
        row = {
            "page_id": page_id,
            "route": plan["route"],
            "regions": len(plan["regions"]),
            "main": kinds.count("main_text"),
            "margin": kinds.count("marginalia"),
            "glyph": plan["glyph_height_px"],
            "lines": sum(r["est_lines"] for r in plan["regions"]),
        }
        if page_id in reference_lengths:
            row["ref_chars"] = reference_lengths[page_id]
            row["verdict"] = _verdict(plan, reference_lengths[page_id])
        rows.append(row)
    return rows


def _reference_lengths(path: Path) -> dict[str, int]:
    lengths = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            lengths[record["page_id"]] = len(record.get("text", ""))
    return lengths


def _verdict(plan: dict, ref_chars: int) -> str:
    """Weak sanity check: reference length versus the detected lassos."""
    if ref_chars > 800 and plan["route"] == "blank":
        return "MISSING-INK?"
    if ref_chars < 120 and plan["route"] != "blank" and plan["regions"]:
        return "over-detect?"
    return "ok"


def _find_source_image(doc_id: str, page_id: str, library_root: Path) -> Path:
    image = artifact_path(doc_id, "page_image", page_id, library_root)
    if not image.exists():
        raise FileNotFoundError(f"No page_image for {doc_id}/{page_id}")
    return image


def _draw_plan(
    image: np.ndarray, plan: dict, *, show_line_counts: bool = True
) -> np.ndarray:
    viz = image.copy()
    thickness = max(2, image.shape[0] // 900)
    for region in plan.get("regions", []):
        x, y, bw, bh = region["bbox"]
        color = KIND_COLORS.get(region["kind"], (128, 128, 128))
        cv2.rectangle(viz, (x, y), (x + bw, y + bh), color, thickness)
        label = f"{region['region_id']} {region['kind']}"
        if show_line_counts:
            label += f" {region['est_lines']}L"
        cv2.putText(
            viz,
            label,
            (x, max(18, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            image.shape[0] / 2200,
            color,
            thickness,
        )
    cv2.putText(
        viz,
        f"route: {plan.get('route', '?')}",
        (20, image.shape[0] - 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        image.shape[0] / 1400,
        (30, 30, 200),
        thickness + 1,
    )
    return viz


def _overlay(
    image: np.ndarray, doc_id: str, page_id: str, library_root: Path
) -> np.ndarray:
    regions_path = artifact_path(doc_id, "page_regions", page_id, library_root)
    if not regions_path.exists():
        return image
    return _draw_plan(image, read_json(regions_path), show_line_counts=False)


def _labeled_panel(image: np.ndarray, label: str) -> np.ndarray:
    scale = PANEL_HEIGHT / image.shape[0]
    panel = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bar = np.full((36, panel.shape[1], 3), 24, np.uint8)
    cv2.putText(bar, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1)
    return np.vstack([bar, panel])
