"""Materialize leak-free one-class RF-DETR tiles from MTHv2 manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATEGORY = {"id": 1, "name": "character", "supercategory": "character"}
MIN_VISIBLE_FRACTION = 0.80


def read_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(record)
    return records


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tile_origins(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= 0 or tile_size <= 0:
        raise ValueError("image length and tile size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("tile overlap must be non-negative and smaller than tile size")
    if length <= tile_size:
        return [0]
    stride = tile_size - overlap
    origins = list(range(0, length - tile_size + 1, stride))
    final_origin = length - tile_size
    if origins[-1] != final_origin:
        origins.append(final_origin)
    return origins


def clipped_bbox(
    bbox: object, *, x0: int, y0: int, width: int, height: int
) -> list[float] | None:
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(not isinstance(value, (int, float)) for value in bbox)
    ):
        raise ValueError("character bbox must contain four numbers")
    x, y, box_width, box_height = (float(value) for value in bbox)
    if box_width <= 0 or box_height <= 0:
        raise ValueError("character bbox dimensions must be positive")
    left = max(x, float(x0))
    top = max(y, float(y0))
    right = min(x + box_width, float(x0 + width))
    bottom = min(y + box_height, float(y0 + height))
    visible_width = right - left
    visible_height = bottom - top
    if (
        visible_width <= 0
        or visible_height <= 0
        or visible_width * visible_height
        < box_width * box_height * MIN_VISIBLE_FRACTION
    ):
        return None
    return [left - x0, top - y0, visible_width, visible_height]


def image_path(record: dict[str, object]) -> Path:
    value = record.get("image")
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {record.get('case_id')!r} has no image path")
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def safe_case_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("every case requires a non-empty case_id")
    return value.replace("/", "__").replace("\\", "__")


def write_split(
    records: list[dict[str, object]],
    destination: Path,
    split: str,
    *,
    tile_size: int,
    overlap: int,
    keep_empty: bool,
) -> dict[str, int]:
    split_root = destination / split
    annotations_path = split_root / "_annotations.coco.json"
    if annotations_path.exists():
        raise FileExistsError(f"refusing to overwrite immutable COCO split: {split_root}")
    split_root.mkdir(parents=True, exist_ok=True)

    images: list[dict[str, object]] = []
    annotations: list[dict[str, object]] = []
    image_id = 1
    annotation_id = 1
    for record in records:
        source_path = image_path(record)
        image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot decode training image: {source_path}")
        height, width = image.shape[:2]
        raw_characters = record.get("characters")
        if not isinstance(raw_characters, list):
            raise ValueError(f"case {record.get('case_id')!r} lacks character boxes")
        case_name = safe_case_id(record.get("case_id"))
        for y0 in tile_origins(height, tile_size, overlap):
            for x0 in tile_origins(width, tile_size, overlap):
                tile_width = min(tile_size, width - x0)
                tile_height = min(tile_size, height - y0)
                boxes = [
                    clipped
                    for character in raw_characters
                    if isinstance(character, dict)
                    for clipped in [
                        clipped_bbox(
                            character.get("bbox"),
                            x0=x0,
                            y0=y0,
                            width=tile_width,
                            height=tile_height,
                        )
                    ]
                    if clipped is not None
                ]
                tile_name = f"{case_name}__x{x0}_y{y0}.png"
                retain_empty = keep_empty or int(
                    hashlib.sha256(tile_name.encode()).hexdigest(), 16
                ) % 10 == 0
                if not boxes and not retain_empty:
                    continue
                tile = image[y0 : y0 + tile_height, x0 : x0 + tile_width]
                if not cv2.imwrite(str(split_root / tile_name), tile):
                    raise OSError(f"failed to write tile: {split_root / tile_name}")
                images.append(
                    {
                        "id": image_id,
                        "file_name": tile_name,
                        "width": tile_width,
                        "height": tile_height,
                        "source_case_id": record["case_id"],
                        "source_origin": [x0, y0],
                    }
                )
                for box in boxes:
                    annotations.append(
                        {
                            "id": annotation_id,
                            "image_id": image_id,
                            "category_id": CATEGORY["id"],
                            "bbox": box,
                            "area": box[2] * box[3],
                            "iscrowd": 0,
                        }
                    )
                    annotation_id += 1
                image_id += 1

    payload = {
        "info": {
            "description": "MTHv2 unlabeled character localization tiles",
            "version": "1",
        },
        "licenses": [],
        "categories": [CATEGORY],
        "images": images,
        "annotations": annotations,
    }
    annotations_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"pages": len(records), "tiles": len(images), "boxes": len(annotations)}


def manifest_identity(record: dict[str, object], field: str) -> str | None:
    if field in {"case_id", "source_path"}:
        value = record.get(field)
    else:
        hashes = record.get("sha256")
        value = hashes.get(field) if isinstance(hashes, dict) else None
    return value if isinstance(value, str) and value else None


def validate_disjoint_manifests(
    training: list[dict[str, object]], development: list[dict[str, object]]
) -> None:
    for field in ("case_id", "source_path", "image", "text_lines", "characters"):
        split_values: list[set[str]] = []
        for split, records in (("training", training), ("development", development)):
            values = [
                value
                for record in records
                if (value := manifest_identity(record, field)) is not None
            ]
            if field == "case_id" and len(values) != len(records):
                raise ValueError(f"{split} manifest has a missing case_id")
            if len(values) != len(set(values)):
                raise ValueError(f"{split} manifest has duplicate {field} identities")
            split_values.append(set(values))
        overlap = sorted(split_values[0] & split_values[1])
        if overlap:
            raise ValueError(
                f"training/development leakage by {field}: {overlap[:5]}"
            )


def materialize(
    training_manifest: Path,
    development_manifest: Path,
    destination: Path,
    *,
    tile_size: int,
    overlap: int,
) -> dict[str, object]:
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"refusing to overwrite dataset: {destination}")
    training = read_jsonl(training_manifest)
    development = read_jsonl(development_manifest)
    validate_disjoint_manifests(training, development)

    counts = {
        "train": write_split(
            training,
            destination,
            "train",
            tile_size=tile_size,
            overlap=overlap,
            keep_empty=False,
        ),
        "valid": write_split(
            development,
            destination,
            "valid",
            tile_size=tile_size,
            overlap=overlap,
            keep_empty=True,
        ),
        "test": write_split(
            [],
            destination,
            "test",
            tile_size=tile_size,
            overlap=overlap,
            keep_empty=True,
        ),
    }
    metadata: dict[str, object] = {
        "schema_version": 1,
        "objective": "unlabeled_character_localization",
        "tile_size": tile_size,
        "overlap": overlap,
        "minimum_visible_fraction": MIN_VISIBLE_FRACTION,
        "category": CATEGORY,
        "source_manifests": {
            "training": {
                "path": training_manifest.resolve().as_posix(),
                "sha256": sha256_file(training_manifest),
            },
            "development": {
                "path": development_manifest.resolve().as_posix(),
                "sha256": sha256_file(development_manifest),
            },
        },
        "counts": counts,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return metadata


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-manifest", type=Path, required=True)
    parser.add_argument("--development-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=96)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.tile_size <= 0:
        raise SystemExit("--tile-size must be positive")
    if args.overlap < 0 or args.overlap >= args.tile_size:
        raise SystemExit("--overlap must be non-negative and smaller than --tile-size")
    metadata = materialize(
        args.training_manifest.resolve(),
        args.development_manifest.resolve(),
        args.out.resolve(),
        tile_size=args.tile_size,
        overlap=args.overlap,
    )
    print(json.dumps(metadata["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
