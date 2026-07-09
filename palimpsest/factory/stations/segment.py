"""segment: ink-density polygon lassos + the routing decision.

Deterministic CV, no model call, effectively free — so it runs on every
page and decides how the page should be read:

- ``route: full_page``  — light page; read sends the whole image, one call
- ``route: segmented``  — dense page; read lifts each region onto a white
  tile and makes one bounded call per region
- ``route: blank``      — no ink found; read writes an empty transcription
  without spending a single token

Regions are paragraph-scale blobs: two ink classes (dark + faint small
marks), fused by dilation scaled to the measured glyph height, oversized
blobs split at projection-profile valleys so no single read can blow the
output-token budget. Coordinates are pixels in the cleaned image.
"""

from __future__ import annotations

import cv2
import numpy as np

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import glyph_height, ink_masks, to_gray

ANALYSIS_MAX_SIDE = 1600
MIN_REGION_INK_PX = 60
# Drawing strokes (diagram circles, long curves) are single ink components
# big in BOTH dimensions but nearly empty in their bbox; writing components
# are glyph-scale. Separated before fusing so figures stay whole.
DRAWING_MIN_GLYPHS = 4.0
DRAWING_MAX_DENSITY = 0.15
# Verso bleed-through passes the ink-core test on heavily written folios,
# but its MEDIAN depth is consistently shallower than same-page real ink
# (measured on Pal.lat.1199: bleed ≈ 141-146 gray vs text ≈ 111-117).
BLEED_DEPTH_DELTA = 22
# Real writing fills ≥ ~4% of its fused bbox; blobs fused out of scattered
# dirt/foxing specks measure ≤ ~1.6% (measured on Pal.lat.1199).
MIN_REGION_DENSITY = 0.03
# A full-page read needs substantial ink to anchor the model; pages below
# this go as region tiles instead — a stain tile reads as empty, but a
# near-blank full page invites page-scale hallucination. Measured on
# Pal.lat.1199: real-but-light content ≈ 22k ink px, stain/bleed bands ≈ 10k.
FULL_PAGE_MIN_INK_PX = 15000
MAX_LINES_PER_REGION = 14     # split blobs taller than this many text lines
FULL_PAGE_MAX_REGIONS = 3     # routing: few blobs and few lines → one call
FULL_PAGE_MAX_LINES = 30


class Segment(Station):
    name = "segment"
    version = "segment/v1"
    grain = "page"
    consumes = ("page_image_clean",)
    produces = "page_regions"

    def run(self, job: Job) -> StationResult:
        image = cv2.imread(str(job.path_of("page_image_clean")))
        if image is None:
            raise ValueError(f"Unreadable image: {job.path_of('page_image_clean')}")
        payload = analyze(image, dict(job.config.options))
        return StationResult(payload={
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            **payload,
        })


def analyze(image: np.ndarray, options: dict) -> dict:
    """The pure analysis: image in, route + regions out. Also the entry
    point for the offline tuning harness (``factory tune``)."""
    gray_full = to_gray(image)
    h, w = gray_full.shape

    # Physical-object shots (spine, fore-edge, ruler cards) have pathological
    # aspect ratios; a folio is roughly 3:4. Nothing on them is readable text.
    aspect = w / h
    if aspect < 0.35 or aspect > 3.0:
        return {
            "route": "blank",
            "image": {"width": w, "height": h},
            "glyph_height_px": 0.0,
            "regions": [],
        }
    scale = min(1.0, ANALYSIS_MAX_SIDE / max(h, w))
    gray = cv2.resize(gray_full, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA) if scale < 1.0 else gray_full

    dark, faint = ink_masks(gray)
    # The faint channel (pencil notes, faded ink) is opt-in: on textured
    # or show-through-heavy scans it is dominated by noise. When enabled,
    # a density guard still drops it if it lights up broadly relative to
    # real ink — sparse annotations, not surface texture.
    if options.get("include_faint", False):
        dark_px = cv2.countNonZero(dark)
        if cv2.countNonZero(faint) > max(3 * dark_px, gray.size * 0.005):
            faint = np.zeros_like(faint)
    else:
        faint = np.zeros_like(faint)
    ink = cv2.bitwise_or(dark, faint)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    glyph = glyph_height(dark)

    writing, figure_boxes = _extract_drawings(ink, glyph)
    blobs = _drop_edge_artifacts(_blobs(writing, glyph), gray.shape)
    blobs = _merge_overlaps(blobs)
    blobs = [split for blob in blobs for split in _split_tall(writing, blob, glyph)]
    regions = _classify(blobs, writing, gray, glyph, scale)
    page_depth = float(np.median(gray[writing > 0])) if cv2.countNonZero(writing) else None
    regions = _drop_bleed_through(regions, page_depth, options)
    regions = _add_figures(regions, figure_boxes, ink, scale)
    regions.sort(key=lambda r: (r["kind"] == "marginalia", r["bbox"][1], r["bbox"][0]))
    for order, region in enumerate(regions):
        region["region_id"] = f"r{order:02d}"
        region["reading_order"] = order

    total_lines = sum(r["est_lines"] for r in regions)
    total_ink = sum(r["ink_px"] for r in regions)
    if not regions:
        route = "blank"
    elif (total_ink >= int(options.get("full_page_min_ink", FULL_PAGE_MIN_INK_PX))
          and len(regions) <= int(options.get("full_page_max_regions", FULL_PAGE_MAX_REGIONS))
          and total_lines <= int(options.get("full_page_max_lines", FULL_PAGE_MAX_LINES))):
        route = "full_page"
    else:
        route = "segmented"

    return {
        "route": route,
        "image": {"width": w, "height": h},
        "glyph_height_px": round(glyph / scale, 1),
        "regions": regions,
    }


