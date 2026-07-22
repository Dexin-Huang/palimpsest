"""Prepare source-grounded P.3477 crops for the style-transfer experiment.

The page transcription supplies candidate character sequences. A visual dynamic
program aligns those sequences to detected ink columns and cells. The resulting
labels are explicitly silver evidence: generated model pixels are never used to
create or validate them. The review sheet and manifest retain the source bbox,
transcription provenance, and alignment score for every crop.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from palimpsest.factory import glyphs

ROOT = Path(__file__).parents[2]
DOC = ROOT / "library" / "gallica_pelliot_chinois_3477"
OUT = Path(__file__).parent / "out"
CROP_DIR = OUT / "crops"
FONT_PATH = Path("C:/Windows/Fonts/simkai.ttf")
REFERENCE_PAGE = "page_0000"
HELD_OUT_PAGE = "page_0001"
PAGES = (REFERENCE_PAGE, HELD_OUT_PAGE)
CANVAS = 128
MERGE_MAX = 3
SKIP_CELL_COST = 0.78
SKIP_CHAR_COST = 0.86
SKIP_COLUMN_COST = 0.95
SKIP_LINE_COST = 0.95


@dataclass(frozen=True, slots=True)
class InkCell:
    bbox: tuple[int, int, int, int]
    feature: np.ndarray
    gray: np.ndarray


@dataclass(frozen=True, slots=True)
class CellMatch:
    char_index: int
    bbox: tuple[int, int, int, int] | None
    cost: float
    consumed: int
    gray: np.ndarray | None


def is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_ink(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray | None:
    x, y, width, height = bbox
    pad = max(2, int(max(width, height) * 0.08))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1 = min(image.shape[1], x + width + pad)
    y1 = min(image.shape[0], y + height + pad)
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    components, labels, stats, centroids = cv2.connectedComponentsWithStats(ink, 8)
    keep = np.zeros(components, dtype=bool)
    core_x, core_y = x - x0, y - y0
    for index in range(1, components):
        center_x, center_y = centroids[index]
        in_core = (
            core_x - 3 <= center_x <= core_x + width + 3
            and core_y - 3 <= center_y <= core_y + height + 3
        )
        keep[index] = in_core and stats[index, cv2.CC_STAT_AREA] >= 4
    ink = np.where(keep[labels], np.uint8(255), np.uint8(0))
    points_y, points_x = np.nonzero(ink)
    if not len(points_x):
        return None
    tight = ink[points_y.min() : points_y.max() + 1, points_x.min() : points_x.max() + 1]
    fill = float(np.count_nonzero(tight)) / tight.size
    if fill < 0.025 or fill > 0.72:
        return None
    scale = (CANVAS - 16) / max(tight.shape)
    resized = cv2.resize(
        tight,
        (
            max(1, round(tight.shape[1] * scale)),
            max(1, round(tight.shape[0] * scale)),
        ),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.zeros((CANVAS, CANVAS), dtype=np.uint8)
    offset_y = (CANVAS - resized.shape[0]) // 2
    offset_x = (CANVAS - resized.shape[1]) // 2
    canvas[offset_y : offset_y + resized.shape[0], offset_x : offset_x + resized.shape[1]] = resized
    return 255 - canvas


def shape_feature(gray: np.ndarray) -> np.ndarray:
    small = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    ink = np.float32(255 - small) / 255.0
    gx = cv2.Sobel(ink, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(ink, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy)
    angle = (cv2.phase(gx, gy, angleInDegrees=True) % 180.0) / 22.5
    cells: list[np.ndarray] = []
    for y in range(0, 64, 8):
        for x in range(0, 64, 8):
            bins = np.zeros(8, dtype=np.float32)
            local_angle = angle[y : y + 8, x : x + 8].ravel()
            local_magnitude = magnitude[y : y + 8, x : x + 8].ravel()
            np.add.at(bins, np.floor(local_angle).astype(int) % 8, local_magnitude)
            cells.append(bins)
    density = cv2.resize(ink, (8, 8), interpolation=cv2.INTER_AREA).ravel()
    feature = np.concatenate([*cells, density]).astype(np.float32)
    norm = float(np.linalg.norm(feature))
    return feature / norm if norm else feature


@lru_cache(maxsize=None)
def rendered_template(char: str) -> tuple[np.ndarray, np.ndarray]:
    font = ImageFont.truetype(str(FONT_PATH), 112)
    image = Image.new("L", (CANVAS, CANVAS), 255)
    draw = ImageDraw.Draw(image)
    bounds = draw.textbbox((0, 0), char, font=font)
    x = (CANVAS - (bounds[2] - bounds[0])) // 2 - bounds[0]
    y = (CANVAS - (bounds[3] - bounds[1])) // 2 - bounds[1]
    draw.text((x, y), char, fill=0, font=font)
    gray = np.asarray(image)
    return gray, shape_feature(gray)


def fuse_bbox(cells: list[glyphs.Cell], start: int, consumed: int) -> tuple[int, int, int, int]:
    fused = cells[start]
    for index in range(start + 1, start + consumed):
        fused = fused.fuse(cells[index])
    return tuple(fused.bbox())


def prepare_cells(image: np.ndarray, cells: list[glyphs.Cell]) -> dict[tuple[int, int], InkCell | None]:
    prepared: dict[tuple[int, int], InkCell | None] = {}
    for start in range(len(cells)):
        for consumed in range(1, min(MERGE_MAX, len(cells) - start) + 1):
            bbox = fuse_bbox(cells, start, consumed)
            gray = normalize_ink(image, bbox)
            prepared[(start, consumed)] = (
                InkCell(bbox, shape_feature(gray), gray) if gray is not None else None
            )
    return prepared


def align_cells(
    image: np.ndarray,
    cells: list[glyphs.Cell],
    chars: list[str],
    glyph_height: float,
    prepared: dict[tuple[int, int], InkCell | None] | None = None,
) -> tuple[float, list[CellMatch]]:
    if prepared is None:
        prepared = prepare_cells(image, cells)
    n_cells, n_chars = len(cells), len(chars)
    costs = np.full((n_cells + 1, n_chars + 1), np.inf, dtype=np.float32)
    previous: dict[tuple[int, int], tuple[int, int, int, float]] = {}
    costs[0, 0] = 0.0

    def advance(
        cell_index: int,
        char_index: int,
        next_cell: int,
        next_char: int,
        candidate: float,
        consumed: int,
        local_cost: float,
    ) -> None:
        if candidate < costs[next_cell, next_char]:
            costs[next_cell, next_char] = candidate
            previous[(next_cell, next_char)] = (
                cell_index,
                char_index,
                consumed,
                local_cost,
            )

    for cell_index in range(n_cells + 1):
        for char_index in range(n_chars + 1):
            current = float(costs[cell_index, char_index])
            if not np.isfinite(current):
                continue
            if char_index < n_chars:
                advance(
                    cell_index,
                    char_index,
                    cell_index,
                    char_index + 1,
                    current + SKIP_CHAR_COST,
                    0,
                    SKIP_CHAR_COST,
                )
            if cell_index < n_cells:
                advance(
                    cell_index,
                    char_index,
                    cell_index + 1,
                    char_index,
                    current + SKIP_CELL_COST,
                    -1,
                    SKIP_CELL_COST,
                )
            if char_index >= n_chars:
                continue
            template_feature = rendered_template(chars[char_index])[1]
            for consumed in range(1, min(MERGE_MAX, n_cells - cell_index) + 1):
                ink_cell = prepared[(cell_index, consumed)]
                if ink_cell is None:
                    continue
                cosine = float(np.dot(ink_cell.feature, template_feature))
                height_cost = abs(ink_cell.bbox[3] - glyph_height) / max(glyph_height, 1.0)
                local_cost = 1.0 - cosine + 0.12 * height_cost + 0.08 * (consumed - 1)
                advance(
                    cell_index,
                    char_index,
                    cell_index + consumed,
                    char_index + 1,
                    current + local_cost,
                    consumed,
                    local_cost,
                )

    matches: list[CellMatch] = []
    cell_index, char_index = n_cells, n_chars
    while (cell_index, char_index) != (0, 0):
        prior_cell, prior_char, consumed, local_cost = previous[(cell_index, char_index)]
        if consumed == 0:
            matches.append(CellMatch(prior_char, None, local_cost, consumed, None))
        elif consumed > 0:
            ink_cell = prepared[(prior_cell, consumed)]
            assert ink_cell is not None
            matches.append(
                CellMatch(prior_char, ink_cell.bbox, local_cost, consumed, ink_cell.gray)
            )
        cell_index, char_index = prior_cell, prior_char
    matches.reverse()
    normalized_cost = float(costs[n_cells, n_chars]) / max(n_cells, n_chars, 1)
    return normalized_cost, matches


def detect_columns(image: np.ndarray) -> tuple[list[list[glyphs.Cell]], float]:
    mask = glyphs.binarize(image)
    rough = glyphs.ink_blobs(mask)
    pitch = glyphs._column_pitch(mask)
    glyph_height = pitch * glyphs._GLYPH_OF_PITCH if pitch else glyphs._main_glyph_height(rough)
    fuse = max(3, int(glyph_height * 0.15))
    closed = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((fuse, fuse), dtype=np.uint8),
    )
    blobs = glyphs.ink_blobs(closed)
    main = [blob for blob in blobs if blob.h >= glyph_height * glyphs._SMALL_GLYPH_FRAC]
    bands = glyphs.column_bands(closed, glyph_height)
    columns = [
        column
        for column in (glyphs._band_cells(main, band, glyph_height) for band in bands)
        if column
    ]
    columns.sort(key=lambda column: -max(cell.x1 for cell in column))
    return columns, glyph_height


def pair_columns(
    image: np.ndarray,
    columns: list[list[glyphs.Cell]],
    lines: list[list[str]],
    glyph_height: float,
) -> tuple[list[tuple[int | None, int | None]], dict[tuple[int, int], tuple[float, list[CellMatch]]]]:
    prepared_columns = [prepare_cells(image, column) for column in columns]
    comparisons = {
        (column_index, line_index): align_cells(
            image,
            column,
            line,
            glyph_height,
            prepared_columns[column_index],
        )
        for column_index, column in enumerate(columns)
        for line_index, line in enumerate(lines)
    }
    n_columns, n_lines = len(columns), len(lines)
    costs = np.full((n_columns + 1, n_lines + 1), np.inf, dtype=np.float32)
    previous: dict[tuple[int, int], tuple[int, int, str]] = {}
    costs[0, 0] = 0.0
    for column_index in range(n_columns + 1):
        for line_index in range(n_lines + 1):
            current = float(costs[column_index, line_index])
            if not np.isfinite(current):
                continue
            candidates: list[tuple[int, int, float, str]] = []
            if column_index < n_columns:
                candidates.append(
                    (column_index + 1, line_index, current + SKIP_COLUMN_COST, "skip_column")
                )
            if line_index < n_lines:
                candidates.append(
                    (column_index, line_index + 1, current + SKIP_LINE_COST, "skip_line")
                )
            if column_index < n_columns and line_index < n_lines:
                match_cost = comparisons[(column_index, line_index)][0]
                candidates.append(
                    (column_index + 1, line_index + 1, current + match_cost, "match")
                )
            for next_column, next_line, candidate, operation in candidates:
                if candidate < costs[next_column, next_line]:
                    costs[next_column, next_line] = candidate
                    previous[(next_column, next_line)] = (
                        column_index,
                        line_index,
                        operation,
                    )
    pairs: list[tuple[int | None, int | None]] = []
    column_index, line_index = n_columns, n_lines
    while (column_index, line_index) != (0, 0):
        prior_column, prior_line, operation = previous[(column_index, line_index)]
        if operation == "match":
            pairs.append((prior_column, prior_line))
        elif operation == "skip_column":
            pairs.append((prior_column, None))
        else:
            pairs.append((None, prior_line))
        column_index, line_index = prior_column, prior_line
    pairs.reverse()
    return pairs, comparisons


def read_lines(page_id: str) -> tuple[list[list[str]], dict]:
    path = DOC / "page_transcription" / f"{page_id}.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    lines = [[char for char in line if is_cjk(char)] for line in artifact["text"].splitlines()]
    return [line for line in lines if line], artifact


def prepare_page(page_id: str) -> tuple[list[dict], dict]:
    image_path = DOC / "page_image_clean" / f"{page_id}.jpg"
    transcription_path = DOC / "page_transcription" / f"{page_id}.json"
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    lines, transcription = read_lines(page_id)
    columns, glyph_height = detect_columns(image)
    pairs, comparisons = pair_columns(image, columns, lines, glyph_height)
    records: list[dict] = []
    matched_columns = 0
    for column_index, line_index in pairs:
        if column_index is None or line_index is None:
            continue
        matched_columns += 1
        line_cost, matches = comparisons[(column_index, line_index)]
        chars = lines[line_index]
        for match in matches:
            if match.bbox is None or match.gray is None:
                continue
            char = chars[match.char_index]
            crop_id = f"{page_id}_c{column_index:02d}_l{line_index:02d}_p{match.char_index:02d}"
            crop_path = CROP_DIR / page_id / f"{crop_id}.png"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(crop_path), match.gray)
            records.append(
                {
                    "crop_id": crop_id,
                    "page_id": page_id,
                    "column_index": column_index,
                    "line_index": line_index,
                    "char_index": match.char_index,
                    "claimed_char": char,
                    "bbox": list(match.bbox),
                    "crop_path": crop_path.relative_to(OUT).as_posix(),
                    "cell_cost": round(match.cost, 6),
                    "line_cost": round(line_cost, 6),
                    "consumed_cells": match.consumed,
                    "label_status": "silver_visual_sequence_alignment",
                }
            )
    page_record = {
        "page_id": page_id,
        "role": "writer_reference" if page_id == REFERENCE_PAGE else "held_out_real_ink",
        "image_path": image_path.relative_to(ROOT).as_posix(),
        "image_sha256": sha256(image_path),
        "transcription_path": transcription_path.relative_to(ROOT).as_posix(),
        "transcription_sha256": sha256(transcription_path),
        "transcription_provenance": transcription.get("provenance"),
        "detected_columns": len(columns),
        "transcription_lines": len(lines),
        "matched_columns": matched_columns,
        "crop_count": len(records),
    }
    return records, page_record


def leave_one_out_purity(records: list[dict]) -> dict:
    features = {
        record["crop_id"]: shape_feature(cv2.imread(str(OUT / record["crop_path"]), cv2.IMREAD_GRAYSCALE))
        for record in records
    }
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["claimed_char"], []).append(record)
    eligible = {char: items for char, items in grouped.items() if len(items) >= 2}
    correct = 0
    total = 0
    for char, items in eligible.items():
        for held_out in items:
            templates: dict[str, np.ndarray] = {}
            for candidate, candidate_items in eligible.items():
                available = [
                    features[item["crop_id"]]
                    for item in candidate_items
                    if item["crop_id"] != held_out["crop_id"]
                ]
                if available:
                    template = np.mean(available, axis=0)
                    norm = np.linalg.norm(template)
                    templates[candidate] = template / norm if norm else template
            if char not in templates:
                continue
            query = features[held_out["crop_id"]]
            predicted = max(templates, key=lambda candidate: float(np.dot(query, templates[candidate])))
            correct += predicted == char
            total += 1
    return {
        "correct": correct,
        "total": total,
        "accuracy": correct / total if total else None,
        "eligible_classes": len(eligible),
    }


def render_review_sheet(records: list[dict]) -> Path:
    sample = sorted(records, key=lambda record: (record["cell_cost"], record["crop_id"]))[:96]
    tile = 144
    columns = 12
    rows = (len(sample) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile, rows * (tile + 30)), "#e9e5dc")
    label_font = ImageFont.truetype(str(FONT_PATH), 24)
    draw = ImageDraw.Draw(sheet)
    for index, record in enumerate(sample):
        x = (index % columns) * tile
        y = (index // columns) * (tile + 30)
        crop = Image.open(OUT / record["crop_path"]).convert("L").resize((tile, tile))
        sheet.paste(crop.convert("RGB"), (x, y))
        draw.text((x + 4, y + tile + 1), record["claimed_char"], fill="#222222", font=label_font)
        draw.text((x + 38, y + tile + 7), f"{record['cell_cost']:.2f}", fill="#655e55")
    path = OUT / "silver_review.png"
    sheet.save(path)
    return path


def main() -> None:
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    all_records: list[dict] = []
    pages: list[dict] = []
    for page_id in PAGES:
        records, page = prepare_page(page_id)
        all_records.extend(records)
        pages.append(page)
        print(
            f"{page_id}: {page['matched_columns']}/{page['detected_columns']} columns, "
            f"{page['crop_count']} crops"
        )
    reference_chars = {
        record["claimed_char"] for record in all_records if record["page_id"] == REFERENCE_PAGE
    }
    held_out = [record for record in all_records if record["page_id"] == HELD_OUT_PAGE]
    unseen = [record for record in held_out if record["claimed_char"] not in reference_chars]
    purity = leave_one_out_purity(all_records)
    manifest = {
        "schema_version": 1,
        "experiment": "scribe-template-retrieval-v1",
        "evidence_status": "silver_not_human_adjudicated",
        "label_source": "independent read transcription aligned to source ink with generic Kai visual costs",
        "generated_pixels_are_labels": False,
        "reference_page": REFERENCE_PAGE,
        "held_out_page": HELD_OUT_PAGE,
        "pages": pages,
        "records": all_records,
        "summary": {
            "total_crops": len(all_records),
            "reference_crops": len(all_records) - len(held_out),
            "held_out_crops": len(held_out),
            "held_out_unseen_crops": len(unseen),
            "held_out_unseen_characters": len({record["claimed_char"] for record in unseen}),
            "leave_one_out_purity": purity,
        },
    }
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    review_path = render_review_sheet(all_records)
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    print(f"manifest: {manifest_path}")
    print(f"review: {review_path}")


if __name__ == "__main__":
    main()
