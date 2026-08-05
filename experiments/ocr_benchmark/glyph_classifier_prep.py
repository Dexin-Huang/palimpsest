"""Build the local glyph-classifier training set from already-pinned corpora.

Sources, with the data-split contract preserved:

- TRAIN/DEV: the MTHv2 train-side manifests of
  ``train-all-valid-after-dev80-v1`` (2,156 training + 240 development pages,
  disjoint from the official test-800 set that our evaluation suites and the
  adjudication instances come from). Per-character crops cut from
  ``label_char`` boxes.
- TRAIN EXTRA: the 1,928 seed-labeled Dunhuang exemplar crops
  (exemplar-labeling-development-v1; high/medium certainty only, characters
  already in the vocabulary) - cross-domain manuscript signal.
- LOCAL EVAL PACK: crops for the 1,131 blind-adjudication instances
  (test-800 pages, EVAL ONLY, never shipped to the pod).

Outputs under scratch/ocr_benchmark/glyph-classifier-v1/:
  shards/train-XXXX.tar   members named <class_id>/<n>.jpg
  shards/dev-XXXX.tar
  classes.json            char -> class_id
  adjudication_eval.tar   + adjudication_eval.jsonl (metadata)
  prep_report.json        counts, caps, vocabulary, sha256 of every artifact
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

import cv2

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = (
    REPOSITORY_ROOT / "scratch/ocr_benchmark/mthv2/train-all-valid-after-dev80-v1"
)
DEFAULT_ASSET_ROOT = REPOSITORY_ROOT / "scratch/ocr_benchmark/mthv2/v1/assets"
DEFAULT_EXEMPLAR_CROPS = REPOSITORY_ROOT / "experiments/char_inventory/out/crops"
DEFAULT_EXEMPLAR_LABELS = (
    REPOSITORY_ROOT / "scratch/ocr_benchmark/runs/glyph-labeling-v1/labels.jsonl"
)
DEFAULT_ADJ_MANIFEST = (
    REPOSITORY_ROOT / "scratch/ocr_benchmark/runs/glyph-adjudication-v1/manifest.jsonl"
)
DEFAULT_OUT_ROOT = REPOSITORY_ROOT / "scratch/ocr_benchmark/glyph-classifier-v1"

SEED = 361004
CROP_SIZE = 64
PAD_FRACTION = 0.15
MIN_COUNT = 2
PER_CLASS_CAP = 3000
SHARD_SIZE = 25000
JPEG_QUALITY = 87


def local_image(manifest_image: str, asset_root: Path) -> Path:
    marker = "/assets/"
    index = manifest_image.rfind(marker)
    if index < 0:
        raise ValueError(f"unexpected manifest image path: {manifest_image}")
    return asset_root / manifest_image[index + len(marker) :]


def parse_label_char(path: Path) -> list[tuple[str, tuple[float, float, float, float]]]:
    entries = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw.strip().split()
        if len(parts) != 5 or len(parts[0]) != 1:
            continue
        x1, y1, x2, y2 = (float(v) for v in parts[1:])
        entries.append((parts[0], (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))))
    return entries


def crop_jpeg(image, box, pad_fraction: float = PAD_FRACTION) -> bytes | None:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    pad_x = pad_fraction * (x2 - x1) + 2
    pad_y = pad_fraction * (y2 - y1) + 2
    ax1, ax2 = max(0, int(x1 - pad_x)), min(width, int(x2 + pad_x))
    ay1, ay2 = max(0, int(y1 - pad_y)), min(height, int(y2 + pad_y))
    crop = image[ay1:ay2, ax1:ax2]
    if crop.size == 0:
        return None
    crop = cv2.resize(crop, (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_AREA)
    ok, payload = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return payload.tobytes() if ok else None


def page_records(manifest: Path, asset_root: Path):
    for line in manifest.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        image_path = local_image(str(record["image"]), asset_root)
        label_path = (
            image_path.parent.parent / "label_char" / (image_path.stem + ".txt")
        )
        if image_path.is_file() and label_path.is_file():
            yield record["case_id"], image_path, label_path


class ShardWriter:
    def __init__(self, root: Path, prefix: str):
        self.root = root
        self.prefix = prefix
        self.index = 0
        self.count = 0
        self.total = 0
        self.tar: tarfile.TarFile | None = None
        self.paths: list[Path] = []

    def _open(self) -> None:
        path = self.root / f"{self.prefix}-{self.index:04d}.tar"
        self.tar = tarfile.open(path, "w")
        self.paths.append(path)

    def add(self, class_id: int, payload: bytes) -> None:
        if self.tar is None or self.count >= SHARD_SIZE:
            if self.tar is not None:
                self.tar.close()
                self.index += 1
            self.count = 0
            self._open()
        info = tarfile.TarInfo(name=f"{class_id}/{self.total:08d}.jpg")
        info.size = len(payload)
        self.tar.addfile(info, io.BytesIO(payload))
        self.count += 1
        self.total += 1

    def close(self) -> None:
        if self.tar is not None:
            self.tar.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--exemplar-crops", type=Path, default=DEFAULT_EXEMPLAR_CROPS)
    parser.add_argument("--exemplar-labels", type=Path, default=DEFAULT_EXEMPLAR_LABELS)
    parser.add_argument(
        "--adjudication-manifest", type=Path, default=DEFAULT_ADJ_MANIFEST
    )
    parser.add_argument("--skip-adjudication-eval", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--minimum-train-pages", type=int, default=2_000)
    parser.add_argument(
        "--exclude-case-prefixes",
        default="",
        help=(
            "comma-separated case_id prefixes removed from BOTH splits before "
            "vocabulary and crop extraction; the training manifest records the "
            "exclusions for holdout audits"
        ),
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    rng = random.Random(SEED)
    out_root = args.output.resolve()
    shards_root = out_root / "shards"
    shards_root.mkdir(parents=True, exist_ok=True)

    # Pass 1: vocabulary from the training split.
    counts: Counter[str] = Counter()
    exclude_prefixes = tuple(
        prefix.strip()
        for prefix in args.exclude_case_prefixes.split(",")
        if prefix.strip()
    )

    def excluded(case_id: str) -> bool:
        return any(case_id.startswith(prefix) for prefix in exclude_prefixes)

    train_pages_all = list(
        page_records(args.data_root / "manifests/training.jsonl", args.asset_root)
    )
    dev_pages_all = list(
        page_records(args.data_root / "manifests/development.jsonl", args.asset_root)
    )
    train_pages = [page for page in train_pages_all if not excluded(page[0])]
    dev_pages = [page for page in dev_pages_all if not excluded(page[0])]
    excluded_pages = (len(train_pages_all) - len(train_pages)) + (
        len(dev_pages_all) - len(dev_pages)
    )
    if exclude_prefixes:
        print(
            f"canary exclusion: {excluded_pages} pages removed by "
            f"{len(exclude_prefixes)} prefixes",
            flush=True,
        )
    if len(train_pages) < args.minimum_train_pages:
        raise RuntimeError(
            f"only {len(train_pages)} training pages are materialized; "
            f"need at least {args.minimum_train_pages}"
        )
    for _, _, label_path in train_pages:
        counts.update(ch for ch, _ in parse_label_char(label_path))
    vocab = sorted(ch for ch, n in counts.items() if n >= MIN_COUNT)
    class_of = {ch: i for i, ch in enumerate(vocab)}
    skipped_rare = sum(n for ch, n in counts.items() if n < MIN_COUNT)
    print(f"pages: train={len(train_pages)} dev={len(dev_pages)}", flush=True)
    print(
        f"vocab: {len(vocab)} classes (min_count={MIN_COUNT}); "
        f"rare occurrences skipped: {skipped_rare}",
        flush=True,
    )

    # Pass 2: crops, per-class cap via reservoir-free shuffle of page order.
    per_class: dict[int, int] = defaultdict(int)
    writer = ShardWriter(shards_root, "train")
    rng.shuffle(train_pages)
    capped = 0
    for page_index, (_, image_path, label_path) in enumerate(train_pages, 1):
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        for ch, box in parse_label_char(label_path):
            class_id = class_of.get(ch)
            if class_id is None:
                continue
            if per_class[class_id] >= PER_CLASS_CAP:
                capped += 1
                continue
            payload = crop_jpeg(image, box)
            if payload is None:
                continue
            writer.add(class_id, payload)
            per_class[class_id] += 1
        if page_index % 200 == 0:
            print(
                f"  train pages {page_index}/{len(train_pages)} crops={writer.total}",
                flush=True,
            )

    # Exemplar bank: high/medium certainty, in-vocab.
    exemplar_added = 0
    exemplar_skipped = 0
    for line in args.exemplar_labels.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        ch = row.get("character") or ""
        if not ch or row.get("certainty") not in ("high", "medium"):
            continue
        class_id = class_of.get(ch)
        if class_id is None:
            exemplar_skipped += 1
            continue
        crop_path = args.exemplar_crops / f"{row['crop_id']}.png"
        image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        image = cv2.resize(image, (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_AREA)
        ok, payload = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            continue
        writer.add(class_id, payload.tobytes())
        per_class[class_id] += 1
        exemplar_added += 1
    writer.close()
    train_total = writer.total
    print(
        f"train crops: {train_total} (exemplar {exemplar_added} added, "
        f"{exemplar_skipped} out-of-vocab; {capped} over class cap)",
        flush=True,
    )

    dev_writer = ShardWriter(shards_root, "dev")
    dev_skipped = 0
    for _, image_path, label_path in dev_pages:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        for ch, box in parse_label_char(label_path):
            class_id = class_of.get(ch)
            if class_id is None:
                dev_skipped += 1
                continue
            payload = crop_jpeg(image, box)
            if payload is not None:
                dev_writer.add(class_id, payload)
    dev_writer.close()
    print(f"dev crops: {dev_writer.total} (out-of-vocab {dev_skipped})", flush=True)

    eval_rows = []
    eval_artifacts: list[Path] = []
    if not args.skip_adjudication_eval:
        # This frozen probe is intentionally constructed only on the local workstation.
        eval_tar_path = out_root / "adjudication_eval.tar"
        with tarfile.open(eval_tar_path, "w") as eval_tar:
            by_image: dict[str, list[dict[str, object]]] = defaultdict(list)
            for line in args.adjudication_manifest.read_text(
                encoding="utf-8"
            ).splitlines():
                row = json.loads(line)
                by_image[str(row["image"])].append(row)
            for image_rel, rows in by_image.items():
                image = cv2.imread(str(REPOSITORY_ROOT / image_rel), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                for row in rows:
                    payload = crop_jpeg(image, tuple(row["box"]), pad_fraction=0.3)
                    if payload is None:
                        continue
                    name = f"{row['instance_id'].replace('#', '_')}.jpg"
                    info = tarfile.TarInfo(name=name)
                    info.size = len(payload)
                    eval_tar.addfile(info, io.BytesIO(payload))
                    eval_rows.append(
                        {
                            "instance_id": row["instance_id"],
                            "member": name,
                            "kind": row["kind"],
                            "gold_char": row["gold_char"],
                            "output_char": row["output_char"],
                            "alternative": row["alternative"],
                            "case_id": row["case_id"],
                            "corpus": row["corpus"],
                        }
                    )
        eval_manifest_path = out_root / "adjudication_eval.jsonl"
        eval_manifest_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in eval_rows),
            encoding="utf-8",
            newline="\n",
        )
        eval_artifacts = [eval_tar_path, eval_manifest_path]
        print(f"adjudication eval pack: {len(eval_rows)} instances", flush=True)

    (out_root / "classes.json").write_text(
        json.dumps(vocab, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    (out_root / "class_counts.json").write_text(
        json.dumps([per_class[index] for index in range(len(vocab))]),
        encoding="utf-8",
        newline="\n",
    )
    artifacts = {}
    for path in (
        sorted(shards_root.glob("*.tar"))
        + [
            out_root / "classes.json",
            out_root / "class_counts.json",
        ]
        + eval_artifacts
    ):
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        "seed": SEED,
        "crop_size": CROP_SIZE,
        "pad_fraction": PAD_FRACTION,
        "min_count": MIN_COUNT,
        "per_class_cap": PER_CLASS_CAP,
        "jpeg_quality": JPEG_QUALITY,
        "classes": len(vocab),
        "train_pages": len(train_pages),
        "dev_pages": len(dev_pages),
        "excluded_case_prefixes": list(exclude_prefixes),
        "excluded_pages": excluded_pages,
        "train_crops": train_total,
        "exemplar_added": exemplar_added,
        "exemplar_out_of_vocab": exemplar_skipped,
        "capped_occurrences": capped,
        "dev_crops": dev_writer.total,
        "dev_out_of_vocab": dev_skipped,
        "adjudication_instances": len(eval_rows),
        "artifacts_sha256": artifacts,
    }
    (out_root / "prep_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "artifacts_sha256"}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
