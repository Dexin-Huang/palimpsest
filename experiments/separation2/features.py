"""Shape features for glyph matching — the bottleneck component.

v1: blurred density map (the incumbent from clustering/shape_prior).
v2: gradient-orientation histograms (HOG-like) + coarse density.

Self-test (label-free, run as main): render the CJK inventory in Kai,
perturb a sample toward manuscript conditions (blur, thickness change,
small rotation), and measure top-1 retrieval against the full 21k
gallery in each feature space. The feature that survives distortion
best wins the slot.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

CANVAS = 64
BLOCK = (0x4E00, 0xA000)
FONT = "C:/Windows/Fonts/simkai.ttf"


def tight_canvas(ink_on_white: np.ndarray) -> np.ndarray | None:
    ink = 255 - ink_on_white if ink_on_white.dtype == np.uint8 else ink_on_white
    ys, xs = np.nonzero(ink > 127)
    if ys.size < 12:
        return None
    tight = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    scale = (CANVAS - 8) / max(tight.shape)
    resized = cv2.resize(tight, (max(1, int(tight.shape[1] * scale)),
                                 max(1, int(tight.shape[0] * scale))))
    canvas = np.zeros((CANVAS, CANVAS), np.float32)
    oy = (CANVAS - resized.shape[0]) // 2
    ox = (CANVAS - resized.shape[1]) // 2
    canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized
    return canvas


def feature_density(gray: np.ndarray) -> np.ndarray | None:
    canvas = tight_canvas(gray)
    if canvas is None:
        return None
    soft = cv2.GaussianBlur(canvas, (0, 0), 2.2)
    vec = cv2.resize(soft, (32, 32)).flatten()
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else None


def feature_grad(gray: np.ndarray, bins: int = 8, cell: int = 8) -> np.ndarray | None:
    canvas = tight_canvas(gray)
    if canvas is None:
        return None
    canvas = cv2.GaussianBlur(canvas / 255.0, (0, 0), 1.0)
    gx = cv2.Sobel(canvas, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(canvas, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    ang = np.mod(np.arctan2(gy, gx), np.pi)  # unsigned orientation
    bin_index = np.minimum((ang / np.pi * bins).astype(np.int32), bins - 1)
    grid = CANVAS // cell
    hist = np.zeros((grid, grid, bins), np.float32)
    cy, cx = np.mgrid[0:CANVAS, 0:CANVAS]
    np.add.at(hist, (cy // cell, cx // cell, bin_index), mag)
    hist = hist.reshape(grid * grid, bins)
    hist /= np.linalg.norm(hist, axis=1, keepdims=True) + 1e-6  # per-cell
    vec = hist.flatten()
    density = cv2.resize(canvas, (16, 16)).flatten() * 0.5
    out = np.concatenate([vec, density])
    norm = np.linalg.norm(out)
    return out / norm if norm > 0 else None


def render_glyph(font, ch: str, size: int = CANVAS) -> np.ndarray:
    from PIL import Image, ImageDraw
    img = Image.new("L", (size + 16, size + 16), 255)
    ImageDraw.Draw(img).text((8, 8), ch, font=font, fill=0)
    return np.asarray(img)


def perturb(gray: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Toward manuscript conditions: stroke-weight drift, blur, small tilt."""
    img = gray.copy()
    k = int(rng.integers(0, 3))
    if k:
        kernel = np.ones((k + 1, k + 1), np.uint8)
        img = cv2.erode(img, kernel) if rng.random() < 0.5 else cv2.dilate(img, kernel)
    angle = float(rng.uniform(-5, 5))
    h, w = img.shape
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    img = cv2.warpAffine(img, rot, (w, h), borderValue=255)
    return cv2.GaussianBlur(img, (0, 0), float(rng.uniform(0.6, 1.8)))


def main() -> None:
    from PIL import ImageFont
    font = ImageFont.truetype(FONT, CANVAS)
    rng = np.random.default_rng(7)
    glyphs, chars = [], []
    for code in range(*BLOCK):
        arr = render_glyph(font, chr(code))
        if (arr < 128).sum() >= 12:
            glyphs.append(arr)
            chars.append(chr(code))
    sample = rng.choice(len(glyphs), 2000, replace=False)

    for name, fn in (("v1 density", feature_density), ("v2 gradient", feature_grad)):
        gallery, keep = [], []
        for i, g in enumerate(glyphs):
            f = fn(g)
            if f is not None:
                gallery.append(f)
                keep.append(i)
        gallery = np.stack(gallery).astype(np.float32)
        row_of = {orig: row for row, orig in enumerate(keep)}
        queries, truth = [], []
        for i in sample:
            if i not in row_of:
                continue
            f = fn(perturb(glyphs[i], rng))
            if f is not None:
                queries.append(f)
                truth.append(row_of[i])
        queries = np.stack(queries).astype(np.float32)
        hits = 0
        for start in range(0, len(queries), 256):
            sims = queries[start:start + 256] @ gallery.T
            hits += int((sims.argmax(axis=1)
                         == np.array(truth[start:start + 256])).sum())
        print(f"{name}: top-1 retrieval {hits}/{len(queries)} "
              f"({hits / len(queries):.1%}) over {len(gallery)} gallery glyphs, "
              f"dim {gallery.shape[1]}")


if __name__ == "__main__":
    main()
