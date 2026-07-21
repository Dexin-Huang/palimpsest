"""Unsupervised clustering of the character inventory. No labels anywhere.

Features: blurred 32x32 ink density maps (shape, tolerant of stroke-width
and registration jitter). Clustering: leader pass + one refinement sweep,
cosine similarity. The proof is visual: each big cluster's row on the
sheet should be one character, discovered rather than told.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
CROPS = OUT / "crops"
CANVAS = 64
FEAT = 32
THRESHOLD = 0.78


def feature(ink_on_white: np.ndarray) -> np.ndarray | None:
    ink = 255 - ink_on_white
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
    soft = cv2.GaussianBlur(canvas, (0, 0), 2.2)
    small = cv2.resize(soft, (FEAT, FEAT)).flatten()
    norm = np.linalg.norm(small)
    return small / norm if norm > 0 else None


def cluster(features: np.ndarray, threshold: float) -> np.ndarray:
    """Leader clustering + one refinement sweep against cluster means."""
    n = len(features)
    labels = np.full(n, -1)
    centers: list[np.ndarray] = []
    members: list[list[int]] = []
    for i in range(n):
        if centers:
            sims = np.array([c @ features[i] for c in centers])
            best = int(np.argmax(sims))
            if sims[best] >= threshold:
                labels[i] = best
                members[best].append(i)
                centers[best] = features[members[best]].mean(axis=0)
                centers[best] /= np.linalg.norm(centers[best])
                continue
        labels[i] = len(centers)
        centers.append(features[i].copy())
        members.append([i])
    matrix = np.stack(centers)
    for sweep in range(2):
        sims = features @ matrix.T
        best = sims.argmax(axis=1)
        best_sim = sims[np.arange(len(features)), best]
        labels = np.where(best_sim >= threshold, best, labels)
        for k in range(len(centers)):
            mask = labels == k
            if mask.any():
                center = features[mask].mean(axis=0)
                matrix[k] = center / np.linalg.norm(center)
    return labels


def main() -> None:
    paths = sorted(CROPS.glob("*.png"))
    images, feats, kept_paths = [], [], []
    for path in paths:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        f = feature(img)
        if f is not None:
            images.append(img)
            feats.append(f)
            kept_paths.append(path.stem)
    features = np.stack(feats)
    labels = cluster(features, THRESHOLD)

    sizes = np.bincount(labels)
    order = np.argsort(-sizes)
    big = [k for k in order if sizes[k] >= 2]
    print(f"crops: {len(features)}  clusters: {len(sizes)}  "
          f"multi-member: {len(big)}  singletons: {(sizes == 1).sum()}")
    print("top cluster sizes:", [int(sizes[k]) for k in order[:12]])

    rows = []
    width = int(min(30, sizes[order[0]]))
    for k in order[:14]:
        idx = np.where(labels == k)[0][:width]
        tiles = []
        for i in idx:
            ink = 255 - images[i]
            ys, xs = np.nonzero(ink > 127)
            tight = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            tile = 255 - cv2.resize(tight, (48, 48), interpolation=cv2.INTER_AREA)
            tiles.append(np.pad(tile, 2, constant_values=140))
        while len(tiles) < width:
            tiles.append(np.full((52, 52), 220, np.uint8))
        rows.append(np.hstack(tiles))
    sheet = np.vstack(rows)
    cv2.imwrite(str(OUT / "cluster_sheet.png"),
                cv2.resize(sheet, None, fx=1.6, fy=1.6,
                           interpolation=cv2.INTER_NEAREST))
    np.save(OUT / "cluster_labels.npy", labels)
    (OUT / "cluster_ids.txt").write_text(
        "\n".join(f"{p} {l}" for p, l in zip(kept_paths, labels)),
        encoding="utf-8")
    print(OUT / "cluster_sheet.png")


if __name__ == "__main__":
    main()
