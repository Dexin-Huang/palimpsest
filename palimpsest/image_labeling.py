"""Manifest-driven local image annotation with append-only human decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import threading
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

PROJECT_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
DATASET_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 256_000
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CROP_MODES = {"none", "optional", "required"}
DECISIONS = {"accept", "skip"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def portable_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def resolve_recorded_path(value: str, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _safe_id(value: object, field: str, pattern: re.Pattern[str]) -> str:
    identifier = _required_text(value, field)
    if pattern.fullmatch(identifier) is None:
        raise ValueError(f"{field} is not filesystem and URL safe: {identifier!r}")
    return identifier


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


class ImageAnnotationStore:
    """Validate one project and persist its human annotation event stream."""

    def __init__(
        self,
        project_path: Path,
        event_path: Path,
        dataset_path: Path,
        accepted_image_dir: Path,
    ) -> None:
        self.project_path = project_path.resolve()
        self.event_path = event_path.resolve()
        self.dataset_path = dataset_path.resolve()
        self.accepted_image_dir = accepted_image_dir.resolve()
        self.project = json.loads(self.project_path.read_text(encoding="utf-8"))
        self.project_sha256 = sha256(self.project_path)
        self.lock = threading.RLock()
        self._validate_project()
        self.events, self.latest = self._load_events()
        self._materialize_dataset()

    def _validate_project(self) -> None:
        project = self.project
        if not isinstance(project, dict):
            raise ValueError("Project manifest must be an object")
        if project.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise ValueError(f"Project schema_version must be {PROJECT_SCHEMA_VERSION}")
        self.project_id = _safe_id(project.get("id"), "project.id", PROJECT_ID)
        _required_text(project.get("title"), "project.title")
        _required_text(project.get("instructions"), "project.instructions")

        asset_root_value = project.get("asset_root", ".")
        if not isinstance(asset_root_value, str):
            raise ValueError("project.asset_root must be a path string")
        self.asset_root = resolve_recorded_path(
            asset_root_value, self.project_path.parent
        )

        label = project.get("label")
        if not isinstance(label, dict):
            raise ValueError("project.label must be an object")
        _required_text(label.get("name"), "project.label.name")
        self.label_required = label.get("required", True)
        if not isinstance(self.label_required, bool):
            raise ValueError("project.label.required must be boolean")
        self.label_max_length = _positive_int(
            label.get("max_length", 256), "project.label.max_length"
        )
        pattern_value = label.get("pattern")
        if pattern_value is not None and not isinstance(pattern_value, str):
            raise ValueError("project.label.pattern must be a regex string")
        self.label_pattern = re.compile(pattern_value) if pattern_value else None

        crop_mode = project.get("crop_mode", "none")
        if not isinstance(crop_mode, str) or crop_mode not in CROP_MODES:
            raise ValueError(f"project.crop_mode must be one of {sorted(CROP_MODES)}")
        self.default_crop_mode = crop_mode

        queues = project.get("queues")
        if not isinstance(queues, list) or not queues:
            raise ValueError("project.queues must contain at least one queue")
        self.queues: dict[str, dict] = {}
        for queue in queues:
            if not isinstance(queue, dict):
                raise ValueError("Every queue must be an object")
            queue_id = _safe_id(queue.get("id"), "queue.id", ITEM_ID)
            if queue_id in self.queues:
                raise ValueError(f"Duplicate queue id: {queue_id}")
            _required_text(queue.get("label"), f"queue {queue_id}.label")
            minimum = queue.get("minimum_distinct_labels", 0)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
                raise ValueError(
                    f"queue {queue_id}.minimum_distinct_labels must be non-negative"
                )
            self.queues[queue_id] = queue

        reasons = project.get(
            "skip_reasons",
            [
                {"id": "unclear", "label": "Label is unclear"},
                {"id": "unusable", "label": "Image is unusable"},
            ],
        )
        if not isinstance(reasons, list) or not reasons:
            raise ValueError("project.skip_reasons must not be empty")
        self.skip_reasons: dict[str, dict] = {}
        for reason in reasons:
            if not isinstance(reason, dict):
                raise ValueError("Every skip reason must be an object")
            reason_id = _safe_id(reason.get("id"), "skip_reason.id", ITEM_ID)
            if reason_id in self.skip_reasons:
                raise ValueError(f"Duplicate skip reason: {reason_id}")
            _required_text(reason.get("label"), f"skip reason {reason_id}.label")
            self.skip_reasons[reason_id] = reason

        items = project.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("project.items must contain at least one item")
        self.items: list[dict] = []
        self.by_id: dict[str, dict] = {}
        image_cache: dict[Path, tuple[int, int, str]] = {}
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"project.items[{position}] must be an object")
            item_id = _safe_id(item.get("id"), f"items[{position}].id", ITEM_ID)
            if item_id in self.by_id:
                raise ValueError(f"Duplicate item id: {item_id}")
            queue_id = _safe_id(item.get("queue"), f"item {item_id}.queue", ITEM_ID)
            if queue_id not in self.queues:
                raise ValueError(f"Item {item_id} uses unknown queue {queue_id!r}")
            image_value = _required_text(
                item.get("image_path"), f"item {item_id}.image_path"
            )
            image_path = resolve_recorded_path(image_value, self.asset_root)
            if not image_path.is_file():
                raise FileNotFoundError(image_path)
            expected_hash = _required_text(
                item.get("image_sha256"), f"item {item_id}.image_sha256"
            )
            if image_path not in image_cache:
                actual_hash = sha256(image_path)
                image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
                if image is None:
                    raise ValueError(f"Cannot decode image: {image_path}")
                image_cache[image_path] = (
                    int(image.shape[1]),
                    int(image.shape[0]),
                    actual_hash,
                )
            width, height, actual_hash = image_cache[image_path]
            if expected_hash != actual_hash:
                raise ValueError(f"Image hash mismatch for {item_id}: {image_path}")
            declared_width = item.get("image_width", width)
            declared_height = item.get("image_height", height)
            if declared_width != width or declared_height != height:
                raise ValueError(f"Image dimensions mismatch for {item_id}")

            item_crop_mode = item.get("crop_mode", self.default_crop_mode)
            if not isinstance(item_crop_mode, str) or item_crop_mode not in CROP_MODES:
                raise ValueError(
                    f"Item {item_id} crop_mode must be one of {sorted(CROP_MODES)}"
                )
            initial_bbox = item.get("initial_bbox")
            if item_crop_mode == "required" and initial_bbox is None:
                raise ValueError(f"Item {item_id} requires initial_bbox")
            if initial_bbox is not None:
                self._validate_bbox_values(
                    initial_bbox, width, height, f"item {item_id}.initial_bbox"
                )

            first_pass = item.get("first_pass")
            if first_pass is not None:
                if not isinstance(first_pass, dict):
                    raise ValueError(f"Item {item_id}.first_pass must be an object")
                if "label" in first_pass and not isinstance(first_pass["label"], str):
                    raise ValueError(f"Item {item_id}.first_pass.label must be text")

            normalized = {
                **item,
                "id": item_id,
                "queue": queue_id,
                "image_path": image_value,
                "image_width": width,
                "image_height": height,
                "crop_mode": item_crop_mode,
            }
            self.items.append(normalized)
            self.by_id[item_id] = normalized

    @staticmethod
    def _validate_bbox_values(
        value: object, width: int, height: int, field: str
    ) -> list[int]:
        if not isinstance(value, list) or len(value) != 4:
            raise ValueError(f"{field} must contain x, y, width, height")
        if any(isinstance(part, bool) or not isinstance(part, int) for part in value):
            raise ValueError(f"{field} values must be integers")
        x, y, box_width, box_height = value
        if box_width <= 0 or box_height <= 0:
            raise ValueError(f"{field} dimensions must be positive")
        if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
            raise ValueError(f"{field} must remain inside the source image")
        return [x, y, box_width, box_height]

    def _load_events(self) -> tuple[list[dict], dict[str, dict]]:
        if not self.event_path.exists():
            return [], {}
        events: list[dict] = []
        latest: dict[str, dict] = {}
        for line_number, line in enumerate(
            self.event_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"Event line {line_number} must be an object")
            if event.get("schema_version") != EVENT_SCHEMA_VERSION:
                raise ValueError(f"Event line {line_number} has the wrong schema")
            if event.get("event_type") != "image_annotation_decision":
                raise ValueError(f"Event line {line_number} has the wrong event type")
            if event.get("project_id") != self.project_id:
                raise ValueError(f"Event line {line_number} belongs to another project")
            if event.get("project_sha256") != self.project_sha256:
                raise ValueError(
                    f"Event line {line_number} targets a different project revision"
                )
            item_id = event.get("item_id")
            if not isinstance(item_id, str) or item_id not in self.by_id:
                raise ValueError(f"Event line {line_number} targets an unknown item")
            item = self.by_id[item_id]
            if event.get("queue") != item["queue"]:
                raise ValueError(f"Event line {line_number} has the wrong queue")
            decision = event.get("decision")
            if not isinstance(decision, str) or decision not in DECISIONS:
                raise ValueError(f"Event line {line_number} has an invalid decision")

            expected_sequence = len(events) + 1
            if event.get("sequence") != expected_sequence:
                raise ValueError(
                    f"Event line {line_number} must have sequence {expected_sequence}"
                )
            previous = latest.get(item_id)
            expected_revision = int(previous["revision"]) + 1 if previous else 1
            if event.get("revision") != expected_revision:
                raise ValueError(
                    f"Event line {line_number} must have revision {expected_revision}"
                )
            expected_supersedes = previous["sequence"] if previous else None
            if event.get("supersedes_sequence") != expected_supersedes:
                raise ValueError(
                    f"Event line {line_number} has an invalid supersedes_sequence"
                )
            events.append(event)
            latest[item_id] = event
        return events, latest

    def _source_path(self, item: dict) -> Path:
        return resolve_recorded_path(item["image_path"], self.asset_root)

    def _verified_source_path(self, item: dict) -> Path:
        path = self._source_path(item)
        if not path.is_file():
            raise FileNotFoundError(path)
        if sha256(path) != item["image_sha256"]:
            raise ValueError(f"Image hash mismatch for {item['id']}: {path}")
        return path

    def _validate_label(self, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("label must be text")
        label = value.strip()
        if self.label_required and not label:
            raise ValueError("A label is required before accepting")
        if len(label) > self.label_max_length:
            raise ValueError(
                f"Label must be at most {self.label_max_length} characters"
            )
        if (
            self.label_pattern is not None
            and self.label_pattern.fullmatch(label) is None
        ):
            raise ValueError("Label does not match the project label pattern")
        return label

    def _validate_bbox(self, item: dict, value: object) -> list[int] | None:
        crop_mode = item["crop_mode"]
        if crop_mode == "none":
            if value is not None:
                raise ValueError("This item does not accept a crop box")
            return None
        if value is None:
            if crop_mode == "required":
                raise ValueError("A crop box is required")
            return None
        return self._validate_bbox_values(
            value,
            int(item["image_width"]),
            int(item["image_height"]),
            "bbox",
        )

    def _accepted_image(
        self,
        item: dict,
        source_path: Path,
        bbox: list[int] | None,
        revision: int,
    ) -> tuple[str, str, str]:
        if bbox is None:
            return (
                portable_path(source_path, self.dataset_path.parent),
                item["image_sha256"],
                "source",
            )
        image = cv2.imread(str(source_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise FileNotFoundError(source_path)
        x, y, width, height = bbox
        crop = image[y : y + height, x : x + width]
        if crop.size == 0:
            raise ValueError("Crop box produced an empty image")
        self.accepted_image_dir.mkdir(parents=True, exist_ok=True)
        path = self.accepted_image_dir / f"{item['id']}-r{revision:03d}.png"
        temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
        if not cv2.imwrite(str(temporary), crop):
            raise RuntimeError(f"Could not write accepted image: {path}")
        os.replace(temporary, path)
        return (
            portable_path(path, self.dataset_path.parent),
            sha256(path),
            "crop",
        )

    def apply(self, payload: dict) -> dict:
        with self.lock:
            if not isinstance(payload, dict):
                raise ValueError("Decision payload must be an object")
            item_id = payload.get("item_id")
            if not isinstance(item_id, str) or item_id not in self.by_id:
                raise ValueError(f"Unknown item: {item_id!r}")
            item = self.by_id[item_id]
            decision = payload.get("decision")
            if not isinstance(decision, str) or decision not in DECISIONS:
                raise ValueError(f"decision must be one of {sorted(DECISIONS)}")

            previous = self.latest.get(item_id)
            revision = int(previous["revision"]) + 1 if previous else 1
            first_pass_label = (item.get("first_pass") or {}).get("label")
            label: str | None = None
            bbox: list[int] | None = None
            accepted_path: str | None = None
            accepted_sha256: str | None = None
            accepted_kind: str | None = None
            skip_reason: str | None = None
            if decision == "accept":
                label = self._validate_label(payload.get("label"))
                bbox = self._validate_bbox(item, payload.get("bbox"))
            else:
                reason_value = payload.get("skip_reason")
                if (
                    not isinstance(reason_value, str)
                    or reason_value not in self.skip_reasons
                ):
                    raise ValueError(
                        f"skip_reason must be one of {sorted(self.skip_reasons)}"
                    )

                skip_reason = reason_value
            source_path = self._verified_source_path(item)
            if decision == "accept":
                accepted_path, accepted_sha256, accepted_kind = self._accepted_image(
                    item, source_path, bbox, revision
                )

            initial_bbox = item.get("initial_bbox")
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_type": "image_annotation_decision",
                "project_id": self.project_id,
                "project_sha256": self.project_sha256,
                "sequence": len(self.events) + 1,
                "revision": revision,
                "supersedes_sequence": previous["sequence"] if previous else None,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "item_id": item_id,
                "queue": item["queue"],
                "decision": decision,
                "skip_reason": skip_reason,
                "label": label,
                "first_pass_label": first_pass_label,
                "label_was_overridden": (
                    decision == "accept" and label != first_pass_label
                ),
                "bbox": bbox,
                "bbox_was_adjusted": (decision == "accept" and bbox != initial_bbox),
                "accepted_image_kind": accepted_kind,
                "accepted_image_path": accepted_path,
                "accepted_image_sha256": accepted_sha256,
                "source_image_path": portable_path(
                    source_path, self.dataset_path.parent
                ),
                "source_image_sha256": item["image_sha256"],
            }
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            with self.event_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.events.append(event)
            self.latest[item_id] = event
            self._materialize_dataset()
            return event

    def _queue_summary(self, queue_id: str) -> dict:
        items = [item for item in self.items if item["queue"] == queue_id]
        events = [
            self.latest[item["id"]] for item in items if item["id"] in self.latest
        ]
        accepted = [event for event in events if event["decision"] == "accept"]
        distinct_labels = {event["label"] for event in accepted}
        required = int(self.queues[queue_id].get("minimum_distinct_labels", 0))
        return {
            "total": len(items),
            "reviewed": len(events),
            "accepted": len(accepted),
            "skipped": len(events) - len(accepted),
            "remaining": len(items) - len(events),
            "distinct_labels": len(distinct_labels),
            "minimum_distinct_labels": required,
            "ready": len(distinct_labels) >= required,
        }

    def _materialize_dataset(self) -> None:
        accepted_records: list[dict] = []
        for item in self.items:
            event = self.latest.get(item["id"])
            if event is None or event["decision"] != "accept":
                continue
            accepted_records.append(
                {
                    "item_id": item["id"],
                    "queue": item["queue"],
                    "label": event["label"],
                    "first_pass": item.get("first_pass"),
                    "label_was_overridden": event["label_was_overridden"],
                    "bbox": event["bbox"],
                    "bbox_was_adjusted": event["bbox_was_adjusted"],
                    "accepted_image_kind": event["accepted_image_kind"],
                    "accepted_image_path": event["accepted_image_path"],
                    "accepted_image_sha256": event["accepted_image_sha256"],
                    "source_image_path": event["source_image_path"],
                    "source_image_sha256": event["source_image_sha256"],
                    "metadata": item.get("metadata", {}),
                    "revision": event["revision"],
                    "event_sequence": event["sequence"],
                }
            )
        queue_summaries = {
            queue_id: self._queue_summary(queue_id) for queue_id in self.queues
        }
        dataset_ready = all(summary["ready"] for summary in queue_summaries.values())
        dataset = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "kind": "human_image_annotation_dataset",
            "project_id": self.project_id,
            "project_title": self.project["title"],
            "status": "human_attested_gold"
            if dataset_ready
            else "annotation_in_progress",
            "project_path": portable_path(self.project_path, self.dataset_path.parent),
            "project_sha256": self.project_sha256,
            "event_path": portable_path(self.event_path, self.dataset_path.parent),
            "event_count": len(self.events),
            "queue_summaries": queue_summaries,
            "dataset_ready": dataset_ready,
            "records": accepted_records,
            "metadata": self.project.get("metadata", {}),
        }
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.dataset_path.with_suffix(self.dataset_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.dataset_path)

    def _client_item(self, item: dict) -> dict:
        event = self.latest.get(item["id"])
        return {
            "id": item["id"],
            "queue": item["queue"],
            "image_width": item["image_width"],
            "image_height": item["image_height"],
            "crop_mode": item["crop_mode"],
            "initial_bbox": item.get("initial_bbox"),
            "first_pass": item.get("first_pass"),
            "metadata": item.get("metadata", {}),
            "latest": event,
            "image_url": f"/api/image?id={item['id']}&v={self.project_sha256[:12]}",
        }

    def client_config(self) -> dict:
        return {
            "project": {
                "id": self.project_id,
                "title": self.project["title"],
                "instructions": self.project["instructions"],
                "label": self.project["label"],
                "crop_mode": self.default_crop_mode,
            },
            "queues": list(self.queues.values()),
            "skip_reasons": list(self.skip_reasons.values()),
        }

    def client_state(self, queue_id: str, item_id: str | None = None) -> dict:
        with self.lock:
            if queue_id not in self.queues:
                raise ValueError(f"Unknown queue: {queue_id}")
            queue_items = [item for item in self.items if item["queue"] == queue_id]
            if item_id is not None:
                item = self.by_id.get(item_id)
                if item is None or item["queue"] != queue_id:
                    raise ValueError(f"Unknown item for queue: {item_id}")
            else:
                item = next(
                    (
                        candidate
                        for candidate in queue_items
                        if candidate["id"] not in self.latest
                    ),
                    None,
                )
            selected_index = queue_items.index(item) if item is not None else -1
            navigation = []
            for candidate in queue_items:
                event = self.latest.get(candidate["id"])
                navigation.append(
                    {
                        "id": candidate["id"],
                        "decision": event["decision"] if event is not None else None,
                        "label": (
                            event.get("label")
                            if event is not None
                            else (candidate.get("first_pass") or {}).get("label")
                        ),
                    }
                )
            return {
                "queue": queue_id,
                "progress": self._queue_summary(queue_id),
                "item": self._client_item(item) if item is not None else None,
                "previous_id": (
                    queue_items[selected_index - 1]["id"]
                    if selected_index > 0
                    else None
                ),
                "next_id": (
                    queue_items[selected_index + 1]["id"]
                    if 0 <= selected_index < len(queue_items) - 1
                    else None
                ),
                "navigation": navigation,
            }

    def image_response(self, item_id: str) -> tuple[bytes, str]:
        item = self.by_id.get(item_id)
        if item is None:
            raise ValueError(f"Unknown item: {item_id}")
        path = self._source_path(item)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            raise ValueError(f"Registered asset is not an image: {path}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["image_sha256"]:
            raise ValueError(f"Image hash mismatch for {item_id}: {path}")
        return payload, mime_type


def render_app() -> str:
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Image Annotation Lab</title><style>
:root{--ink:#211e1a;--muted:#716a61;--paper:#f3efe7;--panel:#fffdf8;--line:#d8d0c3;--red:#b3332a;--red-dark:#81231d;--green:#286746;--blue:#275d74;--shadow:0 22px 60px rgba(55,43,28,.12)}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#ece5d9,#f7f3ec 56%,#e7ded0);color:var(--ink);font:15px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100dvh}.app{max-width:1560px;margin:auto;padding:28px 34px 18px;display:flex;justify-content:space-between;align-items:end;gap:30px}.kicker{font-size:11px;font-weight:800;letter-spacing:.16em;color:var(--red);text-transform:uppercase}.app h1{font:600 clamp(28px,3vw,48px)/1.04 Georgia,serif;margin:5px 0 7px}.sub{max-width:760px;color:var(--muted);margin:0}.stats{display:flex;gap:1px;background:var(--line);border:1px solid var(--line)}.stat{min-width:92px;background:var(--panel);padding:11px 15px;text-align:center}.stat b{display:block;font:600 24px/1 Georgia,serif}.stat small{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}.shell{max-width:1560px;margin:auto;padding:0 34px 34px;display:grid;grid-template-columns:minmax(0,1.65fr) minmax(350px,.72fr);gap:18px}.workspace,.controls{background:rgba(255,253,248,.92);border:1px solid rgba(105,88,66,.17);box-shadow:var(--shadow)}.workspace{min-height:720px;display:flex;flex-direction:column}.toolbar{padding:13px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:14px;align-items:center}.queues{display:flex;flex-wrap:wrap;gap:6px}.plain,.queue{border:1px solid var(--line);background:#fffaf2;color:var(--ink);padding:9px 12px;font-weight:700;cursor:pointer}.queue.active{background:var(--ink);border-color:var(--ink);color:white}.source{flex:1;padding:14px;display:flex;flex-direction:column;min-height:0}.canvas-wrap{flex:1;min-height:590px;background:#d7d0c6;display:grid;place-items:center;overflow:hidden;position:relative}.canvas-wrap.loading:after{content:"Loading source image…";position:absolute;color:#625b52}.canvas-wrap canvas{display:block;max-width:100%;max-height:100%;touch-action:none;box-shadow:0 10px 35px rgba(32,27,21,.2)}.hint{color:var(--muted);font-size:12px;margin:10px 1px 0}.controls{padding:20px;display:flex;flex-direction:column;gap:15px}.item-head{display:flex;justify-content:space-between;align-items:start;gap:12px}.eyebrow{display:block;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.item-head code{font-size:12px}.navigation{display:grid;grid-template-columns:auto 1fr auto;gap:6px}.navigation select,.navigation button,.field input,.box-grid input{width:100%;border:1px solid var(--line);background:white;color:var(--ink);padding:10px;font:inherit}.navigation button{cursor:pointer;font-weight:800}.crop-preview{height:260px;background:#e5ded2;display:grid;place-items:center;overflow:hidden}.crop-preview canvas{width:100%;height:100%}.first-pass{border-left:4px solid var(--blue);background:#eef5f7;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px}.first-pass strong{font:600 32px/1 Georgia,serif}.first-pass .confidence{font-size:11px;color:var(--muted)}.field label{display:block;font-weight:800;margin-bottom:7px}.field input{font-size:25px;padding:12px 14px;border-color:#aaa094}.field input:focus{outline:3px solid rgba(39,93,116,.2);border-color:var(--blue)}.override{display:inline-flex;margin-top:7px;padding:4px 7px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;font-weight:900;background:#e5eee9;color:var(--green)}.override.changed{background:#f8dfdb;color:var(--red-dark)}.helper{display:block;color:var(--muted);font-size:11px;margin-top:5px}.box-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.box-grid label{font-size:10px;font-weight:800;color:var(--muted)}.box-tools{display:flex;gap:6px}.box-tools button{flex:1;border:1px solid var(--line);background:white;padding:8px;cursor:pointer}.accept{border:0;background:var(--red);color:white;padding:14px 16px;font-weight:900;cursor:pointer}.accept:hover{background:var(--red-dark)}.skip-grid{display:grid;gap:7px}.skip{border:1px solid var(--line);background:#fffaf2;padding:11px;text-align:left;font-weight:750;cursor:pointer}.status{min-height:22px;color:var(--red-dark);font-weight:700}.empty{padding:40px;text-align:center;color:var(--muted)}button:disabled{opacity:.35;cursor:not-allowed}@media(max-width:980px){.app{align-items:start;flex-direction:column}.shell{grid-template-columns:1fr}.workspace{min-height:600px}.canvas-wrap{min-height:470px}}@media(max-width:620px){.app,.shell{padding-left:14px;padding-right:14px}.stats{width:100%}.stat{min-width:0;flex:1}.toolbar{align-items:start;flex-direction:column}.box-grid{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<header class="app"><div><span class="kicker">Human-in-the-loop dataset</span><h1 id="title">Image Annotation Lab</h1><p class="sub" id="instructions">Loading project…</p></div><div class="stats"><div class="stat"><b id="reviewed">—</b><small>reviewed</small></div><div class="stat"><b id="accepted">—</b><small>accepted</small></div><div class="stat"><b id="remaining">—</b><small>remaining</small></div></div></header>
<main class="shell"><section class="workspace"><div class="toolbar"><div class="queues" id="queues"></div><button id="reset" class="plain">Reset view</button></div><div class="source"><div id="canvasWrap" class="canvas-wrap loading"><canvas id="sourceCanvas"></canvas><div id="empty" class="empty" hidden>No unreviewed items remain. Use the jump menu to revisit one.</div></div><p class="hint" id="cropHint">Drag inside the red box to move it. Drag a corner to resize. Accepted pixels retain their native source dimensions.</p></div></section>
<aside class="controls"><div class="item-head"><div><span class="eyebrow" id="queueLabel">QUEUE</span><code id="itemId">Loading…</code></div></div><div class="navigation"><button id="previous" aria-label="Previous item">←</button><select id="itemNav" aria-label="Jump to an item"></select><button id="next" aria-label="Next item">→</button></div><div class="crop-preview"><canvas id="cropCanvas"></canvas></div><div class="first-pass"><span><span class="eyebrow">FIRST-PASS LABEL</span><span class="helper" id="firstSource">Automatically copied below. Type to override it.</span></span><strong id="firstPass">—</strong></div><div class="field"><label id="labelName" for="labelInput">Label</label><input id="labelInput" autocomplete="off" spellcheck="false"><span class="override" id="overrideState">Using first pass</span><span class="helper">The value in this field is the value saved. Your typing always wins.</span></div><div id="boxControls"><span class="eyebrow">EXACT SOURCE BOX</span><div class="box-grid"><label>X<input id="boxX" type="number"></label><label>Y<input id="boxY" type="number"></label><label>W<input id="boxW" type="number" min="1"></label><label>H<input id="boxH" type="number" min="1"></label></div><div class="box-tools"><button data-nudge="expand">Expand 2 px</button><button data-nudge="tighten">Tighten 2 px</button><button data-nudge="reset">Reset</button></div></div><button id="accept" class="accept">Save typed label + image</button><div class="skip-grid" id="skipReasons"></div><div class="status" id="status"></div></aside></main>
<script>
const q=s=>document.querySelector(s),source=q('#sourceCanvas'),crop=q('#cropCanvas'),sctx=source.getContext('2d'),cctx=crop.getContext('2d'),image=new Image(),dpr=devicePixelRatio||1;let config=null,queue=null,state=null,item=null,box=null,initial=null,drag=null,saving=false,loadVersion=0;
async function api(path,options){const response=await fetch(path,options),payload=await response.json();if(!response.ok)throw new Error(payload.error||'Request failed');return payload}
function status(text){q('#status').textContent=text}
async function initialize(){config=await api('/api/config');document.title=config.project.title;q('#title').textContent=config.project.title;q('#instructions').textContent=config.project.instructions;q('#labelName').textContent=config.project.label.name;q('#labelInput').maxLength=config.project.label.max_length||256;q('#labelInput').placeholder=config.project.label.placeholder||'Type the final label';const queues=q('#queues');for(const record of config.queues){const button=document.createElement('button');button.className='queue';button.textContent=record.label;button.dataset.queue=record.id;button.onclick=()=>setQueue(record.id);queues.append(button)}const skips=q('#skipReasons');for(const reason of config.skip_reasons){const button=document.createElement('button');button.className='skip';button.textContent=reason.label;button.onclick=()=>decide('skip',reason.id);skips.append(button)}await setQueue(config.queues[0].id)}
async function setQueue(id){queue=id;document.querySelectorAll('.queue').forEach(button=>button.classList.toggle('active',button.dataset.queue===id));await load()}
async function load(id=null){const version=++loadVersion;item=null;drag=null;q('#canvasWrap').classList.add('loading');status('');const query=new URLSearchParams({queue});if(id)query.set('id',id);try{const loadedState=await api('/api/state?'+query);if(version!==loadVersion)return;state=loadedState;q('#reviewed').textContent=state.progress.reviewed;q('#accepted').textContent=state.progress.accepted;q('#remaining').textContent=state.progress.remaining;renderNavigation();item=state.item;q('#previous').disabled=!state.previous_id;q('#next').disabled=!state.next_id;if(!item){q('#empty').hidden=false;source.hidden=true;q('.controls').style.opacity=.55;q('#canvasWrap').classList.remove('loading');return}q('#empty').hidden=true;source.hidden=false;q('.controls').style.opacity=1;q('#itemId').textContent=item.id;q('#queueLabel').textContent=(config.queues.find(record=>record.id===queue)||{}).label||queue;initial=item.initial_bbox?[...item.initial_bbox]:null;box=item.latest?.bbox?[...item.latest.bbox]:(initial?[...initial]:null);const suggestion=item.first_pass?.label??'',saved=item.latest?.decision==='accept'?item.latest.label:null;q('#labelInput').value=saved??suggestion;q('#firstPass').textContent=suggestion||'—';q('#firstSource').textContent=item.first_pass?.source?`${item.first_pass.source}. Copied below; type to override.`:'Automatically copied below. Type to override it.';q('#boxControls').hidden=item.crop_mode==='none';q('#cropHint').hidden=item.crop_mode==='none';q('#itemNav').value=item.id;updateOverride();image.onload=()=>{if(version!==loadVersion)return;fitCanvas();draw();q('#canvasWrap').classList.remove('loading')};image.onerror=()=>{if(version!==loadVersion)return;status('Could not load the registered source image.');q('#canvasWrap').classList.remove('loading')};image.src=item.image_url}catch(error){if(version!==loadVersion)return;status(error.message);q('#canvasWrap').classList.remove('loading')}}
function renderNavigation(){const menu=q('#itemNav');menu.replaceChildren();for(const record of state.navigation){const option=document.createElement('option');option.value=record.id;const mark=record.decision==='accept'?'✓':record.decision==='skip'?'—':'·';option.textContent=`${mark} ${record.id}${record.label?` · ${record.label}`:''}`;menu.append(option)}}
function fitCanvas(){const maxW=q('#canvasWrap').clientWidth-24,maxH=Math.min(760,innerHeight*.70),scale=Math.min(maxW/image.naturalWidth,maxH/image.naturalHeight,1);source.width=Math.round(image.naturalWidth*scale*dpr);source.height=Math.round(image.naturalHeight*scale*dpr);source.style.width=Math.round(image.naturalWidth*scale)+'px';source.style.height=Math.round(image.naturalHeight*scale)+'px';source.dataset.scale=scale*dpr;crop.width=Math.round(600*dpr);crop.height=Math.round(420*dpr)}
function draw(){if(!item||!image.complete||!image.naturalWidth)return;const scale=Number(source.dataset.scale);sctx.setTransform(scale,0,0,scale,0,0);sctx.clearRect(0,0,source.width/scale,source.height/scale);sctx.drawImage(image,0,0);if(box){const[x,y,w,h]=box;sctx.lineWidth=3/scale;sctx.strokeStyle='#b3332a';sctx.fillStyle='rgba(179,51,42,.08)';sctx.fillRect(x,y,w,h);sctx.strokeRect(x,y,w,h);for(const[hx,hy]of[[x,y],[x+w,y],[x,y+h],[x+w,y+h]]){sctx.fillStyle='white';sctx.fillRect(hx-5/scale,hy-5/scale,10/scale,10/scale);sctx.strokeRect(hx-5/scale,hy-5/scale,10/scale,10/scale)}}drawCrop();syncInputs()}
function drawCrop(){cctx.setTransform(dpr,0,0,dpr,0,0);cctx.fillStyle='#fff';cctx.fillRect(0,0,600,420);const region=box||[0,0,image.naturalWidth,image.naturalHeight],[x,y,w,h]=region,scale=Math.min(550/w,370/h),dw=w*scale,dh=h*scale;cctx.imageSmoothingEnabled=true;cctx.drawImage(image,x,y,w,h,(600-dw)/2,(420-dh)/2,dw,dh)}
function syncInputs(){if(!box)return;['X','Y','W','H'].forEach((key,index)=>q('#box'+key).value=box[index])}
function clampBox(candidate){let[x,y,w,h]=candidate;w=Math.max(1,Math.min(Math.round(w),image.naturalWidth));h=Math.max(1,Math.min(Math.round(h),image.naturalHeight));x=Math.max(0,Math.min(Math.round(x),image.naturalWidth-w));y=Math.max(0,Math.min(Math.round(y),image.naturalHeight-h));return[x,y,w,h]}
function point(event){const rectangle=source.getBoundingClientRect(),scale=Number(source.dataset.scale)/dpr;return[(event.clientX-rectangle.left)/scale,(event.clientY-rectangle.top)/scale]}
source.addEventListener('pointerdown',event=>{if(!box)return;const[px,py]=point(event),[x,y,w,h]=box,radius=14;let mode=null;if(Math.hypot(px-x,py-y)<radius)mode='nw';else if(Math.hypot(px-(x+w),py-y)<radius)mode='ne';else if(Math.hypot(px-x,py-(y+h))<radius)mode='sw';else if(Math.hypot(px-(x+w),py-(y+h))<radius)mode='se';else if(px>=x&&px<=x+w&&py>=y&&py<=y+h)mode='move';if(mode){drag={mode,start:[px,py],box:[...box]};source.setPointerCapture(event.pointerId)}})
source.addEventListener('pointermove',event=>{if(!drag)return;const[px,py]=point(event),dx=Math.round(px-drag.start[0]),dy=Math.round(py-drag.start[1]),[x,y,w,h]=drag.box;let next;if(drag.mode==='move')next=[x+dx,y+dy,w,h];if(drag.mode==='nw')next=[x+dx,y+dy,w-dx,h-dy];if(drag.mode==='ne')next=[x,y+dy,w+dx,h-dy];if(drag.mode==='sw')next=[x+dx,y,w-dx,h+dy];if(drag.mode==='se')next=[x,y,w+dx,h+dy];box=clampBox(next);draw()});source.addEventListener('pointerup',()=>drag=null);source.addEventListener('pointercancel',()=>drag=null)
;['X','Y','W','H'].forEach((key,index)=>q('#box'+key).addEventListener('change',event=>{if(!box)return;const next=[...box];next[index]=Number(event.target.value);box=clampBox(next);draw()}));document.querySelectorAll('[data-nudge]').forEach(button=>button.onclick=()=>{if(!box)return;const action=button.dataset.nudge;if(action==='reset')box=initial?[...initial]:box;if(action==='expand')box=clampBox([box[0]-2,box[1]-2,box[2]+4,box[3]+4]);if(action==='tighten'&&box[2]>4&&box[3]>4)box=clampBox([box[0]+2,box[1]+2,box[2]-4,box[3]-4]);draw()});q('#reset').onclick=()=>{box=initial?[...initial]:box;draw()};
function updateOverride(){if(!item)return;const suggestion=item.first_pass?.label??'',value=q('#labelInput').value,changed=value!==suggestion;q('#overrideState').textContent=changed?'Manual override':'Using first pass';q('#overrideState').classList.toggle('changed',changed)}q('#labelInput').addEventListener('input',updateOverride);
async function decide(decision,skipReason=null){if(!item||saving)return;saving=true;const payload={item_id:item.id,decision,skip_reason:skipReason,label:q('#labelInput').value,bbox:box?box.map(Math.round):null};status('Saving…');document.querySelectorAll('button').forEach(button=>button.disabled=true);try{await api('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});await load()}catch(error){status(error.message)}finally{saving=false;document.querySelectorAll('button').forEach(button=>button.disabled=false);q('#previous').disabled=!state?.previous_id;q('#next').disabled=!state?.next_id}}
q('#accept').onclick=()=>decide('accept');q('#previous').onclick=()=>state.previous_id&&load(state.previous_id);q('#next').onclick=()=>state.next_id&&load(state.next_id);q('#itemNav').onchange=event=>load(event.target.value);addEventListener('resize',()=>{if(item&&image.complete&&image.naturalWidth){fitCanvas();draw()}});addEventListener('keydown',event=>{if(event.ctrlKey&&event.key==='Enter'){event.preventDefault();decide('accept')}});initialize().catch(error=>status(error.message));
</script></body></html>"""


class ImageAnnotationHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args,
        store: ImageAnnotationStore,
        app_html: bytes,
        **kwargs,
    ) -> None:
        self.store = store
        self.app_html = app_html
        super().__init__(*args, **kwargs)

    def _send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/label_app.html"}:
                self._send_bytes(self.app_html, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/config":
                self._send_json(self.store.client_config())
                return
            if parsed.path == "/api/state":
                query = parse_qs(parsed.query)
                queue = query.get("queue", [next(iter(self.store.queues))])[0]
                item_id = query.get("id", [None])[0]
                self._send_json(self.store.client_state(queue, item_id))
                return
            if parsed.path == "/api/image":
                query = parse_qs(parsed.query)
                item_id = query.get("id", [None])[0]
                if item_id is None:
                    raise ValueError("Image id is required")
                payload, content_type = self.store.image_response(item_id)
                self._send_bytes(payload, content_type)
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (FileNotFoundError, ValueError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/decision":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError(f"Request must contain 1 to {MAX_REQUEST_BYTES} bytes")
            payload = json.loads(self.rfile.read(length))
            event = self.store.apply(payload)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except RuntimeError as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json(event, HTTPStatus.CREATED)

    def log_message(self, format: str, *args) -> None:
        print(format % args)


def serve_project(
    project_path: Path,
    event_path: Path,
    dataset_path: Path,
    accepted_image_dir: Path,
    host: str = "127.0.0.1",
    port: int = 3478,
    open_browser: bool = True,
) -> None:
    store = ImageAnnotationStore(
        project_path, event_path, dataset_path, accepted_image_dir
    )
    app_html = render_app().encode("utf-8")
    handler = partial(ImageAnnotationHandler, store=store, app_html=app_html)
    server = ThreadingHTTPServer((host, port), handler)
    browser_host = "127.0.0.1" if host in {"", "0.0.0.0"} else host
    url = f"http://{browser_host}:{port}/"
    print(f"project: {project_path}")
    print(f"events: {event_path}")
    print(f"dataset: {dataset_path}")
    print(f"app: {url}")
    if open_browser:
        timer = threading.Timer(0.4, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve a manifest-driven local image annotation project."
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--accepted-images", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3478)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    project_path = args.project.resolve()
    output = project_path.parent
    stem = project_path.stem
    serve_project(
        project_path,
        (args.events or output / f"{stem}.events.jsonl").resolve(),
        (args.dataset or output / f"{stem}.dataset.json").resolve(),
        (args.accepted_images or output / f"{stem}.accepted").resolve(),
        args.host,
        args.port,
        not args.no_open,
    )


if __name__ == "__main__":
    main()
