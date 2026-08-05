"""Tool-bearing agent-cell transcription, fifth iteration: glyph adjudication.

``omp_toolbelt5`` is the ``omp_toolbelt3`` harness - identical agent surface,
task, extension, and evidence bytes - plus a deterministic post-submission
glyph adjudication pass: the production-legal wiring of the instrument
validated in ``transcribe_glyph_adjudication_development_v1`` (blind forced
choice on the character's own enlarged crop; fix rate 0.8135, control break
rate 0.0355 on MTHv2 real gold).

Production-legal means no gold anywhere:

- localization comes from the RF-DETR detector boxes already computed during
  geometry staging (kept in memory; the agent-visible evidence is unchanged);
- the trigger set is built from second-reader disagreements (align the
  sealed primary draft against the per-column second readings) plus a frozen
  variant watchlist at agreement positions;
- the choice set is the draft character versus the production-visible
  alternative (second reader's character, or the watchlist counterpart).

The pass patches the primary layer only, keeps the draft on ``neither``,
``illegible``, or gateway failure, and records full telemetry plus its own
gateway cost in the station result.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.usage import combine_cost, combine_count
from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ImageContent,
    ModelRequest,
)
from palimpsest.factory.stations.align_rfdetr import (
    _detected_columns,
    _parse_detections,
)
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import _extension_source_bytes
from palimpsest.factory.stations.transcribe_omp_toolbelt import (
    _checkpoint_path,
    _detect,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt2 import (
    _MAX_WIDTH_RATIO,
    _MIN_LAYER_FRACTION,
    _SECOND_READER_MODEL,
    _SECOND_READER_THINKING,
    _read_layered_submission,
    _read_second_opinion,
    _station_usage_with_second_reader,
    _two_split,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt3 import (
    _CROP_PAD_FRACTION,
    _OVERLAY_MAX_SIDE,
    _TASK,
    _TOOLBELT3_EXTENSION_BYTES,
    TRANSCRIPTION_TIMEOUT_SECONDS,
    _split_registers,
)

# Adjudication rides the same qwen route; its media_resolution/thinking_level
# request knobs keep their names and are mapped or ignored by the qwen path.
_ADJUDICATOR_MODEL = "token-plan/qwen3.8-max"
_ADJUDICATION_MAX_CALLS = 160
_ADJUDICATION_PAD_FRACTION = 0.3
_ADJUDICATION_MIN_SIDE = 256

# Frozen from the measured evidence of transcribe_glyph_adjudication_v1:
# confusion families seen at least twice among the 931 champion
# disagreements on MTHv2 real gold, kept only when the pair is mutual
# (each side is the other's most frequent counterpart) and the character's
# corpus frequency does not swamp the family signal (freq <= 8x family
# count). 128 characters, 64 families.
_WATCHLIST = {
    "㑹": "會",
    "㝡": "最",
    "㢤": "哉",
    "㽞": "留",
    "㽵": "莊",
    "䥫": "鐵",
    "万": "萬",
    "世": "丗",
    "丗": "世",
    "久": "乆",
    "乆": "久",
    "乗": "乘",
    "乘": "乗",
    "來": "来",
    "倚": "𠋣",
    "减": "減",
    "別": "别",
    "别": "別",
    "勅": "勑",
    "勑": "勅",
    "号": "號",
    "同": "笁",
    "哉": "㢤",
    "嘆": "歎",
    "嘗": "甞",
    "圎": "圓",
    "圓": "圎",
    "土": "圡",
    "圡": "土",
    "増": "增",
    "增": "増",
    "寳": "寶",
    "寶": "寳",
    "尒": "爾",
    "属": "屬",
    "屬": "属",
    "已": "巳",
    "巳": "已",
    "庾": "𢈔",
    "往": "徃",
    "徃": "往",
    "従": "從",
    "從": "従",
    "念": "𫝹",
    "惡": "𢙣",
    "惱": "𢙉",
    "敎": "教",
    "教": "敎",
    "日": "曰",
    "明": "眀",
    "曰": "日",
    "曽": "曾",
    "曾": "曽",
    "最": "㝡",
    "會": "㑹",
    "来": "來",
    "查": "査",
    "査": "查",
    "桐": "椒",
    "椒": "桐",
    "歎": "嘆",
    "歲": "𡻕",
    "減": "减",
    "為": "爲",
    "爲": "為",
    "爾": "尒",
    "甞": "嘗",
    "留": "㽞",
    "異": "護",
    "盖": "蓋",
    "眀": "明",
    "真": "靜",
    "眾": "衆",
    "礼": "禮",
    "禮": "礼",
    "笁": "同",
    "緣": "縁",
    "緫": "總",
    "縁": "緣",
    "總": "緫",
    "胝": "𦙁",
    "臘": "𫞇",
    "舍": "舎",
    "舎": "舍",
    "莊": "㽵",
    "萬": "万",
    "蓋": "盖",
    "藴": "蘊",
    "蘊": "藴",
    "處": "𠁅",
    "號": "号",
    "衆": "眾",
    "護": "異",
    "讃": "讚",
    "變": "震",
    "讚": "讃",
    "踊": "踴",
    "踴": "踊",
    "逰": "遊",
    "逹": "達",
    "遊": "逰",
    "達": "逹",
    "那": "𨚗",
    "釋": "𥼶",
    "鐵": "䥫",
    "閒": "間",
    "間": "閒",
    "陀": "陁",
    "陁": "陀",
    "震": "變",
    "靑": "青",
    "青": "靑",
    "靜": "真",
    "面": "靣",
    "靣": "面",
    "高": "髙",
    "髙": "高",
    "𠁅": "處",
    "𠋣": "倚",
    "𡻕": "歲",
    "𢈔": "庾",
    "𢙉": "惱",
    "𢙣": "惡",
    "𥼶": "釋",
    "𦙁": "胝",
    "𨚗": "那",
    "𫝹": "念",
    "𫞇": "臘",
}

_ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "written_form": {
            "type": "string",
            "enum": ["A", "B", "neither", "illegible"],
        },
        "reasoning": {"type": "string"},
    },
    "required": ["written_form", "reasoning"],
    "additionalProperties": False,
}

_ADJUDICATION_PROMPT = """This image shows ONE character cropped from a digitized premodern Chinese page.

