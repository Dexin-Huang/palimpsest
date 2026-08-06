"""Acquire immutable OCR benchmark assets without adding them to Git.

MTHv2 is restricted to non-commercial academic research. Running this command
requires an explicit acknowledgement of the upstream terms.
"""

from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
import shutil
import sys
import time
import re
import unicodedata
import urllib.error
import urllib.request
import urllib.parse
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

MTHV2_COMMIT = "c9bd9b1b4ee999dd0acafd226a8a901a7088d980"
MTHV2_INDEX_ROOT = (
    f"https://raw.githubusercontent.com/HCIILAB/MTHv2_Datasets_Release/{MTHV2_COMMIT}"
)
MTHV2_ARCHIVE_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1JOFWYmiM2Ljcn1qJII2yHSGNA_0eouaj&export=download&confirm=t"
)
MTHV2_ARCHIVE_SIZE = 4_932_243_090
CORPORA = ("TKH", "MTH1000", "MTH1200")
DEVELOPMENT_RESERVE_PER_CORPUS = 80
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = (
    REPOSITORY_ROOT / "library" / "evaluations" / "raw-assets" / "mthv2" / "v1"
)
ANCIENTDOC_REPOSITORY = "ByteDance/AncientDoc"
ANCIENTDOC_REVISION = "149c447ebff66792cee28e02000682820858f17b"
ANCIENTDOC_LABEL_SHA256 = (
    "4dc642a7b2ed1d6c156f2392ca8807bacef44e8ae821c2707cb79ed6cd3b74ca"
)
ANCIENTDOC_API_URL = (
    f"https://huggingface.co/api/datasets/{ANCIENTDOC_REPOSITORY}"
    f"/revision/{ANCIENTDOC_REVISION}"
)
ANCIENTDOC_RESOLVE_ROOT = (
    f"https://huggingface.co/datasets/{ANCIENTDOC_REPOSITORY}"
    f"/resolve/{ANCIENTDOC_REVISION}"
)
DEFAULT_ANCIENTDOC_ROOT = (
    REPOSITORY_ROOT / "library" / "evaluations" / "raw-assets" / "ancientdoc" / "v1"
)
_ANCIENTDOC_IMAGE_NAME = re.compile(
    r"(?P<book>.+?)(?P<page>page_\d+\.(?:png|jpg|jpeg))$", re.I
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_index(split: str) -> tuple[list[str], bytes]:
    url = f"{MTHV2_INDEX_ROOT}/{split}.txt"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    paths = [
        line.strip() for line in payload.decode("utf-8").splitlines() if line.strip()
    ]
    if not paths:
        raise RuntimeError(f"MTHv2 {split} index is empty")
    return paths, payload


def archive_image_path(source_path: str) -> str:
    parts = PurePosixPath(source_path).parts
    if len(parts) < 3 or parts[-3] not in CORPORA or parts[-2] != "img":
        raise ValueError(f"unexpected MTHv2 source path: {source_path}")
    return "/".join(parts[-3:])


def related_members(image_member: str) -> tuple[str, str, str]:
    path = PurePosixPath(image_member)
    stem = path.stem
    corpus = path.parts[0]
    return (
        image_member,
        f"{corpus}/label_textline/{stem}.txt",
        f"{corpus}/label_char/{stem}.txt",
    )


def ranked_corpus_paths(paths: list[str], corpus: str) -> list[str]:
    candidates = [
        path for path in paths if archive_image_path(path).startswith(f"{corpus}/")
    ]
    return sorted(candidates, key=lambda value: hashlib.sha256(value.encode()).digest())


def _select_balanced(
    paths: list[str],
    per_corpus: int,
    *,
    partition: str,
) -> list[str]:
    selected: list[str] = []
    for corpus in CORPORA:
        ranked = ranked_corpus_paths(paths, corpus)
        if len(ranked) < per_corpus:
            raise ValueError(
                f"requested {per_corpus} {partition} pages from {corpus}, "
                f"but only {len(ranked)} are available"
            )
        selected.extend(ranked[:per_corpus])
    return selected


def select_development(paths: list[str], per_corpus: int) -> list[str]:
    return _select_balanced(paths, per_corpus, partition="development")


def select_qualification(paths: list[str], per_corpus: int) -> list[str]:
    return _select_balanced(paths, per_corpus, partition="qualification")


def select_training(paths: list[str], per_corpus: int) -> list[str]:
    selected: list[str] = []
    for corpus in CORPORA:
        ranked = ranked_corpus_paths(paths, corpus)
        start = DEVELOPMENT_RESERVE_PER_CORPUS
        stop = start + per_corpus
        if len(ranked) < stop:
            raise ValueError(
                f"requested {per_corpus} training pages from {corpus} after "
                f"{start} reserved development pages, but only {len(ranked)} are available"
            )
        selected.extend(ranked[start:stop])
    return selected


def select_all_training(paths: list[str]) -> list[str]:
    selected: list[str] = []
    for corpus in CORPORA:
        ranked = ranked_corpus_paths(paths, corpus)
        selected.extend(ranked[DEVELOPMENT_RESERVE_PER_CORPUS:])
    return selected


def local_member_path(root: Path, member: str) -> Path:
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member: {member}")
    return root / "assets" / Path(*path.parts)


class HTTPRangeReader(io.RawIOBase):
    """Small seekable reader backed by validated HTTP byte ranges."""

    def __init__(self, url: str, size: int, block_size: int = 8 * 1024 * 1024):
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
                "User-Agent": "Palimpsest-MTHv2-Benchmark/1",
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
                if len(self.cache) >= 4:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[start] = payload
                return payload
            except (OSError, urllib.error.URLError) as caught:
                error = caught
                time.sleep(2**attempt)
        raise OSError(f"failed to fetch archive range {start}-{end}") from error


def extract_members(root: Path, members: set[str]) -> None:
    missing = {
        member for member in members if not local_member_path(root, member).exists()
    }
    if not missing:
        print(f"all {len(members)} selected archive members are already present")
        return

    local_archive = root / "TKHMTH2200.zip"
    if local_archive.exists() and local_archive.stat().st_size == MTHV2_ARCHIVE_SIZE:
        print(
            f"opening local archive {local_archive}; "
            f"{len(missing):,} selected members need extraction"
        )
        source_file = local_archive.open("rb")
    else:
        if local_archive.exists():
            print(
                f"ignoring incomplete local archive {local_archive} "
                f"({local_archive.stat().st_size:,}/{MTHV2_ARCHIVE_SIZE:,} bytes)"
            )
        print(
            f"opening the {MTHV2_ARCHIVE_SIZE:,}-byte remote archive by range; "
            f"{len(missing):,} selected members need extraction"
        )
        source_file = HTTPRangeReader(MTHV2_ARCHIVE_URL, MTHV2_ARCHIVE_SIZE)
    try:
        with zipfile.ZipFile(source_file) as archive:
            entries = {entry.filename: entry for entry in archive.infolist()}
            absent = sorted(missing - entries.keys())
            if absent:
                raise RuntimeError(f"archive is missing selected members: {absent[:5]}")
            ordered = sorted(
                (entries[name] for name in missing),
                key=lambda entry: entry.header_offset,
            )
            for index, entry in enumerate(ordered, start=1):
                destination = local_member_path(root, entry.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + ".partial")
                with archive.open(entry) as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                temporary.replace(destination)
                if index == 1 or index % 100 == 0 or index == len(ordered):
                    print(f"extracted {index:,}/{len(ordered):,}: {entry.filename}")
    finally:
        source_file.close()


def parse_text_lines(payload: str) -> tuple[str, list[dict[str, object]]]:
    lines: list[dict[str, object]] = []
    for line_number, raw in enumerate(payload.splitlines(), start=1):
        if not raw.strip():
            continue
        fields = raw.rsplit(",", 8)
        if len(fields) != 9:
            raise ValueError(
                f"invalid MTHv2 text-line label at line {line_number}: {raw!r}"
            )
        text = fields[0]
        points = [float(value) for value in fields[1:]]
        lines.append({"text": text, "polygon": points})
    return "\n".join(str(line["text"]) for line in lines), lines


def parse_characters(payload: str) -> list[dict[str, object]]:
    characters: list[dict[str, object]] = []
    for line_number, raw in enumerate(payload.splitlines(), start=1):
        if not raw.strip():
            continue
        fields = raw.rsplit(maxsplit=4)
        if len(fields) != 5:
            raise ValueError(
                f"invalid MTHv2 character label at line {line_number}: {raw!r}"
            )
        text, x0, y0, x1, y1 = fields
        left, top, right, bottom = (float(x0), float(y0), float(x1), float(y1))
        if right <= left or bottom <= top:
            raise ValueError(
                f"invalid MTHv2 character box at line {line_number}: {raw!r}"
            )
        characters.append(
            {"text": text, "bbox": [left, top, right - left, bottom - top]}
        )
    return characters


def repository_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_case(
    root: Path, source_path: str, split: str, rank: int
) -> dict[str, object]:
    image_member, text_member, char_member = related_members(
        archive_image_path(source_path)
    )
    image_path = local_member_path(root, image_member)
    text_path = local_member_path(root, text_member)
    char_path = local_member_path(root, char_member)
    text, text_lines = parse_text_lines(text_path.read_text(encoding="utf-8-sig"))
    characters = parse_characters(char_path.read_text(encoding="utf-8-sig"))
    corpus = image_member.split("/", 1)[0]
    stem = PurePosixPath(image_member).stem
    return {
        "schema_version": 1,
        "case_id": f"mthv2/{corpus}/{stem}",
        "dataset": "MTHv2",
        "split": split,
        "selection_rank": rank,
        "strata": [corpus, PurePosixPath(image_member).suffix.lower().lstrip(".")],
        "image": repository_relative(image_path),
        "source_path": source_path,
        "text": text,
        "text_lines": text_lines,
        "characters": characters,
        "sha256": {
            "image": sha256_file(image_path),
            "text_lines": sha256_file(text_path),
            "characters": sha256_file(char_path),
        },
    }


def validate_line_character_consistency(record: dict[str, object]) -> None:
    text_lines = record.get("text_lines")
    characters = record.get("characters")
    if not isinstance(text_lines, list) or not isinstance(characters, list):
        raise ValueError("case has invalid text-line or character annotations")
    for line in text_lines:
        if not isinstance(line, dict):
            raise ValueError("text line must be an object")
        polygon = line.get("polygon")
        text = line.get("text")
        if (
            not isinstance(polygon, list)
            or len(polygon) != 8
            or not isinstance(text, str)
        ):
            raise ValueError("text line has invalid text or polygon")
        xs = [float(value) for value in polygon[0::2]]
        ys = [float(value) for value in polygon[1::2]]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        selected = []
        for character in characters:
            if not isinstance(character, dict):
                raise ValueError("character annotation must be an object")
            bbox = character.get("bbox")
            glyph = character.get("text")
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not isinstance(glyph, str)
                or not glyph
            ):
                raise ValueError("character annotation has invalid text or bbox")
            x, y, width, height = (float(value) for value in bbox)
            if left <= x + width / 2 <= right and top <= y + height / 2 <= bottom:
                selected.append(character)
        selected.sort(key=lambda character: float(character["bbox"][1]))
        observed = "".join(str(character["text"]) for character in selected)
        if len(observed) != len(text) or unicodedata.normalize(
            "NFKC", observed
        ) != unicodedata.normalize("NFKC", text):
            raise ValueError(
                f"line annotation mismatch: expected {text!r}, got {observed!r}"
            )


