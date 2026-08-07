"""Development RF-DETR variant for forced character alignment."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from palimpsest.factory import glyphs
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations.align import Align
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import read_json, sha256_file

_RUNTIME_PYTHON_ENV = "PALIMPSEST_RFDETR_PYTHON"
_OBJECT_ROOT_ENV = "PALIMPSEST_RFDETR_OBJECT_ROOT"
_RUNTIME_TIMEOUT_SECONDS = 900
_WORKER_STARTUP_TIMEOUT_SECONDS = 300
_WORKER_IDLE_TIMEOUT_SECONDS = 120
_OPTION_KEYS = frozenset(
    {
        "checkpoint_sha256",
        "rfdetr_version",
        "torch_version",
        "torchvision_version",
        "tile_size",
        "overlap",
        "threshold",
        "nms_iou",
    }
)


@dataclass(frozen=True, slots=True)
class Detection:
    cell: glyphs.Cell
    score: float

    @property
    def center_x(self) -> float:
        return (self.cell.x0 + self.cell.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.cell.y0 + self.cell.y1) / 2

    @property
    def width(self) -> int:
        return self.cell.x1 - self.cell.x0


@dataclass(frozen=True, slots=True)
class RegionSpec:
    bbox: tuple[float, float, float, float]
    char_lines: tuple[tuple[str, ...], ...]

    @property
    def area(self) -> float:
        return self.bbox[2] * self.bbox[3]

    def contains(self, detection: Detection) -> bool:
        x, y, width, height = self.bbox
        return (
            x <= detection.center_x <= x + width
            and y <= detection.center_y <= y + height
        )


def _require_exact_options(options: Mapping[str, Any]) -> None:
    actual = set(options)
    if actual != _OPTION_KEYS:
        missing = sorted(_OPTION_KEYS - actual)
        unknown = sorted(actual - _OPTION_KEYS)
        raise ValueError(
            f"Expected options {sorted(_OPTION_KEYS)}; missing={missing}, unknown={unknown}"
        )


def _require_version(options: Mapping[str, Any], name: str) -> str:
    value = options[name]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _require_sha256(options: Mapping[str, Any]) -> str:
    value = _require_version(options, "checkpoint_sha256")
    if len(value) != 64:
        raise ValueError("checkpoint_sha256 must have 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError:
        raise ValueError(
            "checkpoint_sha256 must have 64 hexadecimal characters"
        ) from None
    return value.lower()


def _require_positive_integer(options: Mapping[str, Any], name: str) -> int:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_fraction(options: Mapping[str, Any], name: str) -> float:
    value = options[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")
    return result


def _parse_detections(
    records: object, *, image_width: int, image_height: int
) -> list[Detection]:
    if not isinstance(records, list):
        raise RuntimeError("RF-DETR inference returned a non-list boxes value")
    detections: list[Detection] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise RuntimeError(f"RF-DETR box {index} is not an object")
        if set(record) != {"bbox", "score"}:
            raise RuntimeError(f"RF-DETR box {index} has invalid fields")
        bbox = record["bbox"]
        score = record["score"]
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(
                isinstance(value, bool) or not isinstance(value, int | float)
                for value in bbox
            )
        ):
            raise RuntimeError(f"RF-DETR box {index} has an invalid bbox")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise RuntimeError(f"RF-DETR box {index} has an invalid score")
        x, y, width, height = (float(value) for value in bbox)
        confidence = float(score)
        if (
            not all(math.isfinite(value) for value in (x, y, width, height, confidence))
            or width <= 0
            or height <= 0
            or not 0.0 <= confidence <= 1.0
        ):
            raise RuntimeError(f"RF-DETR box {index} has invalid geometry or score")
        x0 = math.floor(x)
        y0 = math.floor(y)
        x1 = math.ceil(x + width)
        y1 = math.ceil(y + height)
        if not (0 <= x0 < x1 <= image_width and 0 <= y0 < y1 <= image_height):
            raise RuntimeError(f"RF-DETR box {index} is outside the source image")
        detections.append(Detection(glyphs.Cell(x0, y0, x1, y1), confidence))
    return detections


def _detected_columns(detections: Sequence[Detection]) -> list[list[Detection]]:
    """Cluster character detections into right-to-left vertical columns."""
    if not detections:
        return []
    typical_width = float(median(detection.width for detection in detections))
    columns: list[list[Detection]] = []
    for detection in sorted(detections, key=lambda item: -item.center_x):
        candidates: list[tuple[float, int]] = []
        for index, column in enumerate(columns):
            column_center = median(item.center_x for item in column)
            distance = abs(column_center - detection.center_x)
            if distance <= typical_width * 0.45:
                candidates.append((distance, index))
        if candidates:
            _, index = min(candidates)
            columns[index].append(detection)
        else:
            columns.append([detection])
    columns.sort(key=lambda column: -median(item.center_x for item in column))
    for column in columns:
        column.sort(key=lambda item: (item.cell.y0, item.cell.x0))
    return columns


def _pair_columns(
    columns: Sequence[Sequence[Detection]], char_lines: Sequence[Sequence[str]]
) -> tuple[list[Sequence[Detection] | None], int]:
    """Order-preserving count alignment that can skip spurious image columns."""
    image_count = len(columns)
    text_count = len(char_lines)
    infinity = float("inf")
    costs = [[infinity for _ in range(text_count + 1)] for _ in range(image_count + 1)]
    previous: dict[tuple[int, int], tuple[int, int, str]] = {}
    costs[0][0] = 0.0

    def advance(
        image_index: int,
        text_index: int,
        next_image: int,
        next_text: int,
        candidate: float,
        operation: str,
    ) -> None:
        if candidate < costs[next_image][next_text]:
            costs[next_image][next_text] = candidate
            previous[(next_image, next_text)] = (
                image_index,
                text_index,
                operation,
            )

    for image_index in range(image_count + 1):
        for text_index in range(text_count + 1):
            current = costs[image_index][text_index]
            if current == infinity:
                continue
            if image_index < image_count:
                advance(
                    image_index,
                    text_index,
                    image_index + 1,
                    text_index,
                    current + 0.75,
                    "skip_image",
                )
            if text_index < text_count:
                advance(
                    image_index,
                    text_index,
                    image_index,
                    text_index + 1,
                    current + 1.0,
                    "skip_text",
                )
            if image_index < image_count and text_index < text_count:
                image_length = len(columns[image_index])
                text_length = len(char_lines[text_index])
                denominator = max(image_length, text_length, 1)
                mismatch = abs(image_length - text_length) / denominator
                advance(
                    image_index,
                    text_index,
                    image_index + 1,
                    text_index + 1,
                    current + mismatch,
                    "match",
                )

    paired: list[Sequence[Detection] | None] = [None] * text_count
    skipped_image_columns = 0
    image_index, text_index = image_count, text_count
    while image_index or text_index:
        prior_image, prior_text, operation = previous[(image_index, text_index)]
        if operation == "match":
            paired[prior_text] = columns[prior_image]
        elif operation == "skip_image":
            skipped_image_columns += 1
        image_index, text_index = prior_image, prior_text
    return paired, skipped_image_columns


def _text_char_lines(text: str) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(glyphs._ink_chars(line)) for line in text.splitlines() if line.strip()
    )


def _parse_regions(
    regions: object,
    *,
    image_width: int,
    image_height: int,
    expected_char_lines: tuple[tuple[str, ...], ...],
) -> tuple[RegionSpec, ...]:
    if regions in (None, []):
        return ()
    if not isinstance(regions, list):
        raise RuntimeError("page transcription regions must be a list")

    result: list[RegionSpec] = []
    for index, raw_region in enumerate(regions):
        if not isinstance(raw_region, Mapping):
            raise RuntimeError(f"page transcription region {index} must be an object")
        raw_bbox = raw_region.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            raise RuntimeError(f"page transcription region {index} has invalid bbox")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            for value in raw_bbox
        ):
            raise RuntimeError(f"page transcription region {index} has invalid bbox")
        x, y, width, height = (float(value) for value in raw_bbox)
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > image_width
            or y + height > image_height
        ):
            raise RuntimeError(
                f"page transcription region {index} bbox is outside the source image"
            )
        region_text = raw_region.get("text")
        if not isinstance(region_text, str):
            raise RuntimeError(
                f"page transcription region {index} text must be a string"
            )
        char_lines = _text_char_lines(region_text)
        if char_lines:
            result.append(RegionSpec((x, y, width, height), char_lines))

    observed_char_lines = tuple(line for region in result for line in region.char_lines)
    if observed_char_lines != expected_char_lines:
        raise RuntimeError("page transcription regions do not reproduce the page text")
    return tuple(result)


def _align_char_lines(
    char_lines: Sequence[Sequence[str]],
    detections: Sequence[Detection],
) -> tuple[list[dict[str, object]], int, int]:
    columns = _detected_columns(detections)
    paired, skipped_image_columns = _pair_columns(columns, char_lines)
    glyph_height = (
        float(median(detection.cell.h for detection in detections))
        if detections
        else 1.0
    )

    out_columns: list[dict[str, object]] = []
    for chars, column in zip(char_lines, paired, strict=True):
        if column is None:
            aligned = [(character, None, 0.0, "none") for character in chars]
        else:
            aligned = glyphs.align_column(
                [detection.cell for detection in column],
                list(chars),
                glyph_height,
            )
            aligned = [
                (
                    character,
                    bbox,
                    confidence,
                    (
                        "rfdetr-merged"
                        if method == "merged"
                        else "rfdetr"
                        if method == "blob"
                        else method
                    ),
                )
                for character, bbox, confidence, method in aligned
            ]
        out_columns.append(glyphs._column_payload(aligned))
    return out_columns, len(columns), skipped_image_columns


def align_detections(
    text: str,
    box_records: object,
    *,
    image_width: int,
    image_height: int,
    regions: object = None,
) -> dict[str, object]:
    """Bind a transcription to validated RF-DETR character detections."""
    char_lines = _text_char_lines(text)
    detections = _parse_detections(
        box_records,
        image_width=image_width,
        image_height=image_height,
    )
    region_specs = _parse_regions(
        regions,
        image_width=image_width,
        image_height=image_height,
        expected_char_lines=char_lines,
    )

    unassigned_detections = 0
    if region_specs:
        region_detections: list[list[Detection]] = [[] for _ in region_specs]
        for detection in detections:
            matches = [
                index
                for index, region in enumerate(region_specs)
                if region.contains(detection)
            ]
            if not matches:
                unassigned_detections += 1
                continue
            selected = min(matches, key=lambda index: region_specs[index].area)
            region_detections[selected].append(detection)

        out_columns: list[dict[str, object]] = []
        image_column_count = 0
        skipped_image_columns = 0
        for region, selected in zip(region_specs, region_detections, strict=True):
            region_columns, detected_count, skipped_count = _align_char_lines(
                region.char_lines,
                selected,
            )
            out_columns.extend(region_columns)
            image_column_count += detected_count
            skipped_image_columns += skipped_count
    else:
        out_columns, image_column_count, skipped_image_columns = _align_char_lines(
            char_lines,
            detections,
        )

    stats = glyphs._stats(
        char_lines,
        out_columns,
        0,
        image_columns=image_column_count,
    )
    stats.update(
        {
            "detected_boxes": len(detections),
            "skipped_detection_columns": skipped_image_columns,
            "region_count": len(region_specs),
            "unassigned_detections": unassigned_detections,
        }
    )
    return {"columns": out_columns, "stats": stats}


def _request_worker(state_path: Path, source: Path) -> dict[str, object]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        not isinstance(state, dict)
        or set(state) != {"pid", "port", "token"}
        or isinstance(state["port"], bool)
        or not isinstance(state["port"], int)
        or not 0 < state["port"] <= 65535
        or not isinstance(state["token"], str)
    ):
        raise RuntimeError("RF-DETR worker state is invalid")
    request = (
        json.dumps(
            {"token": state["token"], "source": str(source.resolve())},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    with socket.create_connection(
        ("127.0.0.1", state["port"]),
        timeout=_RUNTIME_TIMEOUT_SECONDS,
    ) as connection:
        connection.settimeout(_RUNTIME_TIMEOUT_SECONDS)
        connection.sendall(request)
        with connection.makefile("rb") as stream:
            line = stream.readline(64 * 1024 * 1024)
    if not line.endswith(b"\n"):
        raise RuntimeError("RF-DETR worker returned an incomplete response")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("RF-DETR worker returned a non-object response")
    error = response.get("error")
    if isinstance(error, str):
        raise RuntimeError(f"rfdetr-mth600/v1 inference failed: {error}")
    return response


class RfDetrAlign(Align):
    """Use a frozen RF-DETR checkpoint as the aligner's character geometry."""

    variant = "rfdetr-mth600/v1"
    option_keys = _OPTION_KEYS
    production_dependencies = (
        *Align.production_dependencies,
        "factory/stations/align.py",
        "factory/stations/align_rfdetr_runtime.py",
    )
    _runtime_script = Path(__file__).with_name("align_rfdetr_runtime.py")

    def validate_options(self, options: Mapping[str, Any]) -> None:
        _require_exact_options(options)
        _require_sha256(options)
        for name in ("rfdetr_version", "torch_version", "torchvision_version"):
            _require_version(options, name)
        tile_size = _require_positive_integer(options, "tile_size")
        overlap = options["overlap"]
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
            raise ValueError("overlap must be a non-negative integer")
        if overlap >= tile_size:
            raise ValueError("overlap must be smaller than tile_size")
        _require_fraction(options, "threshold")
        _require_fraction(options, "nms_iou")

    def _runtime_python(self) -> Path:
        configured = os.getenv(_RUNTIME_PYTHON_ENV)
        return Path(configured) if configured else Path(sys.executable)

    def _checkpoint_path(self, job: Job, digest: str) -> Path:
        configured = os.getenv(_OBJECT_ROOT_ENV)
        root = (
            Path(configured)
            if configured
            else job.library_root / "evaluations" / "objects"
        )
        return root / digest

    def _worker_paths(
        self,
        checkpoint: Path,
        options: Mapping[str, Any],
    ) -> tuple[Path, Path, Path]:
        identity = hashlib.sha256(
            json.dumps(
                dict(options),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        worker_root = checkpoint.parent.parent / "rfdetr-workers"
        worker_root.mkdir(parents=True, exist_ok=True)
        return (
            worker_root / f"{identity}.json",
            worker_root / f"{identity}.launch",
            worker_root / f"{identity}.log",
        )

    def _worker_command(
        self,
        *,
        runtime_python: Path,
        checkpoint: Path,
        options: Mapping[str, Any],
        state_path: Path,
        token: str,
    ) -> list[str]:
        return [
            str(runtime_python),
            str(self._runtime_script),
            ".",
            str(checkpoint),
            str(options["checkpoint_sha256"]),
            str(options["rfdetr_version"]),
            str(options["torch_version"]),
            str(options["torchvision_version"]),
            "--tile-size",
            str(options["tile_size"]),
            "--overlap",
            str(options["overlap"]),
            "--threshold",
            str(options["threshold"]),
            "--nms-iou",
            str(options["nms_iou"]),
            "--serve",
            "--state-path",
            str(state_path),
            "--token",
            token,
            "--idle-timeout",
            str(_WORKER_IDLE_TIMEOUT_SECONDS),
        ]

    def _start_worker(
        self,
        *,
        runtime_python: Path,
        checkpoint: Path,
        options: Mapping[str, Any],
        state_path: Path,
        log_path: Path,
    ) -> subprocess.Popen[bytes]:
        state_path.unlink(missing_ok=True)
        token = secrets.token_hex(32)
        command = self._worker_command(
            runtime_python=runtime_python,
            checkpoint=checkpoint,
            options=options,
            state_path=state_path,
            token=token,
        )
        popen_options: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            popen_options["start_new_session"] = True
        with log_path.open("ab") as log:
            return subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                **popen_options,
            )

    def _predict(self, job: Job) -> dict[str, object]:
        options = job.config.options
        self.validate_options(options)
        runtime_python = self._runtime_python()
        if not runtime_python.is_file():
            raise FileNotFoundError(
                f"Missing isolated RF-DETR runtime: {runtime_python}"
            )
        checkpoint_sha256 = str(options["checkpoint_sha256"])
        checkpoint = self._checkpoint_path(job, checkpoint_sha256)
        if not checkpoint.is_file():
            raise FileNotFoundError(f"Missing RF-DETR checkpoint: {checkpoint}")
        # Fail closed on content drift before any worker spawn, mirroring
        # instrumented_sensors.load_object: the digest in the pinned options
        # names the exact bytes, not the path. The worker re-verifies the
        # same digest from its argv at load time.
        actual_sha256 = sha256_file(checkpoint)
        if actual_sha256 != checkpoint_sha256:
            raise ValueError(
                f"Checkpoint hash mismatch for {checkpoint}: expected "
                f"{checkpoint_sha256}, got {actual_sha256}"
            )
        source = job.path_of("page_image_clean")
        state_path, launch_path, log_path = self._worker_paths(checkpoint, options)

        try:
            return _request_worker(state_path, source)
        except (
            ConnectionError,
            OSError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ):
            pass

        deadline = time.monotonic() + _WORKER_STARTUP_TIMEOUT_SECONDS
        launcher = False
        process: subprocess.Popen[bytes] | None = None
        while not launcher:
            try:
                descriptor = os.open(
                    launch_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                try:
                    return _request_worker(state_path, source)
                except (
                    ConnectionError,
                    OSError,
                    ValueError,
                    json.JSONDecodeError,
                    RuntimeError,
                ):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for the RF-DETR worker launcher"
                        ) from None
                    try:
                        stale = (
                            time.time() - launch_path.stat().st_mtime
                            > _WORKER_STARTUP_TIMEOUT_SECONDS
                        )
                    except FileNotFoundError:
                        stale = False
                    if stale:
                        launch_path.unlink(missing_ok=True)
                    time.sleep(0.2)
            else:
                os.close(descriptor)
                launcher = True

        try:
            process = self._start_worker(
                runtime_python=runtime_python,
                checkpoint=checkpoint,
                options=options,
                state_path=state_path,
                log_path=log_path,
            )
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    detail = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                    raise RuntimeError(
                        f"{self.variant} worker failed to start: "
                        f"{detail or f'exit code {process.returncode}'}"
                    )
                try:
                    return _request_worker(state_path, source)
                except (
                    ConnectionError,
                    FileNotFoundError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    time.sleep(0.2)
            raise TimeoutError("Timed out starting the RF-DETR worker")
        finally:
            launch_path.unlink(missing_ok=True)

    def run(self, job: Job) -> StationResult:
        transcription = read_json(job.path_of("page_transcription"))
        text = transcription["text"]
        if not isinstance(text, str):
            raise RuntimeError("page transcription text must be a string")
        image = load_image(job, "page_image_clean")
        height, width = image.shape[:2]
        if text.strip():
            inference = self._predict(job)
            if set(inference) != {
                "boxes",
                "image_size",
                "model_load_seconds",
                "inference_seconds",
                "peak_vram_bytes",
            }:
                raise RuntimeError(f"{self.variant} inference returned invalid fields")
            if inference["image_size"] != [width, height]:
                raise RuntimeError(
                    f"{self.variant} inference image size does not match its input"
                )
            boxes = inference["boxes"]
        else:
            boxes = []
        result = align_detections(
            text,
            boxes,
            image_width=width,
            image_height=height,
            regions=transcription.get("regions"),
        )
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                **result,
            },
            cost_usd=0.0,
        )


register(RfDetrAlign())
