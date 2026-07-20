"""Forced alignment on synthetic pages with known geometry."""

import numpy as np

from palimpsest.factory.glyphs import align_page

GLYPH = 40  # px, synthetic character size
PITCH = 70  # vertical pitch between characters
COL_PITCH = 90


def _page(columns: list[int]) -> np.ndarray:
    """White page with solid square 'characters'; columns right-to-left."""
    height = 60 + PITCH * max(columns) + GLYPH
    width = 60 + COL_PITCH * len(columns)
    img = np.full((height, width, 3), 255, np.uint8)
    for c, count in enumerate(columns):
        x = width - 60 - COL_PITCH * c - GLYPH
        for r in range(count):
            y = 40 + PITCH * r
            img[y : y + GLYPH, x : x + GLYPH] = 0
    return img


def test_clean_grid_aligns_fully():
    img = _page([5, 5, 5])
    result = align_page(img, ["甲乙丙丁戊", "己庚辛壬癸", "子丑寅卯辰"])
    stats = result["stats"]
    assert stats["boxed"] == stats["transcribed"] == 15
    assert stats["count_mismatch_columns"] == 0
    # rightmost image column carries the first transcription line, top-down
    first = result["columns"][0]["chars"]
    assert [c["ch"] for c in first] == list("甲乙丙丁戊")
    ys = [c["bbox"][1] for c in first]
    assert ys == sorted(ys)


def test_multistroke_characters_fuse_into_one_box():
    # eight columns give the page its periodic column signal; the first
    # (rightmost) column's characters are split into two thin strokes each
    img = _page([3] * 8)
    x = img.shape[1] - 60 - GLYPH
    for r in range(3):
        y = 40 + PITCH * r
        img[y : y + GLYPH, x : x + GLYPH] = 255
        img[y : y + 14, x : x + GLYPH] = 0
        img[y + GLYPH - 14 : y + GLYPH, x : x + GLYPH] = 0
    lines = ["一二三"] + ["甲乙丙"] * 7
    result = align_page(img, lines)
    striped = result["columns"][0]["chars"]
    assert all(c["bbox"] for c in striped)
    for char in striped:
        assert char["bbox"][3] > 20  # box spans both strokes, not one


def test_missing_ink_is_marked_not_forced():
    img = _page([4])  # four blobs on the page
    result = align_page(img, ["甲乙丙丁戊"])  # five transcribed characters
    chars = result["columns"][0]["chars"]
    assert sum(1 for c in chars if c["bbox"]) == 4
    assert sum(1 for c in chars if c["method"] == "none") == 1
    assert result["stats"]["count_mismatch_columns"] == 1


def test_extra_line_without_ink_stays_auditable():
    img = _page([2])
    result = align_page(img, ["甲乙", "丙丁"])
    second = result["columns"][1]["chars"]
    assert all(c["method"] == "none" for c in second)
