from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from palimpsest.image_labeling import ImageAnnotationStore, sha256


ROOT = Path(__file__).parents[1]
EXPERIMENT = ROOT / "experiments" / "generative_hand_font"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adaptation = load_module("generative_font_adaptation", EXPERIMENT / "adapt.py")
calibration = load_module("generative_font_calibration", EXPERIMENT / "calibrate.py")
review = load_module("generative_font_review", EXPERIMENT / "review.py")
attestation = load_module("generative_font_attestation", EXPERIMENT / "attest.py")
benchmarking = load_module("generative_font_benchmark", EXPERIMENT / "benchmark.py")
alignment = load_module(
    "generative_font_glyph_alignment", EXPERIMENT / "glyph_alignment.py"
)


def test_writer_target_cleaning_removes_tiny_residual_components() -> None:
    gray = np.full((64, 64), 255, dtype=np.uint8)
    gray[20:44, 24:40] = 0
    gray[3, 3] = 0

    cleaned = adaptation.clean_writer_image(gray)

    assert cleaned[3, 3] == 255
    assert np.mean(cleaned[22:42, 26:38]) < 10


def test_kai_gravity_alignment_preserves_aspect_and_matches_centroid() -> None:
    source = np.full((56, 38), 255, dtype=np.uint8)
    source[6:50, 8:31] = 0
    source[15:20, 3:7] = 0
    kai = np.full((128, 128), 255, dtype=np.uint8)
    kai[20:108, 37:91] = 0

    aligned, record = alignment.align_to_kai(source, kai)
    source_bbox = alignment.ink_geometry(source)["bbox"]
    aligned_bbox = alignment.ink_geometry(aligned)["bbox"]
    source_ratio = (source_bbox[2] - source_bbox[0]) / (source_bbox[3] - source_bbox[1])
    aligned_ratio = (aligned_bbox[2] - aligned_bbox[0]) / (
        aligned_bbox[3] - aligned_bbox[1]
    )

    assert abs(source_ratio - aligned_ratio) < 0.03
    assert record["centroid_error"] < 0.1
    assert record["margin_breached"] is False
    assert record["canvas_edge_touched"] is False
    assert record["transform"] == "translation_plus_isotropic_scale"


def test_glyph_smoothing_preserves_topology_and_gap_repair_is_audited() -> None:
    aligned = np.full((128, 128), 255, dtype=np.uint8)
    aligned[45:71, 30:50] = 0
    aligned[45:71, 51:71] = 0

    _, _, diagnostics = alignment.refine_ink(aligned)

    assert abs(diagnostics["smoothed_mass_change_fraction"]) < 0.01
    assert diagnostics["smoothed_topology_changed"] is False
    assert diagnostics["topology"]["gravity"]["components"] == 2
    assert diagnostics["repair_added_pixels"] > 0
    assert diagnostics["repair_topology_changed"] is True
    assert diagnostics["topology"]["micro_repair"]["components"] == 1


def test_writer_residual_blend_has_exact_endpoints_and_midpoint() -> None:
    unadapted = np.full((4, 4), 240, dtype=np.uint8)
    adapted = np.full((4, 4), 40, dtype=np.uint8)

    assert np.array_equal(calibration.blend(unadapted, adapted, 0.0), unadapted)
    assert np.array_equal(calibration.blend(unadapted, adapted, 1.0), adapted)
    assert np.all(calibration.blend(unadapted, adapted, 0.6) == 120)


def test_content_gate_rejects_catastrophic_rank_at_the_limit() -> None:
    metrics = {
        "top5": calibration.MINIMUM_TOP5,
        "catastrophic_rank_gt_20": calibration.MAXIMUM_CATASTROPHIC_RATE,
    }

    assert not calibration.content_eligible(metrics)


