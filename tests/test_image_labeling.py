from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from palimpsest.image_labeling import ImageAnnotationStore, sha256


def write_source_image(path: Path) -> None:
    image = np.full((64, 72, 3), 248, dtype=np.uint8)
    image[15:51, 20:54] = (20, 30, 40)
    assert cv2.imwrite(str(path), image)


def write_project(
    tmp_path: Path,
    *,
    crop_mode: str = "required",
    minimum_distinct_labels: int = 2,
) -> tuple[Path, Path, Path, Path, Path]:
    source_path = tmp_path / "source.png"
    write_source_image(source_path)
    items = [
        {
            "id": "item-1",
            "queue": "training",
            "image_path": source_path.name,
            "image_sha256": sha256(source_path),
            "image_width": 72,
            "image_height": 64,
            "crop_mode": crop_mode,
            "first_pass": {
                "label": "甲",
                "source": "fixture model",
                "trusted": False,
            },
            "metadata": {"rank": 1},
        },
        {
            "id": "item-2",
            "queue": "training",
            "image_path": source_path.name,
            "image_sha256": sha256(source_path),
            "image_width": 72,
            "image_height": 64,
            "crop_mode": crop_mode,
            "first_pass": {
                "label": "丙",
                "source": "fixture model",
                "trusted": False,
            },
            "metadata": {"rank": 2},
        },
    ]
    if crop_mode != "none":
        for item in items:
            item["initial_bbox"] = [12, 10, 32, 36]
    project = {
        "schema_version": 1,
        "id": "fixture-image-project",
        "title": "Fixture image project",
        "instructions": "Correct the proposed label and crop.",
        "asset_root": ".",
        "crop_mode": crop_mode,
        "label": {
            "name": "Final label",
            "required": True,
            "max_length": 1 if crop_mode != "none" else 32,
        },
        "queues": [
            {
                "id": "training",
                "label": "Training",
                "minimum_distinct_labels": minimum_distinct_labels,
            }
        ],
        "skip_reasons": [
            {"id": "unclear", "label": "Label is unclear"},
            {"id": "unusable", "label": "Image is unusable"},
        ],
        "items": items,
    }
    project_path = tmp_path / "project.json"
    event_path = tmp_path / "events.jsonl"
    dataset_path = tmp_path / "dataset.json"
    accepted_dir = tmp_path / "accepted"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return project_path, event_path, dataset_path, accepted_dir, source_path


def test_annotations_append_revisions_materialize_ready_dataset_and_resume(
    tmp_path: Path,
) -> None:
    project_path, event_path, dataset_path, accepted_dir, _ = write_project(tmp_path)
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)

    first = store.apply(
        {
            "item_id": "item-1",
            "decision": "accept",
            "label": "甲",
            "bbox": [12, 10, 32, 36],
        }
    )
    in_progress = json.loads(dataset_path.read_text(encoding="utf-8"))
    skipped = store.apply(
        {
            "item_id": "item-2",
            "decision": "skip",
            "skip_reason": "unclear",
            "label": "",
            "bbox": None,
        }
    )
    revised = store.apply(
        {
            "item_id": "item-2",
            "decision": "accept",
            "label": "乙",
            "bbox": [11, 11, 30, 31],
        }
    )
    persisted_events = [
        json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
    ]
    resumed = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    second_record = next(
        record for record in dataset["records"] if record["item_id"] == "item-2"
    )
    second_crop_path = dataset_path.parent / second_record["accepted_image_path"]
    second_crop = cv2.imread(str(second_crop_path), cv2.IMREAD_UNCHANGED)

    assert in_progress["status"] == "annotation_in_progress"
    assert in_progress["dataset_ready"] is False
    assert in_progress["queue_summaries"]["training"]["remaining"] == 1
    assert persisted_events == [first, skipped, revised]
    assert first["label_was_overridden"] is False
    assert skipped["sequence"] == 2
    assert revised["sequence"] == 3
    assert revised["revision"] == 2
    assert revised["supersedes_sequence"] == 2
    assert revised["label_was_overridden"] is True
    assert revised["bbox_was_adjusted"] is True
    assert [event["sequence"] for event in persisted_events] == [1, 2, 3]
    assert dataset["status"] == "human_attested_gold"
    assert dataset["dataset_ready"] is True
    assert dataset["event_count"] == 3
    assert dataset["queue_summaries"]["training"] == {
        "total": 2,
        "reviewed": 2,
        "accepted": 2,
        "skipped": 0,
        "remaining": 0,
        "distinct_labels": 2,
        "minimum_distinct_labels": 2,
        "ready": True,
    }
    assert second_record["label"] == "乙"
    assert second_record["first_pass"]["label"] == "丙"
    assert second_crop is not None
    assert second_crop.shape[:2] == (31, 30)
    assert second_record["revision"] == 2
    assert second_record["event_sequence"] == 3
    assert second_record["accepted_image_sha256"] == sha256(second_crop_path)
    assert second_crop_path.parent.resolve() == accepted_dir.resolve()
    state = resumed.client_state("training", "item-2")
    assert state["item"]["latest"]["sequence"] == 3
    assert state["previous_id"] == "item-1"
    assert state["navigation"] == [
        {"id": "item-1", "decision": "accept", "label": "甲"},
        {"id": "item-2", "decision": "accept", "label": "乙"},
    ]


