"""Challenger for the align slot: DTW column pairing (see ../LOG.md).

The incumbent zips detected image columns to transcription lines by order,
so one missed column mislabels every column after it — caught by the
exemplar purity audit (0.2%). This candidate pairs columns to lines with
dynamic programming on cell-count vs char-count, so a missed column costs
one skip instead of shifting the world. Judged by the same purity audit.

Reuses every geometric primitive from the production glyphs module; the
diff IS the pairing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parents[1] / "m2_exemplars"))
import candidate as m2  # the harvest + audit + contact sheet  # noqa: E402

from palimpsest.factory import glyphs  # noqa: E402

OUT = Path(__file__).parent / "out"
DOC = m2.DOC
PAGES = ("page_0000", "page_0001", "page_0002")


def pair_columns(
    columns: list[list["glyphs.Cell"]], char_lines: list[list[str]]
) -> list[tuple[int | None, int | None]]:
    """DP over (column index, line index): match on count agreement, or
    skip a spurious band / an undetected column at fixed cost."""
    n, m = len(columns), len(char_lines)
    skip = 0.6
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    move: dict[tuple[int, int], tuple[int, int, str]] = {}
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == INF:
                continue
            if i < n and here + skip < cost[i + 1][j]:
                cost[i + 1][j] = here + skip
                move[(i + 1, j)] = (i, j, "skip_col")
            if j < m and here + skip < cost[i][j + 1]:
                cost[i][j + 1] = here + skip
                move[(i, j + 1)] = (i, j, "skip_line")
            if i < n and j < m:
                cells, chars = len(columns[i]), len(char_lines[j])
                fit = abs(cells - chars) / max(cells, chars, 1)
                if here + fit < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1] = here + fit
                    move[(i + 1, j + 1)] = (i, j, "match")
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, op = move[(i, j)]
        if op == "match":
            pairs.append((pi, pj))
        elif op == "skip_col":
            pairs.append((pi, None))
        else:
            pairs.append((None, pj))
        i, j = pi, pj
    pairs.reverse()
    return pairs


def align_page(image, lines: list[str]) -> dict:
    """Identical to production align_page except column↔line pairing."""
    mask = glyphs.binarize(image)
    rough = glyphs.ink_blobs(mask)
    if not rough or not lines:
        return {"columns": [], "stats": {}}
    pitch = glyphs._column_pitch(mask)
    glyph_h = (
        pitch * glyphs._GLYPH_OF_PITCH if pitch else glyphs._main_glyph_height(rough)
    )
    fuse = max(3, int(glyph_h * 0.15))
    import numpy as np

    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((fuse, fuse), np.uint8))
    blobs = glyphs.ink_blobs(closed)
    main = [b for b in blobs if b.h >= glyph_h * glyphs._SMALL_GLYPH_FRAC]
    bands = glyphs.column_bands(closed, glyph_h)
    columns = [c for c in (glyphs._band_cells(main, b, glyph_h) for b in bands) if c]
    columns.sort(key=lambda col: -max(c.x1 for c in col))
    char_lines = [glyphs._ink_chars(line) for line in lines]

    out_columns = []
    matched = 0
    for col_index, line_index in pair_columns(columns, char_lines):
        if line_index is None:
            continue  # spurious band: no transcription, nothing to bind
        if col_index is None:
            out_columns.append(
                glyphs._column_payload(
                    [(ch, None, 0.0, "none") for ch in char_lines[line_index]]
                )
            )
            continue
        matched += 1
        out_columns.append(
            glyphs._column_payload(
                glyphs.align_column(columns[col_index], char_lines[line_index], glyph_h)
            )
        )
    boxed = sum(1 for col in out_columns for c in col["chars"] if c["bbox"])
    return {
        "columns": out_columns,
        "stats": {
            "transcribed": sum(len(line) for line in char_lines),
            "boxed": boxed,
            "image_columns": len(columns),
            "matched_pairs": matched,
        },
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    m2.OUT = OUT  # audit artifacts land in this experiment's corner
    import json

    alignments = {}
    for pid in PAGES:
        image = cv2.imread(str(DOC / "page_image_clean" / f"{pid}.jpg"))
        text = json.loads(
            (DOC / "page_transcription" / f"{pid}.json").read_text(encoding="utf-8")
        )["text"]
        result = align_page(image, text.splitlines())
        result["page_id"] = pid
        alignments[pid] = result
        s = result["stats"]
        print(
            f"{pid}: boxed={s['boxed']}/{s['transcribed']} "
            f"cols={s['image_columns']} matched={s['matched_pairs']}"
        )

    instances = m2.harvest(alignments)
    report = m2.audit(instances)
    sheet, frequent = m2.contact_sheet(instances)
    counts = [len(i) for i in instances.values()]
    print(
        f"characters: {len(instances)} unique, {sum(counts)} instances "
        f"(multi-instance {sum(1 for c in counts if c > 1)})"
    )
    print(
        f"purity (leave-one-out NN): {report['purity']:.1%} "
        f"over {report['audited']} audited instances"
    )
    print(
        f"intra-class similarity {report['intra_mean']:.3f} vs "
        f"inter-class {report['inter_mean']:.3f}"
    )
    print(f"contact sheet ({''.join(frequent)}): {sheet}")


if __name__ == "__main__":
    main()