Decide which exact written form appears, judging ONLY the visible strokes of the glyph as drawn on the page. Do not consider which form is more common in modern text, and do not consider meaning; compare stroke-level shape against each candidate codepoint's canonical glyph.

A: {form_a}
B: {form_b}

If the drawn glyph matches neither candidate form, answer neither. If the crop is too degraded or truncated to decide, answer illegible. Keep reasoning to one short sentence naming the deciding stroke feature. Respond as JSON only."""


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3134F
    )


def _squeeze(text: str) -> tuple[str, list[int]]:
    """Non-space characters plus each one's index in the original string."""

    chars: list[str] = []
    index_map: list[int] = []
    for index, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            index_map.append(index)
    return "".join(chars), index_map


def _stage_geometry(
    workspace: Path, staged_image: Path, checkpoint: Path
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Byte-identical agent evidence to toolbelt3, plus in-memory box geometry.

    The second return value never reaches the agent workspace: per-column
    character boxes in reading order for the adjudication pass.
    """

    image = cv2.imdecode(
        np.frombuffer(staged_image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        raise RuntimeError(f"cannot decode staged page image: {staged_image}")
    height, width = image.shape[:2]

    inference = _detect(staged_image, checkpoint)
    if inference.get("image_size") != [width, height]:
        raise RuntimeError("RF-DETR inference image size does not match the page")
    detections = _parse_detections(
        inference.get("boxes"), image_width=width, image_height=height
    )

    split = _two_split([float(d.width) for d in detections])
    threshold = None
    two_layer = False
    if split is not None:
        threshold, small_center, large_center = split
        small_fraction = sum(1 for d in detections if float(d.width) < threshold) / max(
            len(detections), 1
        )
        two_layer = (
            small_center / large_center <= _MAX_WIDTH_RATIO
            and _MIN_LAYER_FRACTION <= small_fraction <= 1 - _MIN_LAYER_FRACTION
        )

    def layer_of(detection) -> str:
        if two_layer and float(detection.width) < threshold:
            return "commentary"
        return "primary"

    evidence_root = workspace / "evidence"
    columns_root = evidence_root / "columns"
    columns_root.mkdir(parents=True, exist_ok=True)

    overlay = image.copy()
    for detection in detections:
        cell = detection.cell
        color = (0, 200, 0) if layer_of(detection) == "primary" else (0, 140, 255)
        cv2.rectangle(overlay, (cell.x0, cell.y0), (cell.x1, cell.y1), color, 2)
    scale = _OVERLAY_MAX_SIDE / max(overlay.shape[:2])
    if scale < 1.0:
        overlay = cv2.resize(
            overlay,
            (round(overlay.shape[1] * scale), round(overlay.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, payload = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("failed to encode detection overlay")
    (evidence_root / "overlay.jpg").write_bytes(payload.tobytes())

    column_records: list[dict[str, object]] = []
    adjudication_columns: list[dict[str, object]] = []
    second_reader_failures = 0
    second_reader_tokens: int | None = 0
    second_reader_cost_usd: float | None = 0.0
    global_index = 0
    registers = _split_registers(detections)
    for register_index, register_detections in enumerate(registers):
        for column_index, column in enumerate(_detected_columns(register_detections)):
            left = min(item.cell.x0 for item in column)
            top = min(item.cell.y0 for item in column)
            right = max(item.cell.x1 for item in column)
            bottom = max(item.cell.y1 for item in column)
            pad = max(8, round((right - left) * _CROP_PAD_FRACTION))
            crop = image[
                max(0, top - pad) : min(height, bottom + pad),
                max(0, left - pad) : min(width, right + pad),
            ]
            ok, crop_payload = cv2.imencode(".png", crop)
            if not ok:
                raise RuntimeError(
                    f"failed to encode column crop r{register_index}c{column_index:02d}"
                )
            crop_bytes = crop_payload.tobytes()
            crop_name = f"r{register_index}c{column_index:02d}.png"
            (columns_root / crop_name).write_bytes(crop_bytes)

            primary_boxes = sum(1 for item in column if layer_of(item) == "primary")
            second_reader = _read_second_opinion(crop_bytes, len(column))
            second_reader_tokens = combine_count(
                second_reader_tokens, second_reader.tokens
            )
            second_reader_cost_usd = combine_cost(
                second_reader_cost_usd, second_reader.cost_usd
            )
            second_reading = second_reader.reading
            if second_reading is None:
                second_reader_failures += 1
            layer = "primary" if primary_boxes * 2 >= len(column) else "commentary"
            column_records.append(
                {
                    "index": global_index,
                    "register": register_index,
                    "bbox": [left, top, right - left, bottom - top],
                    "boxes": len(column),
                    "primary_boxes": primary_boxes,
                    "commentary_boxes": len(column) - primary_boxes,
                    "layer": layer,
                    "crop": f"columns/{crop_name}",
                    "second_reader": second_reading,
                }
            )
            adjudication_columns.append(
                {
                    "layer": layer,
                    "second_reader": second_reading,
                    "boxes": [
                        [item.cell.x0, item.cell.y0, item.cell.x1, item.cell.y1]
                        for item in sorted(
                            column, key=lambda item: item.cell.y0 + item.cell.y1
                        )
                    ],
                }
            )
            global_index += 1

    primary_total = sum(int(record["primary_boxes"]) for record in column_records)
    commentary_total = sum(int(record["commentary_boxes"]) for record in column_records)
    geometry = {
        "schema_version": 3,
        "image_size": [width, height],
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
        "registers": len(registers),
        "columns_right_to_left": True,
        "second_reader": {
            "model": _SECOND_READER_MODEL,
            "thinking_level": _SECOND_READER_THINKING,
            "failures": second_reader_failures,
        },
        "columns": column_records,
    }
    geometry_bytes = (
        json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (evidence_root / "geometry.json").write_bytes(geometry_bytes)
    summary = {
        "detected_boxes": len(detections),
        "primary_boxes": primary_total,
        "commentary_boxes": commentary_total,
        "two_layer": two_layer,
        "registers": len(registers),
        "columns": len(column_records),
        "second_reader_failures": second_reader_failures,
        "second_reader_tokens": second_reader_tokens,
        "second_reader_cost_usd": second_reader_cost_usd,
        "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
        "inference_seconds": inference.get("inference_seconds"),
    }
    return summary, adjudication_columns


def _adjudication_crop(image, box: list[int]) -> bytes | None:
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    pad_x = round(_ADJUDICATION_PAD_FRACTION * (x1 - x0)) + 4
    pad_y = round(_ADJUDICATION_PAD_FRACTION * (y1 - y0)) + 4
    x0, x1 = max(0, x0 - pad_x), min(width, x1 + pad_x)
    y0, y1 = max(0, y0 - pad_y), min(height, y1 + pad_y)
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    long_side = max(crop.shape[:2])
    if long_side < _ADJUDICATION_MIN_SIDE:
        scale = _ADJUDICATION_MIN_SIDE / long_side
        crop = cv2.resize(
            crop,
            (
                max(1, round(crop.shape[1] * scale)),
                max(1, round(crop.shape[0] * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    ok, encoded = cv2.imencode(".png", crop)
    return encoded.tobytes() if ok else None


def _forced_choice(
    page_key: str, position: int, crop: bytes, draft_char: str, alternative: str
) -> tuple[str | None, float]:
    """Blind seeded A/B forced choice; returns (chosen char or None, cost)."""

    seed = int(
        hashlib.sha256(
            f"adjudicate#{page_key}#{position}#{draft_char}#{alternative}".encode(
                "utf-8"
            )
        ).hexdigest()[:8],
        16,
    )
    draft_is_a = seed % 2 == 0
    form_a = draft_char if draft_is_a else alternative
    form_b = alternative if draft_is_a else draft_char
    try:
        value, response = generate_json(
            ModelRequest(
                model=_ADJUDICATOR_MODEL,
                prompt=_ADJUDICATION_PROMPT.format(form_a=form_a, form_b=form_b),
                system=(
                    "You are a paleography assistant deciding which exact "
                    "glyph form is drawn in one character crop."
                ),
                images=(ImageContent(data=crop, mime="image/png"),),
                temperature=0.0,
                max_output_tokens=512,
                media_resolution="high",
                json_output=True,
                json_schema=_ADJUDICATION_SCHEMA,
                thinking_level="minimal",
            )
        )
    except GatewayError:
        return None, 0.0
    cost = float(response.cost_usd or 0.0)
    verdict = value.get("written_form")
    if verdict == "A":
        return form_a, cost
    if verdict == "B":
        return form_b, cost
    return None, cost


def _adjudicate_glyphs(
    image, columns: list[dict[str, object]], primary: str, page_key: str
) -> tuple[str, dict[str, object]]:
    """Deterministic post-pass: trigger, crop, forced-choice, patch."""

    stats: dict[str, object] = {
        "model": _ADJUDICATOR_MODEL,
        "max_calls": _ADJUDICATION_MAX_CALLS,
        "triggers_disagreement": 0,
        "triggers_watchlist": 0,
        "calls": 0,
        "patched": 0,
        "kept_draft": 0,
        "unresolved": 0,
        "boxes_exact": 0,
        "boxes_proportional": 0,
        "skipped": None,
        "cost_usd": 0.0,
    }
    readable = [
        c
        for c in columns
        if c["layer"] == "primary" and isinstance(c["second_reader"], str)
    ]
    if not primary.strip() or not readable:
        stats["skipped"] = "no primary draft or no readable primary columns"
        return primary, stats

    draft_ns, draft_map = _squeeze(primary)
    reader_chars: list[str] = []
    reader_boxes: list[tuple[list[int], bool]] = []
    for column in readable:
        text_ns, _ = _squeeze(str(column["second_reader"]))
        boxes = column["boxes"]
        exact = len(text_ns) == len(boxes)
        for position, ch in enumerate(text_ns):
            if exact:
                box = boxes[position]
            else:
                slot = round(position * (len(boxes) - 1) / max(len(text_ns) - 1, 1))
                box = boxes[slot]
            reader_chars.append(ch)
            reader_boxes.append((box, exact))
    reader_ns = "".join(reader_chars)
    if not reader_ns:
        stats["skipped"] = "second readings empty"
        return primary, stats

    disagreements: list[tuple[int, int, str]] = []
    agreements: list[tuple[int, int, str]] = []
    matcher = difflib.SequenceMatcher(None, draft_ns, reader_ns, autojunk=False)
    for tag, d0, d1, r0, r1 in matcher.get_opcodes():
        if tag == "replace":
            for k in range(min(d1 - d0, r1 - r0)):
                draft_char, reader_char = draft_ns[d0 + k], reader_ns[r0 + k]
                if (
                    draft_char != reader_char
                    and _is_cjk(draft_char)
                    and _is_cjk(reader_char)
                ):
                    disagreements.append((d0 + k, r0 + k, reader_char))
        elif tag == "equal":
            for k in range(d1 - d0):
                draft_char = draft_ns[d0 + k]
                alternative = _WATCHLIST.get(draft_char)
                if alternative:
                    agreements.append((d0 + k, r0 + k, alternative))
    stats["triggers_disagreement"] = len(disagreements)
    stats["triggers_watchlist"] = len(agreements)

    patched = list(primary)
    for draft_index, reader_index, alternative in (disagreements + agreements)[
        :_ADJUDICATION_MAX_CALLS
    ]:
        box, exact = reader_boxes[reader_index]
        stats["boxes_exact" if exact else "boxes_proportional"] += 1
        crop = _adjudication_crop(image, box)
        if crop is None:
            stats["unresolved"] += 1
            continue
        stats["calls"] += 1
        draft_char = draft_ns[draft_index]
        chosen, cost = _forced_choice(
            page_key, reader_index, crop, draft_char, alternative
        )
        stats["cost_usd"] += cost
        if chosen is None:
            stats["unresolved"] += 1
        elif chosen == draft_char:
            stats["kept_draft"] += 1
        else:
            patched[draft_map[draft_index]] = chosen
            stats["patched"] += 1
    stats["cost_usd"] = round(float(stats["cost_usd"]), 6)
    return "".join(patched), stats


class OmpToolbelt5Transcribe(Transcribe):
    """v3 layered reader plus deterministic glyph adjudication post-pass."""

    variant = "omp_toolbelt5"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/transcribe_omp_toolbelt2.py",
        "factory/stations/transcribe_omp_toolbelt3.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
    )

    def validate_options(self, options) -> None:
        _extension_source_bytes(options)

    def run(self, job: Job) -> StationResult:
        source_bytes = _extension_source_bytes(job.config.options)
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        workspace = agent_cell.stage_workspace(
            self._workspace_root(job) / page_key,
            skill=job.config.prompt.text,
            evidence={},
            images=[job.path_of("page_image")],
        )
        staged_images = sorted((workspace / "images").glob("*"))
        if len(staged_images) != 1:
            raise RuntimeError("toolbelt cell expects exactly one staged page image")
        geometry_summary, adjudication_columns = _stage_geometry(
            workspace, staged_images[0], _checkpoint_path(job)
        )

        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / "00-toolbelt3.ts").write_bytes(_TOOLBELT3_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        run = agent_cell.run(
            workspace,
            _TASK,
            model=job.config.model,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        primary, commentary = _read_layered_submission(workspace)
        image = cv2.imdecode(
            np.frombuffer(staged_images[0].read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise RuntimeError("cannot decode staged page image for adjudication")
        primary, adjudication = _adjudicate_glyphs(
            image, adjudication_columns, primary, page_key
        )
        page = job.page or {}
        tokens, cost_usd = _station_usage_with_second_reader(
            run.tokens,
            run.cost_usd,
            geometry_summary,
            extra_cost_usd=float(adjudication["cost_usd"]),
        )
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": page.get("order", 0),
                "canvas_id": page.get("canvas_id", ""),
                "text": primary,
                "commentary": commentary,
                "requested_model": job.config.model,
                "model": job.config.model,
                "finish_reason": "submit_transcription",
                "toolbelt": {**geometry_summary, "adjudication": adjudication},
            },
            tokens_in=tokens,
            cost_usd=cost_usd,
            process_stats=run.process_stats,
        )

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt5"
        )


register(OmpToolbelt5Transcribe())
