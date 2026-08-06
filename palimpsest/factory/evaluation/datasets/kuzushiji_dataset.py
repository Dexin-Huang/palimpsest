"""Build immutable one-class training data from the CODH Kuzushiji v2 release."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import time
import sys
import zipfile
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path, PurePosixPath

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DATASET_ID = "codh-kuzushiji-v2"
SOURCE_URL = "https://codh.rois.ac.jp/char-shape/dataset/v2/all.zip"
ARCHIVE_SIZE = 5_144_378_200
ARCHIVE_LAST_MODIFIED = "Sun, 08 May 2022 08:30:27 GMT"
SOURCE_PAGE = "https://codh.rois.ac.jp/char-shape/"
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = (
    "Japanese Kuzushiji Dataset (National Institute of Japanese Literature and "
    "other holding institutions / processed by CODH), doi:10.20676/00000340"
)
SEED = 361004
CSV_FIELDS = (
    "Unicode",
    "Image",
    "X",
    "Y",
    "Block ID",
    "Char ID",
    "Width",
    "Height",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def repository_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def hash_rank(*values: object) -> bytes:
    payload = "\0".join(str(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).digest()


class HTTPRangeReader(io.RawIOBase):
    """Seekable reader backed by validated HTTP byte ranges."""

    def __init__(
        self, url: str, size: int, block_size: int = 1024 * 1024
    ) -> None:
        super().__init__()
        self.url = url
        self.size = size
        self.block_size = block_size
        self.position = 0
        self.cache: dict[int, bytes] = {}

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.position + offset
        elif whence == io.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError(f"unsupported seek mode: {whence}")
        if position < 0:
            raise ValueError("negative seek position")
        self.position = min(position, self.size)
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.position >= self.size:
            return b""
        remaining = self.size - self.position if size is None or size < 0 else size
        remaining = min(remaining, self.size - self.position)
        chunks: list[bytes] = []
        while remaining:
            block_start = (self.position // self.block_size) * self.block_size
            block = self._block(block_start)
            offset = self.position - block_start
            chunk = block[offset : offset + remaining]
            if not chunk:
                raise OSError(f"empty range block at archive offset {self.position}")
            chunks.append(chunk)
            self.position += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _block(self, start: int) -> bytes:
        cached = self.cache.get(start)
        if cached is not None:
            return cached
        end = min(start + self.block_size, self.size) - 1
        request = urllib.request.Request(
            self.url,
            headers={
                "Accept-Encoding": "identity",
                "Range": f"bytes={start}-{end}",
                "User-Agent": "Palimpsest-Kuzushiji-Benchmark/1",
            },
        )
        error: Exception | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    if response.status != 206:
                        raise OSError(
                            f"server ignored byte range {start}-{end}: "
                            f"HTTP {response.status}"
                        )
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {start}-{end}/"):
                        raise OSError(f"unexpected Content-Range: {content_range!r}")
                    payload = response.read()
                if len(payload) != end - start + 1:
                    raise OSError(
                        f"short range {start}-{end}: received {len(payload)} bytes"
                    )
                if len(self.cache) >= 8:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[start] = payload
                return payload
            except (OSError, urllib.error.URLError) as caught:
                error = caught
                time.sleep(2**attempt)
        raise OSError(f"failed to fetch archive range {start}-{end}") from error


def coordinate_members(archive: zipfile.ZipFile) -> dict[str, str]:
    members: dict[str, str] = {}
    for name in archive.namelist():
        path = PurePosixPath(name)
        if len(path.parts) != 2 or not path.name.endswith("_coordinate.csv"):
            continue
        book_id = path.parts[0]
        if path.name != f"{book_id}_coordinate.csv":
            continue
        if book_id in members:
            raise ValueError(f"duplicate coordinate table for book {book_id}")
        members[book_id] = name
    if not members:
        raise ValueError("archive contains no Kuzushiji coordinate tables")
    return members


def parse_coordinate_table(
    payload: bytes, book_id: str, member: str
) -> dict[str, list[dict[str, object]]]:
    pages: dict[str, list[dict[str, object]]] = defaultdict(list)
    identities: set[tuple[str, str]] = set()
    reader = csv.DictReader(
        io.StringIO(payload.decode("utf-8-sig"), newline="")
    )
    if tuple(reader.fieldnames or ()) != CSV_FIELDS:
        raise ValueError(
            f"unexpected coordinate columns for {book_id}: {reader.fieldnames}"
        )
    for line_number, row in enumerate(reader, start=2):
        page_id = row["Image"].strip()
        character_id = row["Char ID"].strip()
        if not page_id or not character_id:
            raise ValueError(f"{member}:{line_number}: missing page or character ID")
        identity = (page_id, character_id)
        if identity in identities:
            raise ValueError(f"{member}:{line_number}: duplicate character {identity}")
        identities.add(identity)
        try:
            bbox = [
                int(row["X"]),
                int(row["Y"]),
                int(row["Width"]),
                int(row["Height"]),
            ]
        except ValueError as exc:
            raise ValueError(f"{member}:{line_number}: non-integer bbox") from exc
        if bbox[0] < 0 or bbox[1] < 0 or bbox[2] <= 0 or bbox[3] <= 0:
            raise ValueError(f"{member}:{line_number}: invalid bbox {bbox}")
        character: dict[str, object] = {"bbox": bbox}
        codepoint = row["Unicode"].strip()
        if codepoint:
            character["text"] = codepoint
        pages[page_id].append(character)
    return dict(pages)


def image_members(archive: zipfile.ZipFile, book_id: str) -> dict[str, str]:
    prefix = f"{book_id}/images/"
    result: dict[str, str] = {}
    for name in archive.namelist():
        if not name.startswith(prefix):
            continue
        path = PurePosixPath(name)
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        page_id = path.stem
        if page_id in result:
            raise ValueError(f"duplicate page image for {book_id}/{page_id}")
        result[page_id] = name
    return result


def select_pages(
    pages_by_book: dict[str, list[str]], *, page_count: int, seed: int
) -> list[tuple[str, str]]:
    if page_count <= 0:
        raise ValueError("page_count must be positive")
    ordered_books = sorted(pages_by_book, key=lambda book: hash_rank(seed, "book", book))
    ranked_pages = {
        book: sorted(
            pages_by_book[book],
            key=lambda page: hash_rank(seed, "page", book, page),
        )
        for book in ordered_books
    }
    available = sum(len(pages) for pages in ranked_pages.values())
    if available < page_count:
        raise ValueError(f"requested {page_count} pages but only {available} are available")
    selected: list[tuple[str, str]] = []
    round_index = 0
    while len(selected) < page_count:
        added = False
        for book in ordered_books:
            pages = ranked_pages[book]
            if round_index < len(pages):
                selected.append((book, pages[round_index]))
                added = True
                if len(selected) == page_count:
                    return selected
        if not added:
            raise AssertionError("page selection exhausted unexpectedly")
        round_index += 1
    return selected


def write_jsonl(path: Path, records: list[dict[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for record in records:
            target.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return sha256_file(path)


def build_manifest(
    archive_path: Path | None,
    output: Path,
    *,
    page_count: int,
    seed: int,
    expected_archive_sha256: str | None,
    archive_url: str | None = None,
    archive_size: int | None = None,
    archive_last_modified: str | None = None,
) -> dict[str, object]:
    manifest_path = output / "manifests" / "training.jsonl"
    assets_root = output / "assets"
    if manifest_path.exists() or (assets_root.exists() and any(assets_root.iterdir())):
        raise FileExistsError(f"refusing to overwrite Kuzushiji dataset: {output}")
    if archive_path is None:
        if not archive_url or archive_size is None or archive_size <= 0:
            raise ValueError("remote archive requires a URL and positive byte size")
        if expected_archive_sha256:
            raise ValueError("full archive SHA-256 cannot be verified through range access")
        archive_sha256 = None
        source: io.IOBase = HTTPRangeReader(archive_url, archive_size)
        archive_identity: dict[str, object] = {
            "path": None,
            "url": archive_url,
            "sha256": None,
            "bytes": archive_size,
            "last_modified": archive_last_modified,
        }
    else:
        archive_sha256 = sha256_file(archive_path)
        if expected_archive_sha256 and archive_sha256 != expected_archive_sha256:
            raise ValueError(
                "Kuzushiji archive SHA-256 mismatch: "
                f"expected {expected_archive_sha256}, got {archive_sha256}"
            )
        source = archive_path.open("rb")
        archive_identity = {
            "path": archive_path.resolve().as_posix(),
            "url": SOURCE_URL,
            "sha256": archive_sha256,
            "bytes": archive_path.stat().st_size,
            "last_modified": archive_last_modified,
        }
    source_snapshot = sha256_bytes(
        json.dumps(
            archive_identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    records: list[dict[str, object]] = []
    book_counts: dict[str, int] = defaultdict(int)
    coordinate_sha256: dict[str, str] = {}
    try:
        with zipfile.ZipFile(source) as archive:
            coordinate_tables = coordinate_members(archive)
            annotations: dict[str, dict[str, list[dict[str, object]]]] = {}
            images: dict[str, dict[str, str]] = {}
            eligible: dict[str, list[str]] = {}
            for book_id, member in coordinate_tables.items():
                coordinate_payload = archive.read(member)
                coordinate_sha256[book_id] = sha256_bytes(coordinate_payload)
                book_annotations = parse_coordinate_table(
                    coordinate_payload, book_id, member
                )
                book_images = image_members(archive, book_id)
                missing_images = sorted(set(book_annotations) - set(book_images))
                if missing_images:
                    raise ValueError(
                        f"book {book_id} has annotations without images: "
                        f"{missing_images[:5]}"
                    )
                annotations[book_id] = book_annotations
                images[book_id] = book_images
                eligible[book_id] = sorted(book_annotations)

            selected = [
                (selection_rank, book_id, page_id, images[book_id][page_id])
                for selection_rank, (book_id, page_id) in enumerate(
                    select_pages(eligible, page_count=page_count, seed=seed),
                    start=1,
                )
            ]
            selected.sort(key=lambda item: archive.getinfo(item[3]).header_offset)
            for selection_rank, book_id, page_id, member in selected:
                payload = archive.read(member)
                image = cv2.imdecode(
                    np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if image is None:
                    raise ValueError(f"cannot decode Kuzushiji image {member}")
                height, width = image.shape[:2]
                characters = annotations[book_id][page_id]
                for character in characters:
                    x, y, box_width, box_height = character["bbox"]
                    if x + box_width > width or y + box_height > height:
                        raise ValueError(
                            f"Kuzushiji bbox exceeds {member} dimensions: "
                            f"{character['bbox']} outside {width}x{height}"
                        )
                suffix = PurePosixPath(member).suffix.lower()
                destination = assets_root / book_id / f"{page_id}{suffix}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                character_sha256 = sha256_bytes(
                    json.dumps(
                        characters,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                records.append(
                    {
                        "case_id": f"kuzushiji/{book_id}/{page_id}",
                        "dataset": DATASET_ID,
                        "split": "training",
                        "selection_rank": selection_rank,
                        "source_book_id": book_id,
                        "source_path": member,
                        "image": repository_relative(destination),
                        "width": width,
                        "height": height,
                        "strata": ["Kuzushiji", f"book:{book_id}"],
                        "license": LICENSE,
                        "characters": characters,
                        "sha256": {
                            "image": sha256_bytes(payload),
                            "characters": character_sha256,
                            "coordinates": coordinate_sha256[book_id],
                            "source_snapshot": source_snapshot,
                        },
                    }
                )
                book_counts[book_id] += 1
    finally:
        source.close()

    records.sort(key=lambda record: int(record["selection_rank"]))
    manifest_sha256 = write_jsonl(manifest_path, records)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "dataset": DATASET_ID,
        "source_url": archive_url or SOURCE_URL,
        "source_page": SOURCE_PAGE,
        "archive": archive_identity,
        "source_snapshot_sha256": source_snapshot,
        "coordinate_sha256_by_book": dict(sorted(coordinate_sha256.items())),
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "attribution": ATTRIBUTION,
        "selection": {
            "seed": seed,
            "method": "book-round-robin/page-sha256-rank-v1",
            "pages": len(records),
            "books": len(book_counts),
            "pages_by_book": dict(sorted(book_counts.items())),
        },
        "manifest_sha256": manifest_sha256,
        "counts": {
            "pages": len(records),
            "characters": sum(len(record["characters"]) for record in records),
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return metadata


def read_coco(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a COCO object")
    if not isinstance(payload.get("images"), list) or not isinstance(
        payload.get("annotations"), list
    ):
        raise ValueError(f"{path} lacks COCO images or annotations")
    return payload


def select_additional_tiles(
    images: list[dict[str, object]], *, count: int, seed: int
) -> list[dict[str, object]]:
    by_book: dict[str, list[dict[str, object]]] = defaultdict(list)
    for image in images:
        case_id = image.get("source_case_id")
        if not isinstance(case_id, str):
            raise ValueError("additional tile lacks source_case_id")
        parts = case_id.split("/")
        if len(parts) < 3 or parts[0] != "kuzushiji":
            raise ValueError(f"unexpected additional source case: {case_id!r}")
        by_book[parts[1]].append(image)
    if sum(len(group) for group in by_book.values()) < count:
        raise ValueError("additional tile pool is smaller than the requested matched count")
    ordered_books = sorted(by_book, key=lambda book: hash_rank(seed, "book", book))
    for book, group in by_book.items():
        group.sort(key=lambda image: hash_rank(seed, "tile", book, image.get("file_name")))
    selected: list[dict[str, object]] = []
    round_index = 0
    while len(selected) < count:
        for book in ordered_books:
            group = by_book[book]
            if round_index < len(group):
                selected.append(group[round_index])
                if len(selected) == count:
                    return selected
        round_index += 1
    return selected


def copy_coco_split(
    sources: list[tuple[Path, dict[str, object], list[dict[str, object]]]],
    destination: Path,
    *,
    description: str,
    license_metadata: bool,
) -> dict[str, int]:
    destination.mkdir(parents=True, exist_ok=True)
    output_images: list[dict[str, object]] = []
    output_annotations: list[dict[str, object]] = []
    next_image_id = 1
    next_annotation_id = 1
    seen_names: set[str] = set()
    for split_root, coco, selected_images in sources:
        annotations_by_image: dict[int, list[dict[str, object]]] = defaultdict(list)
        for annotation in coco["annotations"]:
            annotations_by_image[int(annotation["image_id"])].append(annotation)
        for image in selected_images:
            file_name = image.get("file_name")
            if not isinstance(file_name, str) or not file_name:
                raise ValueError("COCO image has no file_name")
            if file_name in seen_names:
                raise ValueError(f"duplicate tile file name while composing data: {file_name}")
            seen_names.add(file_name)
            shutil.copy2(split_root / file_name, destination / file_name)
            original_id = int(image["id"])
            output_image = dict(image)
            output_image["id"] = next_image_id
            output_images.append(output_image)
            for annotation in annotations_by_image.get(original_id, []):
                output_annotation = dict(annotation)
                output_annotation["id"] = next_annotation_id
                output_annotation["image_id"] = next_image_id
                output_annotations.append(output_annotation)
                next_annotation_id += 1
            next_image_id += 1
    payload = {
        "info": {"description": description, "version": "1"},
        "licenses": (
            [
                {
                    "id": 1,
                    "name": LICENSE,
                    "url": LICENSE_URL,
                    "attribution": ATTRIBUTION,
                }
            ]
            if license_metadata
            else []
        ),
        "categories": [{"id": 1, "name": "character", "supercategory": "character"}],
        "images": output_images,
        "annotations": output_annotations,
    }
    annotations_path = destination / "_annotations.coco.json"
    annotations_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "tiles": len(output_images),
        "boxes": len(output_annotations),
        "source_pages": len(
            {image.get("source_case_id") for image in output_images}
        ),
    }


def compose_matched_dataset(
    base: Path,
    control: Path,
    additional: Path,
    output: Path,
    *,
    seed: int,
) -> dict[str, object]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite matched dataset: {output}")
    base_train_path = base / "train" / "_annotations.coco.json"
    control_train_path = control / "train" / "_annotations.coco.json"
    additional_train_path = additional / "train" / "_annotations.coco.json"
    base_train = read_coco(base_train_path)
    control_train = read_coco(control_train_path)
    additional_train = read_coco(additional_train_path)
    target_additional = len(control_train["images"]) - len(base_train["images"])
    if target_additional <= 0:
        raise ValueError("control dataset must contain more training tiles than the base")
    selected_additional = select_additional_tiles(
        additional_train["images"], count=target_additional, seed=seed
    )
    counts = {
        "train": copy_coco_split(
            [
                (base / "train", base_train, base_train["images"]),
                (additional / "train", additional_train, selected_additional),
            ],
            output / "train",
            description="Matched MTHv2 plus CODH Kuzushiji character-localization tiles",
            license_metadata=True,
        )
    }
    for split in ("valid", "test"):
        source_path = base / split / "_annotations.coco.json"
        source = read_coco(source_path)
        counts[split] = copy_coco_split(
            [(base / split, source, source["images"])],
            output / split,
            description="Frozen MTHv2 character-localization tiles",
            license_metadata=False,
        )
    metadata: dict[str, object] = {
        "schema_version": 1,
        "objective": "unlabeled_character_localization",
        "selection": {
            "seed": seed,
            "method": "book-round-robin/tile-sha256-rank-v1",
            "matched_control_train_tiles": len(control_train["images"]),
            "base_train_tiles": len(base_train["images"]),
            "added_kuzushiji_tiles": target_additional,
        },
        "sources": {
            "base": {
                "path": base.resolve().as_posix(),
                "dataset_sha256": sha256_file(base / "dataset.json"),
            },
            "control": {
                "path": control.resolve().as_posix(),
                "dataset_sha256": sha256_file(control / "dataset.json"),
            },
            "kuzushiji_pool": {
                "path": additional.resolve().as_posix(),
                "dataset_sha256": sha256_file(additional / "dataset.json"),
                "license": LICENSE,
                "attribution": ATTRIBUTION,
            },
        },
        "counts": counts,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return metadata


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest", help="extract a frozen page manifest")
    source = manifest.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--remote", action="store_true")
    manifest.add_argument("--out", type=Path, required=True)
    manifest.add_argument("--pages", type=int, default=600)
    manifest.add_argument("--seed", type=int, default=SEED)
    manifest.add_argument("--archive-sha256")
    manifest.add_argument("--archive-url", default=SOURCE_URL)
    manifest.add_argument("--archive-size", type=int, default=ARCHIVE_SIZE)
    manifest.add_argument("--archive-last-modified", default=ARCHIVE_LAST_MODIFIED)
    compose = subparsers.add_parser(
        "compose", help="match Kuzushiji tile exposure to a Chinese control"
    )
    compose.add_argument("--base", type=Path, required=True)
    compose.add_argument("--control", type=Path, required=True)
    compose.add_argument("--additional", type=Path, required=True)
    compose.add_argument("--out", type=Path, required=True)
    compose.add_argument("--seed", type=int, default=SEED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "manifest":
        result = build_manifest(
            args.archive.resolve() if args.archive else None,
            args.out.resolve(),
            page_count=args.pages,
            seed=args.seed,
            expected_archive_sha256=args.archive_sha256,
            archive_url=args.archive_url if args.remote else None,
            archive_size=args.archive_size if args.remote else None,
            archive_last_modified=args.archive_last_modified,
        )
    else:
        result = compose_matched_dataset(
            args.base.resolve(),
            args.control.resolve(),
            args.additional.resolve(),
            args.out.resolve(),
            seed=args.seed,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