def _blobs(ink: np.ndarray, glyph: int) -> list[tuple[int, int, int, int]]:
    fuse_words = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(3, int(glyph * 2.2)), max(3, int(glyph * 0.8))))
    fuse_lines = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(3, int(glyph * 0.8)), max(3, int(glyph * 1.7))))
    fused = cv2.dilate(cv2.dilate(ink, fuse_words), fuse_lines)
    n, _, stats, _ = cv2.connectedComponentsWithStats(fused)
    return [tuple(stats[i][:4]) for i in range(1, n)
            if stats[i][4] >= ink.shape[0] * ink.shape[1] * 0.0004]


def _extract_drawings(
    ink: np.ndarray, glyph: int
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Split ink into writing vs drawing strokes; return (writing_mask,
    merged figure bboxes). A drawing stroke is one component big in both
    dimensions with almost no fill — a circle arc, not a word."""
    min_side = glyph * DRAWING_MIN_GLYPHS
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    writing = ink.copy()
    drawing_boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw > min_side and bh > min_side and area < DRAWING_MAX_DENSITY * bw * bh:
            writing[labels == i] = 0
            drawing_boxes.append((x, y, bw, bh))
    return writing, _merge_overlaps(drawing_boxes)


def _drop_bleed_through(
    regions: list[dict], page_depth: float | None, options: dict
) -> list[dict]:
    """Drop regions whose ink is uniformly shallower than the page's own
    ink median — verso bleed-through. The page-wide median is dominated by
    real body text and immune to a few dark initials; on faded hands
    (where EVERYTHING is shallow) the reference shifts with them."""
    if page_depth is None or len(regions) < 2:
        return regions
    delta = float(options.get("bleed_depth_delta", BLEED_DEPTH_DELTA))
    return [r for r in regions if r["depth"] <= page_depth + delta]


def _add_figures(
    regions: list[dict], figure_boxes: list[tuple[int, int, int, int]],
    ink: np.ndarray, scale: float,
) -> list[dict]:
    """Figures become whole regions (never split); writing regions mostly
    inside a figure are dropped — the figure tile reads their labels."""
    for x, y, bw, bh in figure_boxes:
        ink_px = int(cv2.countNonZero(ink[y:y + bh, x:x + bw]))
        regions = [r for r in regions if not _mostly_inside(
            [int(v * scale) for v in r["bbox"]], (x, y, bw, bh))]
        regions.append({
            "kind": "figure",
            "bbox": [int(v / scale) for v in (x, y, bw, bh)],
            "est_lines": 1,
            "ink_px": ink_px,
            "depth": 0.0,
        })
    return regions


def _mostly_inside(inner: list[int], outer: tuple[int, int, int, int]) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    overlap_w = max(0, min(ix + iw, ox + ow) - max(ix, ox))
    overlap_h = max(0, min(iy + ih, oy + oh) - max(iy, oy))
    return overlap_w * overlap_h >= 0.7 * iw * ih


def _drop_edge_artifacts(
    blobs: list[tuple[int, int, int, int]], shape: tuple[int, int]
) -> list[tuple[int, int, int, int]]:
    """Filter frame residue BEFORE merging, so junk can't fuse with content.

    Two shapes of residue, both flush to an image edge: thin strips (binding
    line, gutter sliver) and full-span bands (page-curl shadow, backdrop).
    Real marginal content — catchwords, folio marks — is neither.
    """
    hs, ws = shape
    tol_x, tol_y = max(3, int(ws * 0.005)), max(3, int(hs * 0.005))
    kept = []
    for x, y, bw, bh in blobs:
        flush_x = x <= tol_x or x + bw >= ws - tol_x
        flush_y = y <= tol_y or y + bh >= hs - tol_y
        # Residue is always THIN in one dimension: a binding line or gutter
        # sliver (flush to a side, narrow), a curl-shadow band (flush to
        # top/bottom, short). A blob big in BOTH dimensions is page content —
        # a dense text block legitimately spans edge to edge.
        if flush_x and bw < ws * 0.07:
            continue
        if flush_y and bh < hs * 0.03:
            continue
        if flush_x and bh > hs * 0.8 and bw < ws * 0.25:
            continue
        if flush_y and bw > ws * 0.8 and bh < hs * 0.25:
            continue
        kept.append((x, y, bw, bh))
    return kept


def _merge_overlaps(
    blobs: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Union blobs whose bboxes substantially overlap — L-shaped components
    produce overlapping boxes that would otherwise be read twice."""
    merged = list(blobs)
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                ax, ay, aw, ah = merged[i]
                bx, by, bw, bh = merged[j]
                ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
                iy = max(0, min(ay + ah, by + bh) - max(ay, by))
                if ix * iy >= 0.4 * min(aw * ah, bw * bh):
                    x0, y0 = min(ax, bx), min(ay, by)
                    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
                    merged[i] = (x0, y0, x1 - x0, y1 - y0)
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _split_tall(
    ink: np.ndarray, bbox: tuple[int, int, int, int], glyph: int
) -> list[tuple[int, int, int, int]]:
    """Split a blob taller than the line budget at its widest whitespace valleys."""
    x, y, bw, bh = bbox
    line_height = glyph * 1.7
    lines = bh / line_height
    if lines <= MAX_LINES_PER_REGION:
        return [bbox]
    pieces = int(np.ceil(lines / MAX_LINES_PER_REGION))
    profile = ink[y:y + bh, x:x + bw].sum(axis=1)
    cuts = []
    for k in range(1, pieces):
        target = int(bh * k / pieces)
        lo = max(0, target - int(line_height * 2))
        hi = min(bh - 1, target + int(line_height * 2))
        cuts.append(lo + int(np.argmin(profile[lo:hi + 1])))
    edges = [0, *sorted(cuts), bh]
    return [(x, y + a, bw, b - a) for a, b in zip(edges, edges[1:]) if b - a > glyph]


def _classify(
    blobs: list[tuple[int, int, int, int]], ink: np.ndarray,
    gray: np.ndarray, glyph: int, scale: float,
) -> list[dict]:
    hs, ws = gray.shape
    regions = []
    for x, y, bw, bh in blobs:
        roi_ink = ink[y:y + bh, x:x + bw] > 0
        ink_px = int(roi_ink.sum())
        if ink_px < MIN_REGION_INK_PX or ink_px < MIN_REGION_DENSITY * bw * bh:
            continue
        depth = float(np.median(gray[y:y + bh, x:x + bw][roi_ink]))
        cx, cy = (x + bw / 2) / ws, (y + bh / 2) / hs
        frac = (bw * bh) / (hs * ws)
        if frac >= 0.10 and 0.2 <= cx <= 0.8:
            kind = "main_text"
        elif cx < 0.16 or cx > 0.84 or cy < 0.10 or cy > 0.90:
            kind = "marginalia"
        else:
            kind = "block"
        regions.append({
            "kind": kind,
            "bbox": [int(v / scale) for v in (x, y, bw, bh)],
            "est_lines": max(1, round(bh / (glyph * 1.7))),
            "ink_px": ink_px,
            "depth": depth,
        })
    return regions


register(Segment())
