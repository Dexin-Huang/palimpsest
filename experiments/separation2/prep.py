"""Imaging prep in front of separation — cycle 1's verdict, implemented.

Reuses the factory's imaging bench directly (parchment_frame,
trim_gutter, remove_overlay_marks, flatten_illumination) and adds the
blank gate: after the watermark is removed, a page with almost no ink is
BLANK, and correct separation on a blank page is zero cells.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2]))
from palimpsest.factory.imaging import (  # noqa: E402
    flatten_illumination,
    parchment_frame,
    remove_overlay_marks,
    to_gray,
    trim_gutter,
)

BLANK_INK_FRACTION = 0.0035


def prepare(image: np.ndarray) -> tuple[np.ndarray | None, dict]:
    """Raw scan -> study image, or (None, info) for a blank page."""
    gray = to_gray(image)
    x0, y0, x1, y1 = parchment_frame(gray)
    page = image[y0:y1, x0:x1]
    gx0, gx1 = trim_gutter(to_gray(page))
    page = page[:, gx0:gx1]
    page = remove_overlay_marks(page)
    page = flatten_illumination(page)

    study = to_gray(page)
    ink = cv2.adaptiveThreshold(
        study, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=35, C=12)
    fraction = float((ink > 0).mean())
    info = {"frame": [int(x0), int(y0), int(x1), int(y1)],
            "gutter": [int(gx0), int(gx1)],
            "ink_fraction": round(fraction, 5)}
    if fraction < BLANK_INK_FRACTION:
        info["blank"] = True
        return None, info
    info["blank"] = False
    return page, info
