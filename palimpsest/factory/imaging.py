"""Shared CV primitives for the image stations (prepare, segment).

Everything here is deterministic: same pixels + same params = same result,
which is what keeps segmentation compatible with the fingerprint system.
Two ink classes matter on archive scans: dark ink (iron gall) sits far below
the parchment brightness; faint marks (pencil, faded ink) sit in the same
light band as digital watermarks and are separable only by structure —
watermark letterforms are large, faint annotations are small.
"""

from __future__ import annotations

import cv2
import numpy as np

DARK_INK_OFFSET = 50        # gray levels below parchment median = dark ink
INK_CORE_OFFSET = 85        # a REAL ink stroke has a core at least this far
                            # below parchment; verso show-through never does
# Light components TALLER than this fraction of the short page side are
# watermark/stamp letterforms. Height is the discriminator: a faint pencil
# word is wide but short; watermark letters are tall even one at a time.
LARGE_LIGHT_HEIGHT_FRACTION = 0.015


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def background_level(gray: np.ndarray) -> float:
    """Parchment brightness: the median dominates because most of a page is background."""
    return float(np.median(gray))


def mark_mask(gray: np.ndarray) -> np.ndarray:
    """Everything that is not parchment: adaptive threshold handles gradients."""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, blockSize=35, C=18,
    )


def ink_masks(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split all marks into (dark_ink, faint_small_marks).

    Faint marks keep only SMALL components — large light structures are
    watermarks/stamps, not annotations.
    """
    marks = mark_mask(gray)
    bg = background_level(gray)
    dark = cv2.bitwise_and(marks, (gray < bg - DARK_INK_OFFSET).astype(np.uint8) * 255)

    # Core test: demote "dark" components whose darkest pixel never reaches
    # true ink depth — that is show-through, not writing on THIS side.
    n, labels = cv2.connectedComponents(dark)
    if n > 1:
        core = gray < bg - INK_CORE_OFFSET
        has_core = np.zeros(n, dtype=bool)
        np.logical_or.at(has_core, labels[core], True)
        has_core[0] = True  # background label never demotes
        demoted = ~has_core[labels] & (dark > 0)
        dark[demoted] = 0

    light = cv2.subtract(marks, dark)

    h, w = gray.shape
    max_height = max(2, int(min(h, w) * LARGE_LIGHT_HEIGHT_FRACTION))
    # parchment texture/JPEG speckle: too small to be writing at this scale
    min_area = max(6, int((min(h, w) * 0.002) ** 2))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(light)
    faint = np.zeros_like(light)
    for i in range(1, n):
        _, _, bw, bh, area = stats[i]
        if bh <= max_height and area >= min_area:
            faint[labels == i] = 255
    return dark, faint


def remove_large_light_marks(image: np.ndarray) -> np.ndarray:
    """Paint watermark-scale light components back to background color.

    Preserves dark ink untouched and keeps small faint marks (pencil notes).
    """
    gray = to_gray(image)
    marks = mark_mask(gray)
    bg = background_level(gray)
    dark = (gray < bg - DARK_INK_OFFSET).astype(np.uint8) * 255
    light = cv2.bitwise_and(marks, cv2.bitwise_not(dark))

    h, w = gray.shape
    max_height = max(2, int(min(h, w) * LARGE_LIGHT_HEIGHT_FRACTION))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(light)
    to_remove = np.zeros_like(light)
    for i in range(1, n):
        _, _, bw, bh, _ = stats[i]
        if bh > max_height:
            to_remove[labels == i] = 255
    # dilate slightly so anti-aliased watermark edges go too
    to_remove = cv2.dilate(to_remove, np.ones((3, 3), np.uint8))
    to_remove = cv2.bitwise_and(to_remove, cv2.bitwise_not(dark))

    cleaned = image.copy()
    if cleaned.ndim == 2:
        cleaned[to_remove > 0] = int(bg)
    else:
        fill = np.median(image.reshape(-1, image.shape[2]), axis=0)
        cleaned[to_remove > 0] = fill
    return cleaned


def parchment_frame(gray: np.ndarray, *, margin_fraction: float = 0.02) -> tuple[int, int, int, int]:
    """Locate the page itself: the largest bright region vs the dark backdrop
    (binding, scanner bed). Returns (x0, y0, x1, y1) with an inward margin.

    Detection beats fixed crop fractions because rectos, versos, and
    different digitization campaigns frame the page differently. If there is
    no dark backdrop (born-clean images), returns the full frame.
    """
    h, w = gray.shape
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if cv2.countNonZero(bright) > 0.92 * h * w:
        return 0, 0, w, h
    bright = cv2.morphologyEx(
        bright, cv2.MORPH_CLOSE, np.ones((max(3, h // 100),) * 2, np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
    if n < 2:
        return 0, 0, w, h
    largest = 1 + int(np.argmax(stats[1:, 4]))
    x, y, bw, bh, _ = stats[largest]
    mx, my = int(bw * margin_fraction), int(bh * margin_fraction)
    return x + mx, y + my, x + bw - mx, y + bh - my


def glyph_height(ink: np.ndarray) -> int:
    """Median height of small connected components ≈ letter height.

    Estimate from DARK ink only — speckle and show-through in the faint
    channel collapse the median to noise scale.
    """
    h = ink.shape[0]
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    heights = [
        stats[i][3] for i in range(1, n)
        if 3 <= stats[i][3] <= h * 0.05 and stats[i][4] >= 8 and stats[i][2] >= 2
    ]
    if not heights:
        return max(4, h // 100)
    return int(np.median(heights))


def encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG encoding failed")
    return buffer.tobytes()
