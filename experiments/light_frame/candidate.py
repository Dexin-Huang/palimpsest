"""Light-backdrop framing challenger for the separation2 champion.

The incumbent finds bright parchment against a dark scanner bed. This arm adds
color distance from the image border so gray Gallica/IDP mounts can be removed
without changing geometry, gates, or the language prior.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
SEPARATION2 = HERE.parent / "separation2"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


champion = load("light_frame_champion", SEPARATION2 / "separate.py")

MAX_DETECTION_SIDE = 1000
BORDER_FRACTION = 0.025
MIN_PAGE_DISTANCE = 8.0
MAX_PAGE_DISTANCE = 25.0
MIN_BACKDROP_LIGHTNESS = 105.0
MAX_BACKDROP_CHROMA = 18.0
CLOSE_FRACTION = 0.015
MIN_COMPONENT_FRACTION = 0.08
MIN_COMPONENT_SPAN = 0.20
FRAME_MARGIN_FRACTION = 0.01
MIN_FRAME_AREA_RATIO = 0.65
MAX_FRAME_AREA_RATIO = 0.90


def light_backdrop_frame(image: np.ndarray) -> tuple[tuple[int, int, int, int], dict]:
    """Locate parchment by Lab distance from the scanner-bed border."""
    h, w = image.shape[:2]
    gray = champion.prep.to_gray(image)
    fallback = tuple(int(value) for value in champion.prep.parchment_frame(gray))

    scale = min(1.0, MAX_DETECTION_SIDE / max(h, w))
    if scale < 1.0:
        sample = cv2.resize(
            image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
    else:
        sample = image
    if sample.ndim == 2:
        sample = cv2.cvtColor(sample, cv2.COLOR_GRAY2BGR)

    sh, sw = sample.shape[:2]
    lab = cv2.cvtColor(
        cv2.GaussianBlur(sample, (5, 5), 0), cv2.COLOR_BGR2LAB
    ).astype(np.float32)
    band = max(2, round(min(sh, sw) * BORDER_FRACTION))
    border = np.concatenate(
        (
            lab[:band].reshape(-1, 3),
            lab[-band:].reshape(-1, 3),
            lab[:, :band].reshape(-1, 3),
            lab[:, -band:].reshape(-1, 3),
        )
    )
    backdrop = np.median(border, axis=0)
    backdrop_chroma = float(np.linalg.norm(backdrop[1:] - 128.0))
    if (
        float(backdrop[0]) < MIN_BACKDROP_LIGHTNESS
        or backdrop_chroma > MAX_BACKDROP_CHROMA
    ):
        return fallback, {
            "method": "incumbent_fallback",
            "reason": "dark_or_colored_backdrop",
            "backdrop_lightness": round(float(backdrop[0]), 2),
            "backdrop_chroma": round(backdrop_chroma, 2),
        }
    distance = np.linalg.norm(lab - backdrop, axis=2)
    p90_distance = float(np.percentile(distance, 90))
    if p90_distance < MIN_PAGE_DISTANCE:
        return fallback, {
            "method": "incumbent_fallback",
            "reason": "low_border_contrast",
            "p90_distance": round(p90_distance, 2),
        }

    clipped = np.clip(distance, 0, 255).astype(np.uint8)
    otsu_threshold, _ = cv2.threshold(
        clipped, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    page_threshold = max(
        MIN_PAGE_DISTANCE, min(float(otsu_threshold), MAX_PAGE_DISTANCE)
    )
    mask = np.where(distance >= page_threshold, np.uint8(255), np.uint8(0))
    close_size = max(3, round(min(sh, sw) * CLOSE_FRACTION)) | 1
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8)
    )

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        area_fraction = area / (sh * sw)
        if (
            area_fraction >= MIN_COMPONENT_FRACTION
            and width >= MIN_COMPONENT_SPAN * sw
            and height >= MIN_COMPONENT_SPAN * sh
        ):
            components.append((area, x, y, width, height, area_fraction))
    if not components:
        return fallback, {
            "method": "incumbent_fallback",
            "reason": "no_plausible_component",
            "p90_distance": round(p90_distance, 2),
            "threshold": round(page_threshold, 2),
        }

    _, x, y, width, height, area_fraction = max(components)
    inverse_scale = 1.0 / scale
    x0, y0, x1, y1 = (
        int(round(value * inverse_scale))
        for value in (x, y, x + width, y + height)
    )
    margin_x = int((x1 - x0) * FRAME_MARGIN_FRACTION)
    margin_y = int((y1 - y0) * FRAME_MARGIN_FRACTION)
    frame = (
        max(0, x0 + margin_x),
        max(0, y0 + margin_y),
        min(w, x1 - margin_x),
        min(h, y1 - margin_y),
    )
    fallback_area = (fallback[2] - fallback[0]) * (fallback[3] - fallback[1])
    frame_area = (frame[2] - frame[0]) * (frame[3] - frame[1])
    frame_area_ratio = frame_area / max(1, fallback_area)
    if not MIN_FRAME_AREA_RATIO <= frame_area_ratio <= MAX_FRAME_AREA_RATIO:
        return fallback, {
            "method": "incumbent_fallback",
            "reason": "insubstantial_or_unsafe_crop",
            "candidate_area_ratio": round(frame_area_ratio, 4),
            "p90_distance": round(p90_distance, 2),
            "threshold": round(page_threshold, 2),
        }
    return frame, {
        "method": "lab_border_distance",
        "p90_distance": round(p90_distance, 2),
        "threshold": round(page_threshold, 2),
        "component_fraction": round(area_fraction, 4),
        "candidate_area_ratio": round(frame_area_ratio, 4),
    }


def prepare(image: np.ndarray) -> tuple[np.ndarray | None, dict]:
    """Raw scan to study image using the challenger frame detector."""
    (x0, y0, x1, y1), detector = light_backdrop_frame(image)
    page = image[y0:y1, x0:x1]
    gx0, gx1 = champion.prep.trim_gutter(champion.prep.to_gray(page))
    page = page[:, gx0:gx1]
    page = champion.prep.remove_overlay_marks(page)
    page = champion.prep.flatten_illumination(page)

    study = champion.prep.to_gray(page)
    ink = cv2.adaptiveThreshold(
        study,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=35,
        C=12,
    )
    fraction = float((ink > 0).mean())
    info = {
        "frame": [x0, y0, x1, y1],
        "frame_detector": detector,
        "gutter": [int(gx0), int(gx1)],
        "ink_fraction": round(fraction, 5),
        "blank": fraction < champion.prep.BLANK_INK_FRACTION,
    }
    if info["blank"]:
        return None, info
    return page, info


def separate(raw_image: np.ndarray) -> dict:
    return champion.separate(raw_image, prepare_fn=prepare)