def test_blind_adjudication_counts_only_calibrated_candidate_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = {
        f"crop-{index}": {
            "character": chr(ord("A") + index),
            "A": "p3477_calibrated" if index < 10 else "p3477_unadapted",
            "B": "wrong_writer_adapted",
            "C": "p3477_unadapted",
        }
        for index in range(12)
    }
    choices = {crop_id: "A" for crop_id in key}
    monkeypatch.setattr(review, "sha256", lambda _: "a" * 64)

    record = review.adjudicate(choices, key)

    assert record["calibrated_wins"] == 10
    assert record["required_wins"] == 10
    assert record["passed"] is True


def test_cv_proposal_filter_accepts_centered_ink_and_rejects_blank() -> None:
    clear = np.full((80, 80, 3), 255, dtype=np.uint8)
    clear[24:56, 25:55] = 0
    blank = np.full((80, 80, 3), 255, dtype=np.uint8)

    accepted = attestation.proposal_score(clear, [8, 8, 64, 64], 64.0)

    assert accepted is not None
    assert accepted[1]["edge_ink_fraction"] == 0.0
    assert attestation.proposal_score(blank, [8, 8, 64, 64], 64.0) is None


def test_p3477_adapter_applies_only_fingerprinted_luna_overlay_before_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proposal_path = tmp_path / "crop_proposals_v2.json"
    project_path = tmp_path / "annotation_project.json"
    proposals = {
        "source_manifest_path": "source/manifest.json",
        "source_manifest_sha256": "b" * 64,
        "pages": {
            "page_0000": {
                "image_path": "images/page.jpg",
                "image_sha256": "c" * 64,
                "width": 100,
                "height": 120,
            }
        },
        "records": [
            {
                "proposal_id": "page_0000_cv2_c01_s02",
                "page_id": "page_0000",
                "role": "writer_specimen",
                "column_index": 1,
                "slot_index": 2,
                "detected_bbox": [10, 20, 30, 40],
                "initial_bbox": [8, 18, 34, 44],
                "cv_score": 0.9,
                "cv_diagnostics": {"edge_ink_fraction": 0.0},
                "silver_hypothesis": "甲",
                "silver_source_crop_id": "silver-1",
            }
        ],
    }
    proposal_path.write_text(json.dumps(proposals), encoding="utf-8")
    monkeypatch.setattr(attestation, "ROOT", tmp_path)
    monkeypatch.setattr(attestation, "PROPOSAL_PATH", proposal_path)
    monkeypatch.setattr(attestation, "PROJECT_PATH", project_path)

    project = attestation.build_annotation_project(proposals)
    item = project["items"][0]

    assert project["metadata"]["first_pass_is_training_truth"] is False
    assert project["queues"][0]["minimum_distinct_labels"] == 24
    assert item["first_pass"] == {
        "label": "甲",
        "source": "Automated sequence-alignment first pass",
        "trusted": False,
    }
    assert item["initial_bbox"] == [8, 18, 34, 44]
    assert item["metadata"]["column_index"] == 1
    assert json.loads(project_path.read_text(encoding="utf-8")) == project

    first_pass_path = tmp_path / "luna_first_pass.json"
    event_path = tmp_path / "annotation_events.jsonl"
    monkeypatch.setattr(attestation, "FIRST_PASS_PATH", first_pass_path)
    monkeypatch.setattr(attestation, "EVENT_PATH", event_path)
    luna_record = {
        "schema_version": 1,
        "project_id": project["id"],
        "source_project_path": project_path.name,
        "source_project_sha256": "0" * 64,
        "generated_at": "2026-07-21T00:00:00+00:00",
        "agent": "gpt-5.6-luna-designated",
        "predictions": [
            {
                "item_id": item["id"],
                "label": "乙",
                "confidence": 0.8,
                "source": "luna_agent_visual_first_pass",
            }
        ],
    }
    first_pass_path.write_text(
        json.dumps(luna_record, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="targets a different annotation project"):
        attestation.prepare_annotation_project(proposals)

    luna_record["source_project_sha256"] = sha256(project_path)
    first_pass_path.write_text(
        json.dumps(luna_record, ensure_ascii=False), encoding="utf-8"
    )
    integrated = attestation.prepare_annotation_project(proposals)
    resumed = attestation.prepare_annotation_project(proposals)

    assert integrated["items"][0]["first_pass"] == {
        "label": "乙",
        "source": "luna_agent_visual_first_pass",
        "confidence": 0.8,
        "trusted": False,
    }
    assert integrated["metadata"]["first_pass_sidecar_sha256"] == sha256(
        first_pass_path
    )
    assert integrated["metadata"]["first_pass_is_training_truth"] is False
    assert resumed == integrated

    integrated_sha256 = sha256(project_path)
    event_path.touch()
    luna_record["predictions"][0]["label"] = "丁"
    first_pass_path.write_text(
        json.dumps(luna_record, ensure_ascii=False), encoding="utf-8"
    )

    assert attestation.prepare_annotation_project(proposals) == integrated
    assert sha256(project_path) == integrated_sha256


def test_human_annotations_flow_into_benchmark_and_adaptation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "page.png"
    source = np.full((64, 64, 3), 255, dtype=np.uint8)
    source[16:48, 18:46] = 0
    assert cv2.imwrite(str(source_path), source)
    source_sha256 = sha256(source_path)
    proposal_path = tmp_path / "proposals.json"
    proposals = {
        "schema_version": 2,
        "source_manifest_sha256": "d" * 64,
    }
    proposal_path.write_text(json.dumps(proposals), encoding="utf-8")
    project_path = tmp_path / "project.json"
    event_path = tmp_path / "events.jsonl"
    dataset_path = tmp_path / "annotation_dataset.json"
    accepted_dir = tmp_path / "accepted"
    items = []
    for index in range(9):
        queue = "writer_specimen" if index < 8 else "held_out_evaluation"
        items.append(
            {
                "id": f"item-{index}",
                "queue": queue,
                "image_path": source_path.name,
                "image_sha256": source_sha256,
                "image_width": 64,
                "image_height": 64,
                "initial_bbox": [12, 10, 40, 44],
                "first_pass": {
                    "label": chr(0x4E00 + index),
                    "source": "fixture first pass",
                    "trusted": False,
                },
                "metadata": {
                    "proposal_id": f"proposal-{index}",
                    "page_id": "page_0000" if index < 8 else "page_0001",
                    "role": queue,
                    "column_index": index,
                    "slot_index": 0,
                    "cv_score": 1.0 - index / 100,
                },
            }
        )
    project = {
        "schema_version": 1,
        "id": "p3477-generative-hand-font-crops",
        "title": "P.3477 fixture",
        "instructions": "Annotate fixture crops.",
        "asset_root": ".",
        "crop_mode": "required",
        "label": {
            "name": "Character",
            "required": True,
            "max_length": 1,
        },
        "queues": [
            {
                "id": "writer_specimen",
                "label": "Training",
                "minimum_distinct_labels": 8,
            },
            {
                "id": "held_out_evaluation",
                "label": "Held out",
                "minimum_distinct_labels": 1,
            },
        ],
        "skip_reasons": [{"id": "unclear", "label": "Unclear"}],
        "metadata": {
            "proposal_path": proposal_path.name,
            "proposal_sha256": sha256(proposal_path),
            "source_manifest_sha256": "d" * 64,
        },
        "items": items,
    }
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    store = ImageAnnotationStore(project_path, event_path, dataset_path, accepted_dir)
    for index, item in enumerate(items):
        store.apply(
            {
                "item_id": item["id"],
                "decision": "accept",
                "label": chr(0x4E00 + index),
                "bbox": [12, 10, 40, 44],
            }
        )
    generation_path = tmp_path / "generation.json"
    generation_path.write_text(
        json.dumps(
            {
                "source_font": "source.ttf",
                "source_font_sha256": "1" * 64,
                "wrong_style_font": "wrong.ttf",
                "wrong_style_font_sha256": "2" * 64,
                "implementation_url": "https://example.test/model",
                "implementation_commit": "abc123",
                "checkpoint_sha256": "3" * 64,
                "candidate_characters": [chr(0x4E00 + index) for index in range(9)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmarking, "ROOT", tmp_path)
    monkeypatch.setattr(benchmarking, "DATASET_PATH", dataset_path)
    monkeypatch.setattr(benchmarking, "GENERATION_PATH", generation_path)
    monkeypatch.setattr(adaptation, "ROOT", tmp_path)

    benchmark = benchmarking.build_benchmark()
    adaptation.validate_benchmark(benchmark, 8)

    assert set(benchmark["specimen_budgets"]) == {"8"}
    assert benchmark["specimen_budgets"]["8"][0]["label_status"] == (
        "human_attested_gold"
    )
    assert len(benchmark["targets"]["strict_unseen_from_reference_page"]) == 1
    assert benchmark["source_records"]["annotation_dataset_sha256"] == sha256(
        dataset_path
    )
    assert benchmark["source_records"]["annotation_project_sha256"] == sha256(
        project_path
    )
    assert benchmark["source_records"]["proposal_sha256"] == sha256(proposal_path)
    assert benchmark["source_records"]["generation_sha256"] == sha256(generation_path)

    changed_dataset_fingerprint = {
        **benchmark,
        "source_records": {
            **benchmark["source_records"],
            "annotation_dataset_sha256": "0" * 64,
        },
    }
    with pytest.raises(RuntimeError, match="human annotation fingerprint changed"):
        adaptation.validate_benchmark(changed_dataset_fingerprint, 8)

    disconnected_provenance = {
        **benchmark,
        "source_records": {
            **benchmark["source_records"],
            "proposal_sha256": "0" * 64,
        },
    }
    with pytest.raises(RuntimeError, match="provenance fingerprints disagree"):
        adaptation.validate_benchmark(disconnected_provenance, 8)

    specimen_path = tmp_path / benchmark["specimen_budgets"]["8"][0]["crop_path"]
    specimen_path.write_bytes(b"tampered after attestation")
    with pytest.raises(ValueError, match="Attested crop hash mismatch"):
        benchmarking.build_benchmark()
    with pytest.raises(RuntimeError, match="specimen fingerprint changed"):
        adaptation.validate_benchmark(benchmark, 8)


@pytest.mark.parametrize(
    "benchmark",
    [
        {
            "schema_version": 1,
            "evidence_status": "silver",
            "source_records": {},
            "specimen_budgets": {"8": []},
        },
        {
            "schema_version": 2,
            "evidence_status": "human_attested_gold",
            "source_records": {},
            "specimen_budgets": {"8": []},
        },
    ],
    ids=["legacy-silver", "missing-annotation-fingerprint"],
)
def test_adaptation_refuses_unverified_benchmarks(benchmark: dict) -> None:
    with pytest.raises(RuntimeError, match="human-annotated"):
        adaptation.validate_benchmark(benchmark, 8)


def test_benchmark_refuses_an_in_progress_annotation_dataset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "annotation_dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "human_image_annotation_dataset",
                "project_id": "p3477-generative-hand-font-crops",
                "status": "annotation_in_progress",
                "dataset_ready": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmarking, "DATASET_PATH", dataset_path)

    with pytest.raises(ValueError, match="not immutable human gold"):
        benchmarking.build_benchmark()


def test_benchmark_requires_annotation_dataset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        benchmarking,
        "DATASET_PATH",
        tmp_path / "missing-annotation-dataset.json",
    )

    with pytest.raises(FileNotFoundError, match="Human image annotation"):
        benchmarking.build_benchmark()
