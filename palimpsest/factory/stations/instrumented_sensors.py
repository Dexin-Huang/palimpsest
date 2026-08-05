"""Instrument sensors for the instrumented transcription rig.

One module owns the measured sensor stack (experiments 24-29 in the exodia
research journal): reading-order column geometry, gap-cost count alignment,
similarity-paired independent readings with disagreement spans, classifier
witness disputes, and the dossier staged for the foreman session.

The wave research driver in the exodia repository carries the same logic for
the R_train lane; this module is the production home consumed by the
``omp_instrumented`` station variant.
"""

from __future__ import annotations

import json
import statistics
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from palimpsest.factory.recognized_text import normalize_recognized_text_v2

# Columns are separated by clear gutters wider than this fraction of the
# median box width; registers by y-gaps taller than this multiple of the
# median box height. Values match the qualified overlay-QA geometry.
COLUMN_GAP_FRACTION = 0.45
REGISTER_GAP_FACTOR = 1.8

# Classifier dispute operating point (experiment 29: conf30/unknown05).
CLASSIFIER_DISAGREE_CONFIDENCE = 0.3
CLASSIFIER_UNKNOWN_CONFIDENCE = 0.05


def normalize_counted(text: str) -> str:
    """Characters that should correspond to detected boxes: no whitespace."""

    normalized = unicodedata.normalize(
        "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return "".join(character for character in normalized if not character.isspace())


def squash_spaces(text: str) -> str:
    return "".join(text.split())


def split_registers(boxes: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    """Split boxes into horizontal bands separated by page-wide y-gaps."""

    if not boxes:
        return []
    median_height = statistics.median(box["h"] for box in boxes)
    ordered = sorted(boxes, key=lambda box: box["y"])
    registers: list[list[dict[str, float]]] = [[ordered[0]]]
    register_bottom = ordered[0]["y"] + ordered[0]["h"]
    for box in ordered[1:]:
        if box["y"] - register_bottom > REGISTER_GAP_FACTOR * median_height:
            registers.append([box])
        else:
            registers[-1].append(box)
        register_bottom = max(register_bottom, box["y"] + box["h"])
    return registers


def split_columns(register: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    """Split one register into vertical columns by x-center gaps."""

    median_width = statistics.median(box["w"] for box in register)
    ordered = sorted(register, key=lambda box: -(box["x"] + box["w"] / 2))
    columns: list[list[dict[str, float]]] = [[ordered[0]]]
    previous_center = ordered[0]["x"] + ordered[0]["w"] / 2
    for box in ordered[1:]:
        center = box["x"] + box["w"] / 2
        if previous_center - center > COLUMN_GAP_FRACTION * median_width:
            columns.append([box])
        else:
            columns[-1].append(box)
        previous_center = center
    return [sorted(column, key=lambda box: box["y"]) for column in columns]


def reading_order_columns(characters: list[dict]) -> list[dict]:
    """Detected boxes grouped into reading-order columns with bboxes."""

    boxes = [
        {
            "x": float(bbox[0]),
            "y": float(bbox[1]),
            "w": float(bbox[2]),
            "h": float(bbox[3]),
        }
        for character in characters
        for bbox in [character["bbox"]]
    ]
    columns: list[dict] = []
    for register in split_registers(boxes):
        for column in split_columns(register):
            columns.append(
                {
                    "boxes": len(column),
                    "bbox": [
                        round(min(box["x"] for box in column), 1),
                        round(min(box["y"] for box in column), 1),
                        round(
                            max(box["x"] + box["w"] for box in column)
                            - min(box["x"] for box in column),
                            1,
                        ),
                        round(
                            max(box["y"] + box["h"] for box in column)
                            - min(box["y"] for box in column),
                            1,
                        ),
                    ],
                }
            )
    return columns


def align_columns_to_lines(
    counts: list[int], lengths: list[int]
) -> tuple[list[int | None], str]:
    """Map each transcript line to a detected column index, or None.

    Index alignment breaks when the detector sees marginal or title columns
    the transcript orders differently. Minimize absolute count differences
    with a gap cost so extra or missing columns are skipped instead of
    shifting every following line.
    """
    if len(counts) == len(lengths):
        return list(range(len(counts))), "index"
    gap = 4
    rows, cols = len(counts), len(lengths)
    cost = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        cost[i][0] = i * gap
    for j in range(1, cols + 1):
        cost[0][j] = j * gap
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + abs(counts[i - 1] - lengths[j - 1]),
                cost[i - 1][j] + gap,
                cost[i][j - 1] + gap,
            )
    aligned: list[int | None] = [None] * cols
    i, j = rows, cols
    while i > 0 and j > 0:
        if cost[i][j] == cost[i - 1][j - 1] + abs(counts[i - 1] - lengths[j - 1]):
            aligned[j - 1] = i - 1
            i -= 1
            j -= 1
        elif cost[i][j] == cost[i - 1][j] + gap:
            i -= 1
        else:
            j -= 1
    return aligned, f"realigned {rows} detected columns to {cols} lines"


def pair_alternate_lines(base_lines: list[str], alternate: str) -> dict[int, str]:
    """Pair an alternate reading's lines to base line indices.

    Independent readings segment lines differently, and near-identical lines
    rarely match exactly, so opcode blocks span whole pages. Within each
    replace block, align lines monotonically by character similarity and
    accept only pairs that plausibly transcribe the same physical column.
    """
    alternate_lines = alternate.splitlines()
    matcher = SequenceMatcher(None, base_lines, alternate_lines, autojunk=False)
    paired: dict[int, str] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        rows, cols = i2 - i1, j2 - j1
        ratio = [
            [
                SequenceMatcher(
                    None, base_lines[i1 + a], alternate_lines[j1 + b], autojunk=False
                ).ratio()
                for b in range(cols)
            ]
            for a in range(rows)
        ]
        best = [[0.0] * (cols + 1) for _ in range(rows + 1)]
        for a in range(1, rows + 1):
            for b in range(1, cols + 1):
                best[a][b] = max(
                    best[a - 1][b],
                    best[a][b - 1],
                    best[a - 1][b - 1] + ratio[a - 1][b - 1],
                )
        a, b = rows, cols
        while a > 0 and b > 0:
            if best[a][b] == best[a - 1][b - 1] + ratio[a - 1][b - 1]:
                if ratio[a - 1][b - 1] >= 0.5:
                    paired[i1 + a - 1] = alternate_lines[j1 + b - 1]
                a -= 1
                b -= 1
            elif best[a][b] == best[a - 1][b]:
                a -= 1
            else:
                b -= 1
    return paired


def diff_spans(base_line: str, alternative: str, limit: int = 6) -> list[dict]:
    spans = []
    base_squashed = squash_spaces(base_line)
    alt_squashed = squash_spaces(alternative)
    matcher = SequenceMatcher(None, base_squashed, alt_squashed, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        spans.append({"base": base_squashed[i1:i2], "alternative": alt_squashed[j1:j2]})
        if len(spans) >= limit:
            break
    return spans


def compute_sensors(
    base_text: str,
    characters: list[dict] | None,
    alternates: list[str],
    classifier_columns: list[list[list]] | None = None,
) -> tuple[dict, dict]:
    """Instrument evidence per line plus page-level flags.

    Measured behavior (exodia experiments 24-29): count anchor covers 74
    percent of omission and invention mass; classifier disputes recall 71
    percent of error lines at 4 percent false flags; similarity-paired
    independent readings supply exact disagreement spans.
    """
    base_lines = base_text.splitlines()
    counted_lengths = [len(normalize_counted(line)) for line in base_lines]
    aligned_columns: list[int | None] = [None] * len(base_lines)
    columns: list[dict] = []
    alignment = "no_detections"
    if characters:
        columns = reading_order_columns(characters)
        aligned_columns, alignment = align_columns_to_lines(
            [column["boxes"] for column in columns], counted_lengths
        )
    paired_alternates = [
        pair_alternate_lines(base_lines, alternate) for alternate in alternates
    ]
    sensor_rows = []
    mismatches = 0
    disagreements = 0
    classifier_disputes = 0
    for index, line in enumerate(base_lines):
        row: dict = {
            "line": index + 1,
            "text": line,
            "characters": counted_lengths[index],
        }
        column_index = aligned_columns[index]
        if column_index is not None:
            column = columns[column_index]
            row["detector_box_count"] = column["boxes"]
            row["count_mismatch"] = column["boxes"] != counted_lengths[index]
            row["_bbox"] = column["bbox"]
            mismatches += row["count_mismatch"]
            verdict_column = (
                classifier_columns[column_index]
                if classifier_columns is not None
                and column_index < len(classifier_columns)
                else None
            )
            if (
                verdict_column is not None
                and len(verdict_column) == counted_lengths[index]
            ):
                counted_line = squash_spaces(line)
                disputes = []
                for char_index, top in enumerate(verdict_column):
                    label, confidence = top[0][0], float(top[0][1])
                    transcript_char = counted_line[char_index]
                    disagree = (
                        normalize_recognized_text_v2(label)
                        != normalize_recognized_text_v2(transcript_char)
                        and confidence >= CLASSIFIER_DISAGREE_CONFIDENCE
                    )
                    unknown = confidence < CLASSIFIER_UNKNOWN_CONFIDENCE
                    if disagree or unknown:
                        disputes.append(
                            {
                                "position": char_index + 1,
                                "transcript": transcript_char,
                                "classifier_top": top,
                            }
                        )
                if disputes:
                    row["classifier"] = disputes[:8]
                    classifier_disputes += 1
        alternatives = sorted(
            {
                paired[index]
                for paired in paired_alternates
                if index in paired
                and squash_spaces(paired[index]) != squash_spaces(line)
            }
        )
        if alternatives:
            row["independent_readings_differ"] = alternatives
            row["disagreement_spans"] = [
                span
                for alternative in alternatives
                for span in diff_spans(line, alternative)
            ][:8]
            disagreements += 1
        sensor_rows.append(row)
    sensors = {
        "note": (
            "Detector box counts follow physical reading order (registers top "
            "to bottom, columns right to left). Alignment to base lines is "
            "recorded below; realigned or missing entries deserve less trust. "
            "disagreement_spans give the exact disputed characters per line; "
            "classifier entries carry an independent stroke-classifier's top "
            "candidates with confidences; flagged lines may carry a magnified "
            "strip image under crops/."
        ),
        "alignment": alignment,
        "base_lines": len(base_lines),
        "lines": sensor_rows,
    }
    flags = {
        "count_mismatch_lines": mismatches,
        "disagreement_lines": disagreements,
        "classifier_dispute_lines": classifier_disputes,
        "alignment": alignment,
    }
    return sensors, flags


def is_quiet(flags: dict, quiet_max_disagreements: int) -> bool:
    """Quiet pages adopt the base text without a foreman session."""

    return (
        quiet_max_disagreements >= 0
        and flags["count_mismatch_lines"] == 0
        and flags["classifier_dispute_lines"] == 0
        and flags["disagreement_lines"] <= quiet_max_disagreements
    )


def write_dossier(
    workspace: Path, context_lines: list[str], base_text: str, sensors: dict, image: Path
) -> None:
    """Stage the foreman dossier: base text, sensors, context, and line crops."""

    tools_dir = workspace / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "base.txt").write_text(base_text, encoding="utf-8", newline="\n")

    flagged = [
        row
        for row in sensors["lines"]
        if "_bbox" in row
        and (
            row.get("count_mismatch")
            or row.get("independent_readings_differ")
            or row.get("classifier")
        )
    ]
    if flagged:
        import cv2
        import numpy as np

        page_pixels = cv2.imdecode(
            np.frombuffer(image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if page_pixels is not None:
            crops_dir = tools_dir / "crops"
            crops_dir.mkdir(exist_ok=True)
            height, width = page_pixels.shape[:2]
            for row in flagged:
                x, y, w, h = row["_bbox"]
                pad = 0.6 * max(w / max(1.0, row.get("detector_box_count", 1)), 24.0)
                x1 = max(0, int(x - pad))
                y1 = max(0, int(y - pad))
                x2 = min(width, int(x + w + pad))
                y2 = min(height, int(y + h + pad))
                if x2 <= x1 or y2 <= y1:
                    continue
                strip = page_pixels[y1:y2, x1:x2]
                strip = cv2.resize(
                    strip,
                    (strip.shape[1] * 2, strip.shape[0] * 2),
                    interpolation=cv2.INTER_CUBIC,
                )
                ok, payload = cv2.imencode(
                    ".jpg", strip, [cv2.IMWRITE_JPEG_QUALITY, 92]
                )
                if ok:
                    name = f"line-{row['line']:02d}.jpg"
                    (crops_dir / name).write_bytes(payload.tobytes())
                    row["crop"] = f"crops/{name}"
    for row in sensors["lines"]:
        row.pop("_bbox", None)
    (tools_dir / "sensors.json").write_text(
        json.dumps(sensors, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (tools_dir / "context.md").write_text(
        "\n".join(context_lines) + "\n", encoding="utf-8", newline="\n"
    )


def load_jsonl_keyed(path: Path, value_field: str) -> dict[str, list]:
    """Load an append-only JSONL artifact keyed by case_id."""

    records: dict[str, list] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["case_id"]] = record[value_field]
    return records
