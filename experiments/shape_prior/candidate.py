"""The language prior: match blobs shape-to-shape against ALL of Chinese.

We know the ink is Chinese, and Chinese is a finite shape inventory —
so "is this blob a character?" becomes "does it resemble ANY CJK glyph?"
Templates: every CJK Unified Ideograph the system Kai font can render
(~21k), pushed through the same feature pipeline as the crops. Uses:

1. junk gate: cells matching nothing Chinese (watermarks, stains) die
   without any deframing;
2. split/merge arbiter (staged next): pieces win iff more Chinese than
   the whole;
3. weak labels for free: the best-matching glyph is a label hypothesis.

A prior, not a verdict: scribal variants score lower than standard forms,
so thresholds are calibrated on the attested-crop distribution, and the
gate only kills what matches nothing at all. Ink primacy holds.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "out"
FONT = "C:/Windows/Fonts/simkai.ttf"
RENDER = 64
BLOCK = (0x4E00, 0xA000)  # CJK Unified Ideographs


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cluster_mod = load("cluster_mod", HERE.parent / "char_inventory" / "cluster.py")
sweep_mod = load("sweep_mod", HERE.parent / "separation_sweep" / "candidate.py")
refine_mod = sweep_mod.refine_mod


def build_templates() -> tuple[np.ndarray, list[str]]:
    cache_f, cache_c = OUT / "templates.npy", OUT / "template_chars.txt"
    if cache_f.exists() and cache_c.exists():
        return (np.load(cache_f),
                cache_c.read_text(encoding="utf-8").splitlines())
    font = ImageFont.truetype(FONT, RENDER)
    feats, chars = [], []
    for code in range(*BLOCK):
        ch = chr(code)
        img = Image.new("L", (RENDER + 16, RENDER + 16), 255)
        ImageDraw.Draw(img).text((8, 8), ch, font=font, fill=0)
        arr = np.asarray(img)
        if (arr < 128).sum() < 12:
            continue  # font cannot render it
        f = cluster_mod.feature(arr)
        if f is not None:
            feats.append(f)
            chars.append(ch)
    matrix = np.stack(feats).astype(np.float32)
    OUT.mkdir(parents=True, exist_ok=True)
    np.save(cache_f, matrix)
    cache_c.write_text("\n".join(chars), encoding="utf-8")
    return matrix, chars


def chineseness(features: np.ndarray, templates: np.ndarray,
                chunk: int = 4000) -> tuple[np.ndarray, np.ndarray]:
    best = np.full(len(features), -1.0, np.float32)
    arg = np.zeros(len(features), np.int64)
    for start in range(0, len(templates), chunk):
        sims = features @ templates[start:start + chunk].T
        piece_best = sims.max(axis=1)
        improve = piece_best > best
        arg[improve] = sims.argmax(axis=1)[improve] + start
        best[improve] = piece_best[improve]
    return best, arg


def crop_features(images: list[np.ndarray]) -> tuple[np.ndarray, list[int]]:
    feats, kept = [], []
    for i, img in enumerate(images):
        f = cluster_mod.feature(img)
        if f is not None:
            feats.append(f)
            kept.append(i)
    return np.stack(feats).astype(np.float32), kept


def build_latin() -> np.ndarray:
    """The negative model: what watermarks and banners are made of."""
    feats = []
    for name in ("arial.ttf", "times.ttf", "georgia.ttf"):
        try:
            font = ImageFont.truetype(f"C:/Windows/Fonts/{name}", RENDER)
        except OSError:
            continue
        for ch in ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "abcdefghijklmnopqrstuvwxyz0123456789&©"):
            img = Image.new("L", (RENDER + 16, RENDER + 16), 255)
            ImageDraw.Draw(img).text((8, 8), ch, font=font, fill=0)
            f = cluster_mod.feature(np.asarray(img))
            if f is not None:
                feats.append(f)
    return np.stack(feats).astype(np.float32)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    templates, chars = build_templates()
    latin = build_latin()
    print(f"template inventory: {len(chars)} CJK glyphs, {len(latin)} Latin")

    # population A: the manuscript inventory (real characters, mostly)
    inv_paths = sorted((HERE.parent / "char_inventory" / "out" / "crops").glob("*.png"))
    inv_images = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for p in inv_paths]
    inv_feats, inv_kept = crop_features(inv_images)
    inv_cjk, inv_arg = chineseness(inv_feats, templates)
    inv_lat, _ = chineseness(inv_feats, latin)
    inv_score = inv_cjk - inv_lat

    # population B: cells found on the watermarked blank page
    blank = cv2.imread(str(HERE.parents[1] / "library" / "vatican_borg_cin_398"
                           / "images" / "f076r.jpg"))
    scale = 3200 / max(blank.shape[:2])
    if scale < 1:
        blank = cv2.resize(blank, None, fx=scale, fy=scale)
    metrics = sweep_mod.separate(blank)
    blank_crops = []
    for cell in metrics["_cells"]:
        ink, _, junk = refine_mod.m2.clean_crop(blank, cell.bbox())
        if not junk and ink is not None:
            blank_crops.append(255 - ink)
    blank_feats, _ = crop_features(blank_crops)
    blank_cjk, _ = chineseness(blank_feats, templates)
    blank_lat, _ = chineseness(blank_feats, latin)
    blank_score = blank_cjk - blank_lat

    q = lambda a, p: float(np.percentile(a, p))
    print(f"manuscript margin  n={len(inv_score)}  "
          f"median {np.median(inv_score):+.3f}  p10 {q(inv_score,10):+.3f}")
    print(f"watermark margin   n={len(blank_score)}  "
          f"median {np.median(blank_score):+.3f}  p90 {q(blank_score,90):+.3f}")
    threshold = q(inv_score, 5)
    killed = float((blank_score < threshold).mean()) if len(blank_score) else 0.0
    survive = float((inv_score >= threshold).mean())
    print(f"gate at manuscript-p5 ({threshold:.3f}): kills "
          f"{killed:.0%} of watermark cells, keeps {survive:.0%} of manuscript")

    # match sheet: crop | best-matching Kai glyph, for 16 random crops
    rng = np.random.default_rng(1)
    font = ImageFont.truetype(FONT, 44)
    picks = rng.choice(len(inv_kept), 16, replace=False)
    tiles = []
    for i in picks:
        crop_img = inv_images[inv_kept[i]]
        ink = 255 - crop_img
        ys, xs = np.nonzero(ink > 127)
        tight = 255 - ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        left = cv2.resize(tight, (52, 52), interpolation=cv2.INTER_AREA)
        glyph = Image.new("L", (52, 52), 255)
        ImageDraw.Draw(glyph).text((4, 2), chars[inv_arg[i]], font=font, fill=0)
        pair = np.hstack([np.pad(left, 2, constant_values=100),
                          np.pad(np.asarray(glyph), 2, constant_values=180)])
        tiles.append(pair)
    rows = [np.hstack(tiles[r * 4:(r + 1) * 4]) for r in range(4)]
    cv2.imwrite(str(OUT / "match_sheet.png"),
                cv2.resize(np.vstack(rows), None, fx=2, fy=2,
                           interpolation=cv2.INTER_NEAREST))
    print(OUT / "match_sheet.png")


if __name__ == "__main__":
    main()
