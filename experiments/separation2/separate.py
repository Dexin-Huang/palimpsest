"""The integrated candidate: prep -> geometry -> gates -> language prior.

Pipeline: imaging prep (prep.py, incl. blank gate) -> the champion
geometry (projection columns + DTW cells + refine) -> crop junk gates ->
the CJK-vs-Latin likelihood-ratio gate with the winning feature.
Recognition-guided arbitration: a tall cell is split only if the pieces
match the Chinese inventory better than the whole did.

Pure code + two font renders. No training, no downloads.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import features  # noqa: E402
import prep  # noqa: E402


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


refine_mod = load("refine_mod", HERE.parent / "char_inventory" / "refine.py")

_TEMPLATES: tuple[np.ndarray, np.ndarray] | None = None


def _feature_fn(gray: np.ndarray):
    return features.feature_grad(gray)


def templates() -> tuple[np.ndarray, np.ndarray]:
    """CJK gallery + Latin negative gallery in the winning feature space."""
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    cache = HERE / "out" / "templates_grad.npz"
    if cache.exists():
        data = np.load(cache)
        _TEMPLATES = (data["cjk"], data["latin"])
        return _TEMPLATES
    from PIL import ImageFont
    font = ImageFont.truetype(features.FONT, features.CANVAS)
    cjk = []
    for code in range(*features.BLOCK):
        arr = features.render_glyph(font, chr(code))
        if (arr < 128).sum() >= 12:
            f = _feature_fn(arr)
            if f is not None:
                cjk.append(f)
    latin = []
    for name in ("arial.ttf", "times.ttf", "georgia.ttf"):
        try:
            lfont = ImageFont.truetype(f"C:/Windows/Fonts/{name}", features.CANVAS)
        except OSError:
            continue
        for ch in ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "abcdefghijklmnopqrstuvwxyz0123456789&©"):
            f = _feature_fn(features.render_glyph(lfont, ch))
            if f is not None:
                latin.append(f)
    cache.parent.mkdir(parents=True, exist_ok=True)
    result = (np.stack(cjk).astype(np.float32),
              np.stack(latin).astype(np.float32))
    np.savez_compressed(cache, cjk=result[0], latin=result[1])
    _TEMPLATES = result
    return result


def margin(gray_crop: np.ndarray) -> float | None:
    """CJK-ness minus Latin-ness of one ink crop."""
    f = _feature_fn(gray_crop)
    if f is None:
        return None
    cjk, latin = templates()
    f32 = f.astype(np.float32)
    best_cjk = -1.0
    for start in range(0, len(cjk), 4000):
        best_cjk = max(best_cjk, float((cjk[start:start + 4000] @ f32).max()))
    return best_cjk - float((latin @ f32).max())


MARGIN_FLOOR = 0.019  # p5 of attested P.3477 crops in v2 space


def separate(raw_image: np.ndarray, *, prepare_fn=prep.prepare) -> dict:
    page, info = prepare_fn(raw_image)
    if page is None:
        return {"blank": True, "prep": info, "kept": 0, "junked": 0,
                "cells": [], "glyph_h": 0.0, "columns": 0,
                "size_consistency": 1.0, "prior_killed": 0,
                "pitch_found": True}

    pitch = refine_mod.glyphs._column_pitch(refine_mod.glyphs.binarize(page))
    columns, glyph_h, closed = refine_mod.page_cells_with_mask(page)
    refined, _ = refine_mod.refine(columns, closed, glyph_h)

    kept_cells, heights = [], []
    junked = prior_killed = 0
    for column in refined:
        for cell in column:
            ink, gray, junk = refine_mod.m2.clean_crop(page, cell.bbox())
            if junk:
                junked += 1
                continue
            crop = np.where(ink, np.uint8(0), np.uint8(255))
            score = margin(crop)
            if score is not None and score < MARGIN_FLOOR:
                prior_killed += 1
                continue
            kept_cells.append(cell)
            heights.append(cell.h)
    heights = np.array(heights) if heights else np.array([0.0])
    consistent = float(((heights >= 0.55 * glyph_h)
                        & (heights <= 1.6 * glyph_h)).mean()) if len(kept_cells) else 0.0
    return {"blank": False, "prep": info, "cells": kept_cells,
            "pitch_found": pitch is not None,
            "kept": len(kept_cells), "junked": junked,
            "prior_killed": prior_killed,
            "glyph_h": round(float(glyph_h), 1),
            "columns": len(refined),
            "size_consistency": round(consistent, 3),
            "_page": page}