def test_resume_rejects_broken_revision_lineage(tmp_path: Path) -> None:
    project_path, event_path, dataset_path, accepted_dir, _ = write_project(tmp_path)
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)
    for label in ("甲", "乙"):
        store.apply(
            {
                "item_id": "item-1",
                "decision": "accept",
                "label": label,
                "bbox": [12, 10, 32, 36],
            }
        )
    events = [
        json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
    ]
    events[1]["supersedes_sequence"] = None
    event_path.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid supersedes_sequence"):
        ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)


def test_event_log_rejects_a_mutated_project_manifest(tmp_path: Path) -> None:
    project_path, event_path, dataset_path, accepted_dir, _ = write_project(tmp_path)
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)
    store.apply(
        {
            "item_id": "item-1",
            "decision": "accept",
            "label": "甲",
            "bbox": [12, 10, 32, 36],
        }
    )
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["title"] = "Mutated after annotation"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="different project revision"):
        ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)


def test_decision_rejects_a_source_image_mutated_after_project_load(
    tmp_path: Path,
) -> None:
    project_path, event_path, dataset_path, accepted_dir, source_path = write_project(
        tmp_path
    )
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)
    changed = np.full((64, 72, 3), 255, dtype=np.uint8)
    changed[4:20, 6:25] = (0, 0, 0)
    assert cv2.imwrite(str(source_path), changed)

    with pytest.raises(ValueError, match="Image hash mismatch"):
        store.apply(
            {
                "item_id": "item-1",
                "decision": "accept",
                "label": "甲",
                "bbox": [12, 10, 32, 36],
            }
        )

    assert event_path.exists() is False
    assert json.loads(dataset_path.read_text(encoding="utf-8"))["event_count"] == 0
    assert accepted_dir.exists() is False


def test_full_image_project_saves_human_override_without_copying_source(
    tmp_path: Path,
) -> None:
    project_path, event_path, dataset_path, accepted_dir, source_path = write_project(
        tmp_path,
        crop_mode="none",
        minimum_distinct_labels=1,
    )
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)

    event = store.apply(
        {
            "item_id": "item-1",
            "decision": "accept",
            "label": "human override",
            "bbox": None,
        }
    )
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    accepted_path = dataset_path.parent / event["accepted_image_path"]

    assert event["first_pass_label"] == "甲"
    assert event["label"] == "human override"
    assert event["label_was_overridden"] is True
    assert event["accepted_image_kind"] == "source"
    assert accepted_path.resolve() == source_path.resolve()
    assert event["accepted_image_sha256"] == sha256(source_path)
    assert accepted_dir.exists() is False
    assert dataset["records"][0]["metadata"] == {"rank": 1}
