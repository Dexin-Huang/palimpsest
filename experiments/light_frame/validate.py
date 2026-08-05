"""Compare separation2 with the light-backdrop framing challenger."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "out"
MANIFEST = HERE.parent / "testset" / "manifest.json"
FOLIOS = HERE.parent / "testset" / "folios"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


champion = load("light_frame_validation_champion", HERE.parent / "separation2" / "separate.py")
challenger = load("light_frame_validation_challenger", HERE / "candidate.py")


def corpus_of(doc: str) -> str:
    for key in ("gallica", "idp", "borg_cin", "estr_or"):
        if key in doc:
            return key
    return "other"


def health(metrics: dict) -> float:
    if metrics.get("blank"):
        return 1.0
    junk_share = metrics.get("junked", 0) / max(
        1, metrics["kept"] + metrics.get("junked", 0)
    )
    pitch_factor = 1.0 if metrics.get("pitch_found") else 0.6
    return metrics["size_consistency"] * (1 - junk_share) * pitch_factor


def fit(image: np.ndarray, *, max_width: int, max_height: int) -> np.ndarray:
    scale = min(max_width / image.shape[1], max_height / image.shape[0], 1.0)
    if scale == 1.0:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def pad_to(image: np.ndarray, *, width: int, height: int) -> np.ndarray:
    canvas = np.full((height, width, 3), 245, dtype=np.uint8)
    canvas[: image.shape[0], : image.shape[1]] = image
    return canvas


def comparison_image(raw: np.ndarray, base: dict, cand: dict) -> np.ndarray:
    frame_view = raw.copy()
    bx0, by0, bx1, by1 = base["prep"]["frame"]
    cx0, cy0, cx1, cy1 = cand["prep"]["frame"]
    thickness = max(2, round(max(raw.shape[:2]) / 700))
    cv2.rectangle(frame_view, (bx0, by0), (bx1, by1), (50, 70, 220), thickness)
    cv2.rectangle(frame_view, (cx0, cy0), (cx1, cy1), (50, 180, 60), thickness)
    cv2.putText(
        frame_view,
        "champion frame",
        (20, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (50, 70, 220),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame_view,
        "challenger frame",
        (20, 82),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (50, 180, 60),
        3,
        cv2.LINE_AA,
    )
    frame_view = fit(frame_view, max_width=1800, max_height=700)

    panels = []
    for label, metrics, color in (
        ("champion cells", base, (50, 70, 220)),
        ("challenger cells", cand, (50, 180, 60)),
    ):
        panel = metrics.get("_page", raw).copy()
        for cell in metrics.get("cells", []):
            x, y, width, height = cell.bbox()
            cv2.rectangle(panel, (x, y), (x + width, y + height), color, 2)
        cv2.putText(
            panel,
            label,
            (20, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            color,
            3,
            cv2.LINE_AA,
        )
        panels.append(fit(panel, max_width=900, max_height=900))
    panel_height = max(panel.shape[0] for panel in panels)
    panels = [pad_to(panel, width=panel.shape[1], height=panel_height) for panel in panels]
    cell_view = np.hstack(panels)

    width = max(frame_view.shape[1], cell_view.shape[1])
    frame_view = pad_to(frame_view, width=width, height=frame_view.shape[0])
    cell_view = pad_to(cell_view, width=width, height=cell_view.shape[0])
    return np.vstack((frame_view, cell_view))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["folios"]:
        path = FOLIOS / f"{entry['doc']}__{entry['folio']}.jpg"
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(path)
        scale = 3200 / max(image.shape[:2])
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale)

        base = champion.separate(image)
        cand = challenger.separate(image)
        base_health = health(base)
        cand_health = health(cand)
        detector = cand["prep"].get("frame_detector", {})
        row = {
            "corpus": corpus_of(entry["doc"]),
            "doc": entry["doc"],
            "folio": entry["folio"],
            "champ_health": round(base_health, 3),
            "challenger_health": round(cand_health, 3),
            "delta": round(cand_health - base_health, 3),
            "champ_kept": base["kept"],
            "challenger_kept": cand["kept"],
            "champ_blank": base.get("blank", False),
            "challenger_blank": cand.get("blank", False),
            "frame_method": detector.get("method", "unknown"),
            "champ_frame": json.dumps(base["prep"]["frame"]),
            "challenger_frame": json.dumps(cand["prep"]["frame"]),
            "_image": image,
            "_base": base,
            "_cand": cand,
        }
        rows.append(row)
        print(
            f"{entry['doc']}/{entry['folio']}: {base_health:.3f} -> "
            f"{cand_health:.3f} ({cand_health - base_health:+.3f}; "
            f"kept {base['kept']}->{cand['kept']}; {row['frame_method']})"
        )

    with (OUT / "validation.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [key for key in rows[0] if not key.startswith("_")]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print("\nper-corpus median health (champion -> challenger):")
    summaries = []
    for corpus in sorted({row["corpus"] for row in rows}):
        subset = [row for row in rows if row["corpus"] == corpus]
        base_median = float(np.median([row["champ_health"] for row in subset]))
        cand_median = float(np.median([row["challenger_health"] for row in subset]))
        summaries.append((corpus, len(subset), base_median, cand_median))
        print(
            f"  {corpus:10s} n={len(subset):2d}  "
            f"{base_median:.3f} -> {cand_median:.3f}"
        )
    base_all = float(np.median([row["champ_health"] for row in rows]))
    cand_all = float(np.median([row["challenger_health"] for row in rows]))
    print(f"  {'ALL':10s} n={len(rows):2d}  {base_all:.3f} -> {cand_all:.3f}")

    audit_rows = sorted(rows, key=lambda row: row["challenger_health"])[:4]
    for row in sorted(rows, key=lambda row: -abs(row["delta"])):
        if row not in audit_rows:
            audit_rows.append(row)
        if len(audit_rows) == 8:
            break

    gallery = []
    for index, row in enumerate(audit_rows):
        image = comparison_image(row["_image"], row["_base"], row["_cand"])
        name = f"audit_{index:02d}_{row['doc']}_{row['folio']}.jpg"
        cv2.imwrite(str(OUT / name), image, [cv2.IMWRITE_JPEG_QUALITY, 84])
        gallery.append((row, name))

    table = "\n".join(
        f"<tr><td>{corpus}</td><td>{count}</td><td>{base:.3f}</td>"
        f"<td><b>{cand:.3f}</b></td><td>{cand - base:+.3f}</td></tr>"
        for corpus, count, base, cand in summaries
    )
    items = "\n".join(
        f"<h2>{row['doc']}/{row['folio']} — {row['champ_health']:.3f} → "
        f"<b>{row['challenger_health']:.3f}</b> ({row['delta']:+.3f}; "
        f"kept {row['champ_kept']}→{row['challenger_kept']}; "
        f"{row['frame_method']})</h2><img src=\"{name}\" loading=\"lazy\">"
        for row, name in gallery
    )
    (OUT / "validation_report.html").write_text(
        f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>light frame validation</title>
<style>body{{background:#faf7f0;font:15px/1.5 Charter,Georgia,serif;color:#2b2620;
margin:1.5rem;max-width:100rem}}img{{width:100%;border:1px solid #d9d2c4;
margin-bottom:1rem}}h2{{font:0.9rem/1.4 Consolas,monospace}}table{{border-collapse:
collapse}}td,th{{border-bottom:1px solid #d9d2c4;padding:.3rem .8rem}}</style>
</head><body><h1>Light-backdrop frame challenger</h1>
<p>{len(rows)} frozen folios. Overall median health {base_all:.3f} →
<b>{cand_all:.3f}</b> ({cand_all - base_all:+.3f}). Red is the champion;
green is the challenger.</p><table><tr><th>corpus</th><th>n</th>
<th>champion</th><th>challenger</th><th>delta</th></tr>{table}</table>
<h1>Worst challenger folios and largest changes</h1>{items}</body></html>""",
        encoding="utf-8",
    )
    print(OUT / "validation_report.html")


if __name__ == "__main__":
    main()
