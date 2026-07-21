"""The scribe's font: a TTF cut from P.3477's own ink (GLYPHS.md M3).

Labels by consensus, never by trust: crops from all three folios are
clustered (unsupervised, v2 features); a cluster becomes a font glyph
only when its members' alignment claims AGREE (majority >= 0.6, n >= 2).
The cleanest member's ink is vectorized (contours + holes) into a
TrueType glyph at that character's codepoint. Output: hand_font.ttf +
a specimen sheet rendering manuscript text in the scribe's own hand
beside the same text in Kai.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
DOC = HERE.parents[1] / "library" / "gallica_pelliot_chinois_3477"
PAGES = ("page_0000", "page_0001", "page_0002")
CLUSTER_THRESHOLD = 0.62
AGREE_FLOOR = 0.6
UPM = 1000
BOX = (70, -40, 930, 880)  # glyph box in font units (x0, y0, x1, y1)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ap = load("ap_candidate", HERE.parent / "align_pairing" / "candidate.py")
features = load("features", HERE.parent / "separation2" / "features.py")
cluster_mod = load("cluster_mod", HERE.parent / "char_inventory" / "cluster.py")
refine_mod = ap.glyphs  # noqa: F841  (loaded transitively)
m2 = load("m2c", HERE.parent / "m2_exemplars" / "candidate.py")


def harvest() -> tuple[list[np.ndarray], list[str], np.ndarray]:
    masks, claims, feats = [], [], []
    for pid in PAGES:
        image = cv2.imread(str(DOC / "page_image_clean" / f"{pid}.jpg"))
        text = json.loads((DOC / "page_transcription" / f"{pid}.json")
                          .read_text(encoding="utf-8"))["text"]
        result = ap.align_page(image, text.splitlines())
        for column in result["columns"]:
            for char in column["chars"]:
                if not char["bbox"] or char["confidence"] < 0.5:
                    continue
                ink, _, junk = m2.clean_crop(image, char["bbox"])
                if junk or ink is None:
                    continue
                f = features.feature_grad(
                    np.where(ink, np.uint8(0), np.uint8(255)))
                if f is None:
                    continue
                masks.append(ink)
                claims.append(char["ch"])
                feats.append(f)
    return masks, claims, np.stack(feats).astype(np.float32)


def consensus_glyphs(masks, claims, feats) -> dict[str, np.ndarray]:
    labels = cluster_mod.cluster(feats, CLUSTER_THRESHOLD)
    by_cluster = defaultdict(list)
    for i, k in enumerate(labels):
        by_cluster[int(k)].append(i)
    chosen: dict[str, tuple[float, np.ndarray]] = {}
    for members in by_cluster.values():
        if len(members) < 2:
            continue
        votes = Counter(claims[i] for i in members)
        char, count = votes.most_common(1)[0]
        agree = count / len(members)
        if agree < AGREE_FLOOR:
            continue
        core = [i for i in members if claims[i] == char]
        center = feats[core].mean(axis=0)
        center /= np.linalg.norm(center)
        champion = core[int(np.argmax(feats[core] @ center))]
        score = agree * len(core)
        if char not in chosen or score > chosen[char][0]:
            chosen[char] = (score, masks[champion])
    return {ch: mask for ch, (_, mask) in chosen.items()}


def glyph_contours(mask: np.ndarray) -> list[np.ndarray]:
    big = cv2.resize(mask.astype(np.uint8) * 255, None, fx=4, fy=4,
                     interpolation=cv2.INTER_NEAREST)
    big = cv2.morphologyEx(big, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(big, cv2.RETR_CCOMP,
                                   cv2.CHAIN_APPROX_TC89_KCOS)
    out = []
    for contour in contours:
        if cv2.contourArea(contour) < 40:
            continue
        eps = 0.006 * cv2.arcLength(contour, True)
        out.append(cv2.approxPolyDP(contour, eps, True).reshape(-1, 2))
    return out


def build_font(glyphs: dict[str, np.ndarray], path: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef"] + [f"uni{ord(c):04X}" for c in sorted(glyphs)]
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): f"uni{ord(c):04X}" for c in glyphs})
    glyf, metrics = {}, {}
    pen = TTGlyphPen(None)
    pen.closePath() if False else None
    empty = TTGlyphPen(None).glyph()
    glyf[".notdef"] = empty
    metrics[".notdef"] = (UPM, 0)
    x0, y0, x1, y1 = BOX
    for ch, mask in glyphs.items():
        contours = glyph_contours(mask)
        if not contours:
            continue
        pts = np.vstack(contours)
        mnx, mny = pts.min(axis=0)
        mxx, mxy = pts.max(axis=0)
        span = max(mxx - mnx, mxy - mny, 1)
        scale = (x1 - x0 if (mxx - mnx) >= (mxy - mny) else y1 - y0) / span
        pen = TTGlyphPen(None)
        for contour in contours:
            first = True
            for px, py in contour:
                fx = x0 + (px - mnx) * scale
                fy = y1 - (py - mny) * scale  # image y-down -> font y-up
                if first:
                    pen.moveTo((round(fx), round(fy)))
                    first = False
                else:
                    pen.lineTo((round(fx), round(fy)))
            pen.closePath()
        name = f"uni{ord(ch):04X}"
        glyf[name] = pen.glyph()
        metrics[name] = (UPM, x0)
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=880, descent=-120)
    fb.setupOS2(sTypoAscender=880, sTypoDescender=-120)
    fb.setupNameTable({"familyName": "P3477 Hand", "styleName": "Regular",
                       "fullName": "P3477 Hand (Dunhuang scribe)",
                       "psName": "P3477Hand-Regular"})
    fb.setupPost()
    fb.save(str(path))


def specimen(glyphs: dict[str, np.ndarray], font_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    lines = ["玄感脉經一卷", "三部者謂寸口爲上部", "脉之大會手太陰之動也"]
    covered_lines = []
    for line in lines:
        covered_lines.append("".join(c for c in line if c in glyphs) or None)
    pool = sorted(glyphs, key=lambda c: c)  # deterministic filler line
    covered_lines.append("".join(pool[:14]))
    hand = ImageFont.truetype(str(font_path), 72)
    kai = ImageFont.truetype(features.FONT, 72)
    img = Image.new("L", (1200, 120 * len(covered_lines) * 2 + 40), 255)
    draw = ImageDraw.Draw(img)
    y = 20
    for line in covered_lines:
        if not line:
            continue
        draw.text((30, y), line, font=hand, fill=0)
        draw.text((30, y + 92), line, font=kai, fill=120)
        y += 214
    path = OUT / "specimen.png"
    img.save(path)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    masks, claims, feats = harvest()
    print(f"harvested {len(masks)} labeled crops")
    glyphs = consensus_glyphs(masks, claims, feats)
    print(f"consensus characters: {len(glyphs)}")
    font_path = OUT / "P3477-Hand.ttf"
    build_font(glyphs, font_path)
    print(f"font written: {font_path} "
          f"({font_path.stat().st_size // 1024} KB)")
    print("specimen:", specimen(glyphs, font_path))
    (OUT / "coverage.txt").write_text(
        "".join(sorted(glyphs)), encoding="utf-8")


if __name__ == "__main__":
    main()