def build_training_cases(
    root: Path,
    source_paths: list[str],
    *,
    protected: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    def identities(record: dict[str, object]) -> dict[str, str]:
        hashes = record.get("sha256")
        if not isinstance(hashes, dict):
            raise ValueError("case is missing content hashes")
        values = {
            "case_id": record.get("case_id"),
            "source_path": record.get("source_path"),
            "image": hashes.get("image"),
            "text_lines": hashes.get("text_lines"),
            "characters": hashes.get("characters"),
        }
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("case has an invalid identity")
        return values

    seen = {
        field: set()
        for field in ("case_id", "source_path", "image", "text_lines", "characters")
    }
    for record in protected or []:
        for field, value in identities(record).items():
            seen[field].add(value)

    cases: list[dict[str, object]] = []
    exclusions: list[dict[str, str]] = []
    for source_path in source_paths:
        try:
            case = build_case(root, source_path, "training", len(cases) + 1)
            case_identities = identities(case)
        except ValueError as error:
            exclusions.append({"source_path": source_path, "reason": str(error)})
            continue
        duplicate = next(
            (
                (field, value)
                for field, value in case_identities.items()
                if value in seen[field]
            ),
            None,
        )
        if duplicate is not None:
            field, value = duplicate
            exclusions.append(
                {
                    "source_path": source_path,
                    "reason": (
                        f"duplicate {field} identity with an earlier development "
                        f"or training page: {value}"
                    ),
                }
            )
            continue
        cases.append(case)
        for field, value in case_identities.items():
            seen[field].add(value)
    return cases, exclusions


def build_qualification_cases(
    root: Path,
    source_paths: list[str],
    per_corpus: int | None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    cases: list[dict[str, object]] = []
    exclusions: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for source_path in source_paths:
        corpus = archive_image_path(source_path).split("/", 1)[0]
        if per_corpus is not None and counts[corpus] >= per_corpus:
            continue
        try:
            case = build_case(root, source_path, "qualification", len(cases) + 1)
            validate_line_character_consistency(case)
        except ValueError as error:
            exclusions.append({"source_path": source_path, "reason": str(error)})
            continue
        cases.append(case)
        counts[corpus] += 1
    if per_corpus is not None:
        missing = {
            corpus: per_corpus - counts[corpus]
            for corpus in CORPORA
            if counts[corpus] < per_corpus
        }
        if missing:
            raise ValueError(
                f"qualification candidate pool lacks admissible pages: {missing}"
            )
    return cases, exclusions


def write_jsonl(path: Path, records: list[dict[str, object]]) -> str:
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return sha256_bytes(payload.encode("utf-8"))


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _ancientdoc_rank(*parts: str) -> bytes:
    identity = "\0".join((ANCIENTDOC_REVISION, *parts))
    return hashlib.sha256(identity.encode("utf-8")).digest()


def parse_ancientdoc_labels(
    payload: bytes, repository_files: list[str]
) -> list[dict[str, str]]:
    image_files = [
        path
        for path in repository_files
        if PurePosixPath(path).suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    records: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    required = {"type", "name", "OCR"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("AncientDoc label.csv lacks type, name, or OCR")
    for row_number, row in enumerate(reader, start=2):
        category = row["type"].strip()
        name = row["name"].strip()
        transcription = row["OCR"].strip()
        if not transcription:
            continue
        match = _ANCIENTDOC_IMAGE_NAME.fullmatch(name)
        if match is None:
            raise ValueError(
                f"invalid AncientDoc image name at row {row_number}: {name!r}"
            )
        book = match.group("book")
        page = match.group("page")
        suffix = f"/{category}/{book}/{page}"
        matches = [path for path in image_files if path.endswith(suffix)]
        if not matches:
            raise ValueError(
                f"no AncientDoc image matches label row {row_number}: {name!r}"
            )
        # The pinned source contains 385 duplicate paths under imgs/imgs. Prefer
        # the shallower canonical path; later byte hashing fixes its identity.
        source_path = min(matches, key=lambda value: (value.count("/"), value))
        records.append(
            {
                "category": category,
                "book": book,
                "page": page,
                "source_path": source_path,
                "transcription": transcription.replace("\\n", "\n"),
            }
        )
    if not records:
        raise ValueError("AncientDoc OCR labels are empty")
    return records


def select_ancientdoc_partitions(
    records: list[dict[str, str]], development_pages_per_category: int
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, dict[str, str]]]:
    if development_pages_per_category <= 0:
        raise ValueError("development_pages_per_category must be positive")
    categories = sorted({record["category"] for record in records})
    development: list[dict[str, str]] = []
    qualification: list[dict[str, str]] = []
    books: dict[str, dict[str, str]] = {}
    for category in categories:
        category_books = sorted(
            {record["book"] for record in records if record["category"] == category},
            key=lambda book: _ancientdoc_rank(category, book),
        )
        if len(category_books) < 2:
            raise ValueError(f"AncientDoc category {category!r} lacks two books")
        development_book, qualification_book = category_books[:2]
        development_pool = [
            record
            for record in records
            if record["category"] == category and record["book"] == development_book
        ]
        if len(development_pool) < development_pages_per_category:
            raise ValueError(
                f"AncientDoc development book {development_book!r} has only "
                f"{len(development_pool)} OCR pages"
            )
        development.extend(
            sorted(
                development_pool,
                key=lambda record: _ancientdoc_rank(record["source_path"]),
            )[:development_pages_per_category]
        )
        qualification.extend(
            sorted(
                (
                    record
                    for record in records
                    if record["category"] == category
                    and record["book"] == qualification_book
                ),
                key=lambda record: _ancientdoc_rank(record["source_path"]),
            )
        )
        books[category] = {
            "development": development_book,
            "qualification_reserve": qualification_book,
        }
    return development, qualification, books


def _ancientdoc_case_id(record: dict[str, str]) -> str:
    digest = hashlib.sha256(record["source_path"].encode("utf-8")).hexdigest()[:16]
    return f"ancientdoc/{digest}"


def _download_ancientdoc_image(root: Path, source_path: str) -> Path:
    destination = root / "assets" / PurePosixPath(source_path)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    url = f"{ANCIENTDOC_RESOLVE_ROOT}/{urllib.parse.quote(source_path, safe='/')}"
    payload = _fetch_bytes(url)
    if not payload:
        raise RuntimeError(f"AncientDoc image is empty: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    return destination


def acquire_ancientdoc(root: Path, development_pages_per_category: int) -> None:
    metadata_payload = _fetch_bytes(ANCIENTDOC_API_URL)
    source_metadata = json.loads(metadata_payload)
    if source_metadata.get("sha") != ANCIENTDOC_REVISION:
        raise RuntimeError("AncientDoc source revision changed during acquisition")
    repository_files = [
        item["rfilename"]
        for item in source_metadata.get("siblings", [])
        if isinstance(item, dict) and isinstance(item.get("rfilename"), str)
    ]
    label_payload = _fetch_bytes(f"{ANCIENTDOC_RESOLVE_ROOT}/label.csv")
    if sha256_bytes(label_payload) != ANCIENTDOC_LABEL_SHA256:
        raise RuntimeError("AncientDoc label.csv hash does not match the pinned source")
    records = parse_ancientdoc_labels(label_payload, repository_files)
    development, qualification_reserve, books = select_ancientdoc_partitions(
        records, development_pages_per_category
    )

    development_inputs: list[dict[str, object]] = []
    development_gold: list[dict[str, object]] = []
    for rank, record in enumerate(development, start=1):
        image_path = _download_ancientdoc_image(root, record["source_path"])
        base: dict[str, object] = {
            "schema_version": 1,
            "case_id": _ancientdoc_case_id(record),
            "dataset": "AncientDoc",
            "split": "development",
            "selection_rank": rank,
            "strata": [record["category"], record["book"]],
            "image": repository_relative(image_path),
            "source_path": record["source_path"],
            "sha256": {"image": sha256_file(image_path)},
        }
        development_inputs.append(base)
        development_gold.append({**base, "text": record["transcription"]})

    reserve_inputs = [
        {
            "schema_version": 1,
            "case_id": _ancientdoc_case_id(record),
            "dataset": "AncientDoc",
            "split": "qualification_reserve",
            "selection_rank": rank,
            "strata": [record["category"], record["book"]],
            "source_path": record["source_path"],
        }
        for rank, record in enumerate(qualification_reserve, start=1)
    ]
    manifest_hashes = {
        "development_inputs": write_jsonl(
            root / "manifests" / "development-inputs.jsonl", development_inputs
        ),
        "development_gold": write_jsonl(
            root / "manifests" / "development-gold.jsonl", development_gold
        ),
        "qualification_reserve": write_jsonl(
            root / "manifests" / "qualification-reserve-inputs.jsonl",
            reserve_inputs,
        ),
    }
    raw_path = root / "raw" / "label.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(label_payload)
    dataset = {
        "schema_version": 1,
        "dataset": "AncientDoc",
        "license": "CC0-1.0",
        "source_repository": f"https://huggingface.co/datasets/{ANCIENTDOC_REPOSITORY}",
        "source_revision": ANCIENTDOC_REVISION,
        "label_sha256": ANCIENTDOC_LABEL_SHA256,
        "label_representation_normalization": "literal backslash-n sequences converted to LF",
        "partition_policy": (
            "deterministic SHA-256 ranking; one development and one disjoint "
            "qualification-reserve book per semantic category"
        ),
        "books": books,
        "manifest_sha256": manifest_hashes,
        "counts": {
            "source_ocr_rows": len(records),
            "categories": len(books),
            "development": len(development_inputs),
            "qualification_reserve": len(reserve_inputs),
        },
    }
    (root / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(dataset["counts"], ensure_ascii=False, indent=2))
    print(f"AncientDoc benchmark ready at {root}")


def acquire_mthv2(
    root: Path,
    development_per_corpus: int,
    *,
    training_per_corpus: int = 0,
    all_training: bool = False,
    qualification_per_corpus: int | None = None,
    development_only: bool = False,
) -> None:
    train_paths, train_index = fetch_index("train")
    test_paths, test_index = fetch_index("test")
    development_paths = select_development(train_paths, development_per_corpus)
    training_paths = (
        select_all_training(train_paths)
        if all_training
        else select_training(train_paths, training_per_corpus)
        if training_per_corpus
        else []
    )
    qualification_paths = (
        []
        if development_only
        else select_qualification(test_paths, qualification_per_corpus * 2)
        if qualification_per_corpus is not None
        else sorted(test_paths, key=archive_image_path)
    )

    selected = development_paths + training_paths + qualification_paths
    members = {
        member
        for source_path in selected
        for member in related_members(archive_image_path(source_path))
    }
    extract_members(root, members)

    development = [
        build_case(root, source_path, "development", rank)
        for rank, source_path in enumerate(development_paths, start=1)
    ]
    if all_training:
        training, training_exclusions = build_training_cases(
            root, training_paths, protected=development
        )
    else:
        training = [
            build_case(root, source_path, "training", rank)
            for rank, source_path in enumerate(training_paths, start=1)
        ]
        training_exclusions = []
    qualification, qualification_exclusions = build_qualification_cases(
        root,
        qualification_paths,
        qualification_per_corpus,
    )
    manifests = root / "manifests"
    training_hash = (
        write_jsonl(manifests / "training.jsonl", training) if training else None
    )
    development_hash = write_jsonl(manifests / "development.jsonl", development)
    qualification_hash = (
        None
        if development_only
        else write_jsonl(manifests / "qualification.jsonl", qualification)
    )

    metadata = {
        "schema_version": 1,
        "dataset": "MTHv2",
        "terms": "non-commercial academic research; see official repository",
        "source_repository": "https://github.com/HCIILAB/MTHv2_Datasets_Release",
        "source_commit": MTHV2_COMMIT,
        "archive_url": MTHV2_ARCHIVE_URL,
        "archive_size": MTHV2_ARCHIVE_SIZE,
        "index_sha256": {
            "train": sha256_bytes(train_index),
            "test": sha256_bytes(test_index),
        },
        "manifest_sha256": {
            "development": development_hash,
            "training": training_hash,
            "qualification": qualification_hash,
        },
        "training_selection": {
            "requested": len(training_paths),
            "admissibility": "parseable positive-area official character boxes",
            "exclusions": training_exclusions,
        },
        "qualification_selection": {
            "requested_per_corpus": qualification_per_corpus,
            "candidate_pool_per_corpus": (
                qualification_per_corpus * 2
                if qualification_per_corpus is not None
                else None
            ),
            "admissibility": (
                "parseable positive-area official character boxes with "
                "NFKC-consistent text-line membership"
            ),
            "exclusions": qualification_exclusions,
        },
        "counts": {
            "training": len(training),
            "development": len(development),
            "qualification": len(qualification),
            "development_by_corpus": Counter(case["strata"][0] for case in development),
            "training_by_corpus": Counter(case["strata"][0] for case in training),
            "qualification_by_corpus": Counter(
                case["strata"][0] for case in qualification
            ),
        },
    }
    (root / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(metadata["counts"], indent=2))
    print(f"MTHv2 benchmark ready at {root}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="dataset", required=True)
    mthv2 = subparsers.add_parser("mthv2", help="acquire the pinned MTHv2 benchmark")
    ancientdoc = subparsers.add_parser(
        "ancientdoc", help="acquire the pinned CC0 AncientDoc OCR benchmark"
    )
    ancientdoc.add_argument("--root", type=Path, default=DEFAULT_ANCIENTDOC_ROOT)
    ancientdoc.add_argument("--development-pages-per-category", type=int, default=2)
    mthv2.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    mthv2.add_argument("--development-per-corpus", type=int, default=80)
    mthv2.add_argument("--training-per-corpus", type=int, default=0)
    mthv2.add_argument(
        "--all-training",
        action="store_true",
        help="select every training page after the reserved development pages",
    )
    mthv2.add_argument("--qualification-per-corpus", type=int)
    mthv2.add_argument(
        "--development-only",
        action="store_true",
        help="materialize the tuning split now; defer the sealed test images",
    )
    mthv2.add_argument("--accept-noncommercial-terms", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.dataset == "mthv2":
        if not args.accept_noncommercial_terms:
            raise SystemExit(
                "MTHv2 is limited to non-commercial academic research. Re-run with "
                "--accept-noncommercial-terms after reviewing the upstream repository terms."
            )
        if args.development_per_corpus < 0:
            raise SystemExit("--development-per-corpus cannot be negative")
        if (
            args.qualification_per_corpus is not None
            and args.qualification_per_corpus <= 0
        ):
            raise SystemExit("--qualification-per-corpus must be positive")
        if args.development_only and args.qualification_per_corpus is not None:
            raise SystemExit(
                "--qualification-per-corpus cannot be used with --development-only"
            )
        if args.training_per_corpus < 0:
            raise SystemExit("--training-per-corpus cannot be negative")
        if args.all_training and args.training_per_corpus:
            raise SystemExit(
                "--all-training cannot be combined with --training-per-corpus"
            )
        acquire_mthv2(
            args.root.resolve(),
            args.development_per_corpus,
            training_per_corpus=args.training_per_corpus,
            all_training=args.all_training,
            qualification_per_corpus=args.qualification_per_corpus,
            development_only=args.development_only,
        )
    elif args.dataset == "ancientdoc":
        if args.development_pages_per_category <= 0:
            raise SystemExit("--development-pages-per-category must be positive")
        acquire_ancientdoc(
            args.root.resolve(),
            args.development_pages_per_category,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
