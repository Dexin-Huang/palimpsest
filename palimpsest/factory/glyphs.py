"""Forced alignment of a transcription to ink blobs.

Pure geometry, no model calls. The transcription supplies the character
sequence; the image supplies ink blobs; dynamic programming binds them
column by column. Order is the signal: merged blobs (ink bleed) and split
blobs (damage) are absorbed by the alignment ops, never by re-reading.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# characters the transcription may carry that have no ink of their own
_NON_INK = set("〔〕?()[] 　")
_MIN_BLOB_AREA_FRAC = 0.05  # of median glyph area — below is speckle
_SMALL_GLYPH_FRAC = 0.45  # of main glyph height — interlinear/gloss pool
_COLUMN_GAP_FRAC = 0.55  # of glyph width — x-gap that splits columns
_MERGE_MAX = 4  # blobs a single character may span vertically
_GLYPH_OF_PITCH = 0.62  # glyph height as fraction of column pitch


@dataclass
class Cell:
    """One vertical slot in a column: a blob or a fused run of fragments."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def bbox(self) -> list[int]:
        return [
            int(self.x0),
            int(self.y0),
            int(self.x1 - self.x0),
            int(self.y1 - self.y0),
        ]

    def fuse(self, other: "Cell") -> "Cell":
        return Cell(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


def align_page(image: np.ndarray, lines: list[str]) -> dict:
    """Bind transcription lines (columns, right-to-left) to ink. Returns the
    page_alignment payload body: columns + stats."""
    mask = binarize(image)
    char_lines = [_ink_chars(line) for line in lines]
    if not char_lines:
        return {"columns": [], "stats": _stats([], [], 0)}
    rough = ink_blobs(mask)
    if not rough:
        out_columns = [
            _column_payload([(ch, None, 0.0, "none") for ch in chars])
            for chars in char_lines
        ]
        return {"columns": out_columns, "stats": _stats(char_lines, out_columns, 0)}
    # glyph scale from the page's strongest periodic signal — the column
    # pitch — not from blob heights (strokes masquerade as tiny glyphs)
    pitch = _column_pitch(mask)
    glyph_h = pitch * _GLYPH_OF_PITCH if pitch else _main_glyph_height(rough)
    # close conservatively: fuse intra-character stroke gaps, never the
    # (larger) inter-character gaps — the DP merge ops absorb the rest
    fuse = max(3, int(glyph_h * 0.15))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((fuse, fuse), np.uint8))
    blobs = ink_blobs(closed)

    main = [b for b in blobs if b.h >= glyph_h * _SMALL_GLYPH_FRAC]
    small_pool = len(blobs) - len(main)
    bands = column_bands(closed, glyph_h)
    columns = [_band_cells(main, band, glyph_h) for band in bands]
    columns = [c for c in columns if c]

    # image columns right-to-left; transcription lines in reading order
    columns.sort(key=lambda col: -max(c.x1 for c in col))
    paired = list(zip(columns, char_lines))

    out_columns = []
    for cells, chars in paired:
        out_columns.append(_column_payload(align_column(cells, chars, glyph_h)))
    # unpaired transcription lines (image column undetected) stay auditable
    for chars in char_lines[len(paired) :]:
        out_columns.append(_column_payload([(ch, None, 0.0, "none") for ch in chars]))

    return {
        "columns": out_columns,
        "stats": _stats(
            char_lines, out_columns, small_pool, image_columns=len(columns)
        ),
    }


def binarize(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    mask = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=35,
        C=12,
    )
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def ink_blobs(mask: np.ndarray) -> list[Cell]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cells = [
        Cell(x, y, x + w, y + h) for x, y, w, h, area in stats[1:count] if area > 0
    ]
    if not cells:
        return []
    med_area = float(np.median([c.h * (c.x1 - c.x0) for c in cells]))
    floor = med_area * _MIN_BLOB_AREA_FRAC
    return [c for c in cells if c.h * (c.x1 - c.x0) >= floor]


