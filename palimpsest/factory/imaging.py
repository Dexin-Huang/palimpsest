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

DARK_INK_OFFSET = 50  # gray levels below parchment median = dark ink
INK_CORE_OFFSET = 85  # a REAL ink stroke has a core at least this far
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
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=35,
        C=18,
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
    keep = np.zeros(n, dtype=np.uint8)
    for i in range(1, n):
        _, _, _, bh, area = stats[i]
        if bh <= max_height and area >= min_area:
            keep[i] = 255
    return dark, keep[labels]


def remove_overlay_marks(
    image: np.ndarray,
    *,
    height_fraction: float = 0.01,
    max_std: float = 12.0,
    analysis_max_side: int = 1600,
) -> np.ndarray:
    """Paint digital overlay marks (watermarks, stamps) back to background.

    Two-part discriminator, both required: letterform HEIGHT (overlay letters
    are tall; pencil words are wide but short) and intensity UNIFORMITY
    (a rendered overlay has near-constant gray; pencil and faded ink vary
    with pressure). Dark ink is never touched.

    Detection runs at analysis scale — INTER_AREA downsampling averages out
    the JPEG noise that hides low-contrast overlays from the adaptive
    threshold at full resolution — and the removal mask is painted back at
    full resolution.
    """
    gray_full = to_gray(image)
    height, width = gray_full.shape
    scale = min(1.0, analysis_max_side / max(height, width))
    gray = (
        cv2.resize(gray_full, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else gray_full
    )

    bg = background_level(gray)
    # The overlay lives in the LIGHT BAND between parchment and ink depth.
    # Detect in the band directly — the adaptive mark mask only catches
    # letter fragments because a soft overlay's local contrast is too low.
    band_low, band_high = bg - DARK_INK_OFFSET, bg - 8
    light = ((gray > band_low) & (gray < band_high)).astype(np.uint8) * 255
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    max_height = max(2, int(min(gray.shape) * height_fraction))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(light)
    to_remove = np.zeros_like(light)
    for i in range(1, n):
        _, _, bw, bh, area = stats[i]
        if bh <= max_height or area < 4:
            continue
        component = labels == i
        if float(np.std(gray[component])) <= max_std:
            to_remove[component] = 255

    if scale < 1.0:
        to_remove = cv2.resize(
            to_remove, (width, height), interpolation=cv2.INTER_NEAREST
        )
    # dilate generously so anti-aliased overlay edges go too, then keep hands
    # off anything that is real dark ink at full resolution
    pad = max(3, int(round(1 / max(scale, 1e-6))) + 2)
    to_remove = cv2.dilate(to_remove, np.ones((pad, pad), np.uint8))
    dark_full = gray_full < background_level(gray_full) - DARK_INK_OFFSET
    to_remove[dark_full] = 0

    cleaned = image.copy()
    if cleaned.ndim == 2:
        cleaned[to_remove > 0] = int(bg)
    else:
        fill = np.median(image.reshape(-1, image.shape[2]), axis=0)
        cleaned[to_remove > 0] = fill
    return cleaned


def flatten_illumination(image: np.ndarray, *, target: int = 235) -> np.ndarray:
    """Divide out the low-frequency background field: vellum shading, gutter
    shadow, and uneven lighting go away; strokes keep their shape and color."""
    channels = image.astype(np.float32)
    sigma = max(image.shape[:2]) / 40
    field = cv2.GaussianBlur(channels, (0, 0), sigmaX=sigma)
    flat = channels / np.maximum(field, 1) * target
    return np.clip(flat, 0, 255).astype(np.uint8)


def attenuate_light_marks(
    image: np.ndarray, *, ink_offset: int = DARK_INK_OFFSET, factor: float = 0.45
) -> np.ndarray:
    """Push everything LIGHTER than the ink band toward white, proportionally.

    Show-through and residue fade; ink is untouched; nothing is thresholded
    away — a faint-but-real stroke dims instead of disappearing.
    """
    gray = to_gray(image)
    cut = background_level(gray) - ink_offset
    lighter = gray > cut  # 2-D mask broadcasts over color channels
    result = image.astype(np.float32)
    result[lighter] = 255 - (255 - result[lighter]) * factor
    return np.clip(result, 0, 255).astype(np.uint8)


def encode_jpeg(image: np.ndarray, *, quality: int = 92) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buffer.tobytes()


def parchment_frame(
    gray: np.ndarray, *, margin_fraction: float = 0.02
) -> tuple[int, int, int, int]:
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
        bright, cv2.MORPH_CLOSE, np.ones((max(3, h // 100),) * 2, np.uint8)
    )
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
    if n < 2:
        return 0, 0, w, h
    largest = 1 + int(np.argmax(stats[1:, 4]))
    x, y, bw, bh, _ = stats[largest]
    mx, my = int(bw * margin_fraction), int(bh * margin_fraction)
    return x + mx, y + my, x + bw - mx, y + bh - my


def trim_gutter(
    gray: np.ndarray, *, search_fraction: float = 0.3, min_depth: int = 35
) -> tuple[int, int]:
    """Find the binding crease inside a framed page and return (x0, x1) crop.

    When the parchment frame connects across the gutter, a strip of the
    NEIGHBORING page rides along. The crease between them is a vertical
    shadow: a column whose MEDIAN intensity is far below the page's — text
    columns never drag the median down that far, a fold shadow does. Cut at
    the deepest such column in each outer zone.
    """
    h, w = gray.shape
    column_median = np.median(gray, axis=0)
    bg = background_level(gray)
    zone = max(1, int(w * search_fraction))

    x0 = 0
    left = column_median[:zone]
    if left.min() < bg - min_depth:
        x0 = int(np.argmin(left)) + max(2, w // 200)
    x1 = w
    right = column_median[w - zone :]
    if right.min() < bg - min_depth:
        x1 = w - zone + int(np.argmin(right)) - max(2, w // 200)
    if x1 - x0 < w * 0.4:  # refuse absurd cuts; keep the frame as-is
        return 0, w
    return x0, x1


def glyph_height(ink: np.ndarray) -> int:
    """Median height of small connected components ≈ letter height.

    Estimate from DARK ink only — speckle and show-through in the faint
    channel collapse the median to noise scale.
    """
    h = ink.shape[0]
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    heights = [
        stats[i][3]
        for i in range(1, n)
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
