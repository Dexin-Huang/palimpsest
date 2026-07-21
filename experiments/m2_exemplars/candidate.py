"""Exemplar-library candidate: harvest, normalize, audit (see NOTES.md).

Reads the align artifacts already in the library; writes crops, an index,
a purity scoreboard, and a contact sheet for the visual audit. No model
calls, no writes outside out/.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

DOC = Path(__file__).parents[2] / "library" / "gallica_pelliot_chinois_3477"
OUT = Path(__file__).parent / "out"
CONFIDENCE_FLOOR = 0.7
PAD_FRAC = 0.08
CANVAS = 64


def harvest(alignments: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """Harvest from the library's align artifacts, or from alignments
    computed in memory by a challenger implementation."""
    instances: dict[str, list[dict]] = defaultdict(list)
    artifacts = (
        alignments.values() if alignments is not None else
        (json.loads(f.read_text(encoding="utf-8"))
         for f in sorted((DOC / "page_alignment").glob("*.json")))
    )
    for artifact in artifacts:
        image = cv2.imread(str(DOC / "page_image_clean" / f"{artifact['page_id']}.jpg"))  # noqa: E501
        for column in artifact["columns"]:
            for char in column["chars"]:
                if not char["bbox"] or char["confidence"] < CONFIDENCE_FLOOR:
                    continue
                mask, gray = normalize(image, char["bbox"])
                if mask is None:
                    continue
                instances[char["ch"]].append({
                    "page_id": artifact["page_id"], "bbox": char["bbox"],
                    "confidence": char["confidence"], "mask": mask, "gray": gray,
                })
    return instances


def normalize(image: np.ndarray, bbox: list[int]):
    x, y, w, h = bbox
    pad = int(max(w, h) * PAD_FRAC)
    crop = image[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    if crop.size == 0:
        return None, None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if ink.sum() == 0:
        return None, None
    ys, xs = np.nonzero(ink)
    tight = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    scale = (CANVAS - 8) / max(tight.shape)
    resized = cv2.resize(tight, (max(1, int(tight.shape[1] * scale)),
                                 max(1, int(tight.shape[0] * scale))))
    canvas = np.zeros((CANVAS, CANVAS), np.uint8)
    oy = (CANVAS - resized.shape[0]) // 2
    ox = (CANVAS - resized.shape[1]) // 2
    canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized
    gray_tight = gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return (canvas > 127), cv2.resize(gray_tight, (CANVAS, CANVAS))


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    union = (a | b).sum()
    return 1.0 - ((a ^ b).sum() / union) if union else 0.0


def audit(instances: dict[str, list[dict]]) -> dict:
    means = {ch: np.mean([i["mask"] for i in inst], axis=0)
             for ch, inst in instances.items()}
    correct = total = 0
    intra, inter = [], []
    outliers = []
    chars = list(instances)
    rng = np.random.default_rng(0)
    for ch, inst in instances.items():
        if len(inst) < 2:
            continue
        for k, item in enumerate(inst):
            own = np.mean([i["mask"] for j, i in enumerate(inst) if j != k], axis=0)
            best_ch, best_score = None, -1.0
            for other in chars:
                template = own if other == ch else means[other]
                score = similarity(item["mask"], template > 0.5)
                if score > best_score:
                    best_ch, best_score = other, score
            own_score = similarity(item["mask"], own > 0.5)
            intra.append(own_score)
            total += 1
            correct += best_ch == ch
            if own_score < 0.35:
                outliers.append((ch, item["page_id"], item["bbox"], round(own_score, 2)))
    for _ in range(min(2000, total * 4)):
        a, b = rng.choice(chars, 2, replace=False)
        inter.append(similarity(
            instances[a][0]["mask"], means[b] > 0.5))
    return {"purity": correct / total if total else 0.0,
            "audited": total,
            "intra_mean": float(np.mean(intra)) if intra else 0.0,
            "inter_mean": float(np.mean(inter)) if inter else 0.0,
            "outliers": outliers}


def contact_sheet(instances: dict[str, list[dict]], top: int = 6) -> Path:
    frequent = sorted(instances, key=lambda c: -len(instances[c]))[:top]
    rows = []
    width = max(len(instances[c]) for c in frequent)
    for ch in frequent:
        tiles = [np.where(i["mask"], 0, 255).astype(np.uint8)
                 for i in instances[ch]]
        tiles += [np.full((CANVAS, CANVAS), 200, np.uint8)] * (width - len(tiles))
        rows.append(np.hstack([np.pad(t, 2, constant_values=120) for t in tiles]))
    sheet = np.vstack(rows)
    path = OUT / "contact_sheet.png"
    cv2.imwrite(str(path), cv2.resize(sheet, None, fx=2, fy=2,
                                      interpolation=cv2.INTER_NEAREST))
    return path, frequent


def main() -> None:
    OUT.mkdir(exist_ok=True)
    instances = harvest()
    for ch, inst in instances.items():
        folder = OUT / "crops" / f"U{ord(ch):05X}"
        folder.mkdir(parents=True, exist_ok=True)
        for n, item in enumerate(inst):
            cv2.imwrite(str(folder / f"{n}.png"),
                        np.where(item["mask"], 0, 255).astype(np.uint8))
    report = audit(instances)
    sheet, frequent = contact_sheet(instances)
    index = {ch: [{k: i[k] for k in ("page_id", "bbox", "confidence")}
                  for i in inst] for ch, inst in instances.items()}
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = [len(i) for i in instances.values()]
    print(f"characters: {len(instances)} unique, {sum(counts)} instances "
          f"(max {max(counts)}, multi-instance {sum(1 for c in counts if c > 1)})")
    print(f"purity (leave-one-out NN): {report['purity']:.1%} "
          f"over {report['audited']} audited instances")
    print(f"intra-class similarity {report['intra_mean']:.3f} vs "
          f"inter-class {report['inter_mean']:.3f}")
    print(f"outliers (own-class sim < 0.35): {len(report['outliers'])}")
    for ch, page, bbox, score in report["outliers"][:8]:
        print(f"  {ch} {page} {bbox} sim={score}")
    print(f"contact sheet ({''.join(frequent)}): {sheet}")


if __name__ == "__main__":
    main()
