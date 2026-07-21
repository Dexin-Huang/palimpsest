"""Stage the raw material for the binding ground truth (step one: look).

For a handful of columns: the column photograph, every harvested crop in
reading order with its CLAIMED character, and labels.csv with an empty
verdict column. No scoring, no filtering — this is the evidence a human
(or a second set of eyes) judges, and the filled CSV becomes the slot's
permanent gold fixture.
"""

from __future__ import annotations

import csv
import html
import importlib.util
import json
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).parent
GT = HERE / "out" / "ground_truth"
COLUMNS_PER_PAGE = 3
PAGES = ("page_0000", "page_0001")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    challenger = load("ap_candidate", HERE / "candidate.py")
    m2 = load("candidate", HERE.parent / "m2_exemplars" / "candidate.py")
    (GT / "img").mkdir(parents=True, exist_ok=True)

    rows = []
    sections = []
    for pid in PAGES:
        image = cv2.imread(str(challenger.DOC / "page_image_clean" / f"{pid}.jpg"))
        text = json.loads(
            (challenger.DOC / "page_transcription" / f"{pid}.json").read_text(
                encoding="utf-8"
            )
        )["text"]
        result = challenger.align_page(image, text.splitlines())
        boxed = [
            (i, col)
            for i, col in enumerate(result["columns"])
            if col["bbox"] and sum(1 for c in col["chars"] if c["bbox"]) >= 8
        ]
        boxed.sort(key=lambda item: -sum(1 for c in item[1]["chars"] if c["bbox"]))
        for col_index, column in sorted(boxed[:COLUMNS_PER_PAGE]):
            x, y, w, h = column["bbox"]
            pad = 14
            strip = image[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
            strip_name = f"{pid}_col{col_index:02d}.png"
            cv2.imwrite(str(GT / "img" / strip_name), strip)

            entries = []
            for pos, char in enumerate(column["chars"]):
                crop_id = f"{pid}_c{col_index:02d}_p{pos:02d}"
                crop_name = clean_name = ""
                junk = None
                if char["bbox"]:
                    cx, cy, cw, ch = char["bbox"]
                    cpad = int(max(cw, ch) * 0.1)
                    crop = image[max(0, cy - cpad):cy + ch + cpad,
                                 max(0, cx - cpad):cx + cw + cpad]
                    crop_name = f"{crop_id}.png"
                    cv2.imwrite(str(GT / "img" / crop_name), crop)
                    ink, _, junk = m2.clean_crop(image, char["bbox"])
                    if ink is not None:
                        import numpy as np
                        clean_name = f"{crop_id}_clean.png"
                        cv2.imwrite(str(GT / "img" / clean_name),
                                    255 - ink)
                rows.append({
                    "crop_id": crop_id, "page": pid, "column": col_index,
                    "position": pos, "claimed_char": char["ch"],
                    "method": char["method"],
                    "confidence": char["confidence"],
                    "verdict": f"auto_junk:{junk}" if junk else "",
                    "note": "",
                })
                entries.append((pos, char, crop_name, clean_name, junk))
            sections.append((pid, col_index, strip_name, entries))

    with (GT / "labels.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    parts = ["""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Binding ground truth — raw material</title><style>
 body{background:#faf7f0;color:#2b2620;font:16px/1.5 Charter,Georgia,serif;
      margin:2rem auto;max-width:78rem;padding:0 1rem}
 h1{font-size:1.5rem} h2{font-size:1.05rem;margin:2.2rem 0 .6rem;
    font-family:Consolas,monospace}
 .col{display:flex;gap:1.4rem;align-items:flex-start;margin-bottom:1rem}
 .strip img{height:900px;width:auto;border:1px solid #d9d2c4;background:#fff}
 table{border-collapse:collapse;font-size:.9rem}
 td,th{border-bottom:1px solid #e4ddd0;padding:.25rem .6rem;text-align:center;
       vertical-align:middle}
 th{font:600 .7rem/1.3 Consolas,monospace;text-transform:uppercase;color:#6b6257}
 .claim{font-size:2rem;line-height:1}
 td img{height:56px;width:56px;object-fit:contain;border:1px solid #d9d2c4;
        background:#fff}
 .none{color:#a09484;font-style:italic}
 .k{font:600 .72rem/1 Consolas,monospace;letter-spacing:.12em;
    text-transform:uppercase;color:#8a4b2d}
</style></head><body>
<div class="k">Palimpsest lab · raw material, unjudged</div>
<h1>Binding ground truth — step one: look</h1>
<p>Left: the column as photographed. Right: what the harvest cut, in
reading order (top→down), with the character it <em>claims</em> each crop
is. Verdicts go in <code>labels.csv</code> — the filled file becomes the
slot's permanent gold fixture.</p>"""]
    for pid, col_index, strip_name, entries in sections:
        parts.append(f"<h2>{pid} · column {col_index:02d}</h2>")
        parts.append('<div class="col">')
        parts.append(f'<div class="strip"><img src="img/{strip_name}"></div>')
        parts.append("<table><tr><th>pos</th><th>claimed</th><th>raw</th>"
                     "<th>cleaned</th><th>method</th><th>conf</th></tr>")
        for pos, char, crop_name, clean_name, junk in entries:
            crop_cell = (f'<img src="img/{crop_name}">' if crop_name
                         else '<span class="none">unbound</span>')
            if junk:
                clean_cell = f'<span class="none">junk: {junk}</span>'
            elif clean_name:
                clean_cell = f'<img src="img/{clean_name}">'
            else:
                clean_cell = ""
            parts.append(
                f"<tr><td>{pos}</td><td class='claim'>{html.escape(char['ch'])}"
                f"</td><td>{crop_cell}</td><td>{clean_cell}</td>"
                f"<td>{char['method']}</td><td>{char['confidence']}</td></tr>")
        parts.append("</table></div>")
    parts.append("</body></html>")
    (GT / "ground_truth.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(rows)} entries across {len(sections)} columns")
    print(GT / "ground_truth.html")
    print(GT / "labels.csv")


if __name__ == "__main__":
    main()