def _column_pitch(mask: np.ndarray) -> float | None:
    """Dominant column spacing via autocorrelation of the ink profile."""
    profile = mask.sum(axis=0).astype(np.float64)
    profile -= profile.mean()
    if not profile.any():
        return None
    ac = np.correlate(profile, profile, mode="full")[len(profile) - 1 :]
    lo, hi = 20, max(21, len(profile) // 6)
    if hi <= lo or ac[0] <= 0:
        return None
    lag = lo + int(np.argmax(ac[lo:hi]))
    return float(lag) if ac[lag] > 0.15 * ac[0] else None


def _main_glyph_height(blobs: list[Cell]) -> float:
    heights = sorted(c.h for c in blobs)
    top = heights[int(len(heights) * 0.5) :]  # upper half: real glyphs
    return float(np.median(top)) if top else 1.0


def column_bands(mask: np.ndarray, glyph_h: float) -> list[tuple[int, int]]:
    """Vertical ink-projection profile; columns are runs above threshold."""
    profile = mask.sum(axis=0).astype(np.float64)
    window = max(3, int(glyph_h * 0.4)) | 1
    smoothed = np.convolve(profile, np.ones(window) / window, mode="same")
    positive = smoothed[smoothed > 0]
    if positive.size == 0:
        return []
    threshold = float(np.median(positive)) * 0.25
    on = smoothed > threshold
    bands, start = [], None
    for x, flag in enumerate(on):
        if flag and start is None:
            start = x
        elif not flag and start is not None:
            bands.append((start, x))
            start = None
    if start is not None:
        bands.append((start, len(on)))
    split: list[tuple[int, int]] = []
    for band in bands:
        split.extend(_split_wide(band, smoothed, glyph_h))
    return [(a, b) for a, b in split if b - a >= glyph_h * 0.4]


def _split_wide(
    band: tuple[int, int], profile: np.ndarray, glyph_h: float
) -> list[tuple[int, int]]:
    """Dense pages fuse neighboring columns into one projection run; split
    recursively at interior valleys until bands are column-pitched."""
    a, b = band
    if b - a <= glyph_h * 2.1:
        return [band]
    margin = max(2, int(glyph_h * 0.7))
    interior = profile[a + margin : b - margin]
    if interior.size == 0:
        return [band]
    cut = a + margin + int(np.argmin(interior))
    left_peak = float(profile[a:cut].max())
    right_peak = float(profile[cut:b].max())
    if profile[cut] > 0.4 * min(left_peak, right_peak):
        return [band]  # valley not deep enough — a truly wide ink mass
    return _split_wide((a, cut), profile, glyph_h) + _split_wide(
        (cut, b), profile, glyph_h
    )


def _band_cells(blobs: list[Cell], band: tuple[int, int], glyph_h: float) -> list[Cell]:
    """Blobs whose center falls in the band, pre-fused into vertical cells:
    fragments closer than a fraction of glyph height belong to one glyph."""
    x0, x1 = band
    inside = sorted(
        (c for c in blobs if x0 <= (c.x0 + c.x1) / 2 < x1), key=lambda c: c.y0
    )
    cells: list[Cell] = []
    for blob in inside:
        if (
            cells
            and blob.y0 - cells[-1].y1 < glyph_h * 0.18
            and cells[-1].h + blob.h < glyph_h * 1.6
        ):
            cells[-1] = cells[-1].fuse(blob)
        else:
            cells.append(Cell(blob.x0, blob.y0, blob.x1, blob.y1))
    return cells


def align_column(
    cells: list[Cell], chars: list[str], glyph_h: float
) -> list[tuple[str, list[int] | None, float, str]]:
    """DP over (cell index, char index): a char takes 1..MERGE_MAX cells, a
    char may be skipped (damage), a cell may be skipped (noise/unread ink)."""
    n, m = len(cells), len(chars)
    skip_char, skip_cell = 1.0, 0.9
    infinity = float("inf")
    cost = np.full((n + 1, m + 1), infinity)
    # consumed cells identify the three moves without stringly operation names:
    # -1 skips ink, 0 marks a missing character, 1..MERGE_MAX match ink.
    previous: dict[tuple[int, int], tuple[int, int, int]] = {}
    cost[0][0] = 0.0

    def advance(
        i: int, j: int, next_i: int, next_j: int, candidate: float, consumed: int
    ) -> None:
        if candidate < cost[next_i][next_j]:
            cost[next_i][next_j] = candidate
            previous[(next_i, next_j)] = (i, j, consumed)

    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == infinity:
                continue
            if j < m:
                advance(i, j, i, j + 1, here + skip_char, 0)
            if i < n:
                advance(i, j, i + 1, j, here + skip_cell, -1)
            if j == m:
                continue

            fused = None
            for consumed in range(1, min(_MERGE_MAX, n - i) + 1):
                fused = fused.fuse(cells[i + consumed - 1]) if fused else cells[i]
                candidate = here + _match_cost(fused, glyph_h) + 0.15 * (consumed - 1)
                advance(i, j, i + consumed, j + 1, candidate, consumed)

    aligned: list[tuple[str, list[int] | None, float, str]] = []
    i, j = n, m
    while (i, j) != (0, 0):
        previous_i, previous_j, consumed = previous[(i, j)]
        if consumed == 0:
            aligned.append((chars[previous_j], None, 0.0, "none"))
        elif consumed > 0:
            fused = cells[previous_i]
            for extra in range(previous_i + 1, i):
                fused = fused.fuse(cells[extra])
            fit = _match_cost(fused, glyph_h)
            confidence = max(0.05, 1.0 - fit)
            method = "blob" if consumed == 1 else "merged"
            aligned.append(
                (chars[previous_j], fused.bbox(), round(confidence, 3), method)
            )
        i, j = previous_i, previous_j
    aligned.reverse()
    return aligned


def _match_cost(cell: Cell, glyph_h: float) -> float:
    return abs(cell.h - glyph_h) / glyph_h * 0.8


def _ink_chars(line: str) -> list[str]:
    return [ch for ch in line.strip() if ch not in _NON_INK]


def _column_payload(aligned) -> dict:
    boxed = [b for _, b, _, _ in aligned if b]
    bbox = None
    if boxed:
        xs0 = min(b[0] for b in boxed)
        ys0 = min(b[1] for b in boxed)
        xs1 = max(b[0] + b[2] for b in boxed)
        ys1 = max(b[1] + b[3] for b in boxed)
        bbox = [xs0, ys0, xs1 - xs0, ys1 - ys0]
    return {
        "bbox": bbox,
        "chars": [
            {"ch": ch, "bbox": b, "confidence": conf, "method": method}
            for ch, b, conf, method in aligned
        ],
    }


def _stats(char_lines, out_columns, small_pool, image_columns=0) -> dict:
    transcribed = sum(len(line) for line in char_lines)
    boxed = sum(1 for col in out_columns for c in col["chars"] if c["bbox"])
    mismatch = sum(
        1 for col in out_columns if any(c["method"] == "none" for c in col["chars"])
    )
    return {
        "transcribed": transcribed,
        "boxed": boxed,
        "count_mismatch_columns": mismatch,
        "image_columns": image_columns,
        "small_blobs_unassigned": small_pool,
    }
