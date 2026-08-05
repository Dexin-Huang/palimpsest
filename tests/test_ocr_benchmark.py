from __future__ import annotations

import importlib.util
import json
import sys
import types
import zipfile
from types import SimpleNamespace
from pathlib import Path

import cv2
import numpy as np
import pytest

_ROOT = Path(__file__).parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


benchmark = _load_module(
    "_ocr_benchmark", _ROOT / "experiments" / "ocr_benchmark" / "benchmark.py"
)
fetch = _load_module("_ocr_fetch", _ROOT / "experiments" / "ocr_benchmark" / "fetch.py")
separation_adapter = _load_module(
    "_ocr_separation_adapter",
    _ROOT / "experiments" / "ocr_benchmark" / "separation_adapter.py",
)
rfdetr_candidate = _load_module(
    "_ocr_rfdetr_candidate",
    _ROOT / "experiments" / "ocr_benchmark" / "rfdetr_candidate.py",
)
rfdetr_dataset = _load_module(
    "_ocr_rfdetr_dataset",
    _ROOT / "experiments" / "ocr_benchmark" / "rfdetr_dataset.py",
)
kuzushiji_dataset = _load_module(
    "_ocr_kuzushiji_dataset",
    _ROOT / "experiments" / "ocr_benchmark" / "kuzushiji_dataset.py",
)
localization_audit = _load_module(
    "_ocr_localization_audit",
    _ROOT / "experiments" / "ocr_benchmark" / "localization_audit.py",
)
make_align_suite = _load_module(
    "_ocr_make_align_suite",
    _ROOT / "experiments" / "ocr_benchmark" / "make_align_suite.py",
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_scoring_rejects_incomplete_prediction_sets(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_jsonl(
        manifest,
        [
            {"case_id": "a", "strata": ["TKH"], "text": "甲", "characters": []},
            {"case_id": "b", "strata": ["TKH"], "text": "乙", "characters": []},
        ],
    )
    _write_jsonl(predictions, [{"case_id": "a", "text": "甲"}])

    with pytest.raises(ValueError, match="missing 1 predictions"):
        benchmark.score(manifest, predictions)


def test_localization_scores_unlabeled_character_boxes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_jsonl(
        manifest,
        [
            {
                "case_id": "a",
                "strata": ["TKH"],
                "characters": [
                    {"text": "天", "bbox": [0, 0, 10, 10]},
                    {"text": "地", "bbox": [20, 0, 10, 10]},
                ],
            }
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "case_id": "a",
                "characters": [
                    {"bbox": [0, 0, 10, 10], "score": 0.9},
                    {"bbox": [20, 0, 10, 10], "score": 0.8},
                ],
            }
        ],
    )

    report = benchmark.score(manifest, predictions)

    assert report["objective"] == "unlabeled_character_localization"
    assert report["summary"]["f1"] == 1.0
    assert report["summary"]["ap50"] == 1.0
    assert report["protected_slices"]["TKH"]["f1"] == 1.0


def test_comparison_uses_paired_detection_f1() -> None:
    baseline = {
        "summary": {
            "case_coverage": 1.0,
            "precision": 0.5,
            "recall": 0.5,
            "f1": 0.5,
            "ap50": 0.5,
        },
        "protected_slices": {"TKH": {"f1": 0.5}},
        "per_case": [
            {"case_id": "a", "detection": {"f1": 0.4}},
            {"case_id": "b", "detection": {"f1": 0.6}},
        ],
        "predictions_sha256": "a" * 64,
    }
    challenger = {
        "summary": {
            "case_coverage": 1.0,
            "precision": 0.6,
            "recall": 0.6,
            "f1": 0.6,
            "ap50": 0.6,
        },
        "protected_slices": {"TKH": {"f1": 0.6}},
        "per_case": [
            {"case_id": "a", "detection": {"f1": 0.5}},
            {"case_id": "b", "detection": {"f1": 0.7}},
        ],
        "predictions_sha256": "b" * 64,
    }

    comparison = benchmark.compare_reports(baseline, challenger)

    assert comparison["decision"] == "challenger_wins"
    assert comparison["primary_delta"]["metric"] == "per_case_detection_f1"
    assert comparison["primary_delta"]["direction"] == "higher_is_better"


def test_development_selection_is_balanced_and_order_independent() -> None:
    paths = [
        f"/source/{corpus}/img/{index:03d}.jpg"
        for corpus in fetch.CORPORA
        for index in range(5)
    ]

    selected = fetch.select_development(paths, 2)
    reversed_selected = fetch.select_development(list(reversed(paths)), 2)

    assert selected == reversed_selected
    assert {
        corpus: sum(
            fetch.archive_image_path(path).startswith(f"{corpus}/") for path in selected
        )
        for corpus in fetch.CORPORA
    } == {corpus: 2 for corpus in fetch.CORPORA}


def test_ancientdoc_labels_resolve_canonical_images_and_newlines() -> None:
    payload = ("type,name,OCR\n史类,史记page_1.png,天地\\n玄黄\n").encode("utf-8")
    repository_files = [
        "imgs/imgs/史类/史记/page_1.png",
        "imgs/史类/史记/page_1.png",
    ]

    records = fetch.parse_ancientdoc_labels(payload, repository_files)

    assert records == [
        {
            "category": "史类",
            "book": "史记",
            "page": "page_1.png",
            "source_path": "imgs/史类/史记/page_1.png",
            "transcription": "天地\n玄黄",
        }
    ]


def test_ancientdoc_partitions_are_book_disjoint_and_order_independent() -> None:
    records = [
        {
            "category": category,
            "book": book,
            "page": f"page_{page}.png",
            "source_path": f"imgs/{category}/{book}/page_{page}.png",
            "transcription": f"{category}-{book}-{page}",
        }
        for category in ("史类", "经类")
        for book in ("甲书", "乙书", "丙书")
        for page in range(3)
    ]

    selected = fetch.select_ancientdoc_partitions(records, 2)
    reversed_selected = fetch.select_ancientdoc_partitions(list(reversed(records)), 2)

    assert selected == reversed_selected
    development, qualification, books = selected
    assert len(development) == 4
    assert {(record["category"], record["book"]) for record in development}.isdisjoint(
        (record["category"], record["book"]) for record in qualification
    )
    assert all(
        book_pair["development"] != book_pair["qualification_reserve"]
        for book_pair in books.values()
    )


def test_qualification_selection_is_balanced_and_order_independent() -> None:
    paths = [
        f"/sealed/{corpus}/img/{index:03d}.jpg"
        for corpus in fetch.CORPORA
        for index in range(5)
    ]

    selected = fetch.select_qualification(paths, 2)

    assert selected == fetch.select_qualification(list(reversed(paths)), 2)
    assert {
        corpus: sum(
            fetch.archive_image_path(path).startswith(f"{corpus}/") for path in selected
        )
        for corpus in fetch.CORPORA
    } == {corpus: 2 for corpus in fetch.CORPORA}


def test_qualification_curation_replaces_malformed_official_annotations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [
        f"/sealed/{corpus}/img/{index:03d}.jpg"
        for corpus in fetch.CORPORA
        for index in range(3)
    ]

    def fake_build_case(_, source_path: str, split: str, rank: int):
        if source_path.endswith("000.jpg"):
            raise ValueError("invalid official character box")
        corpus = fetch.archive_image_path(source_path).split("/", 1)[0]
        return {
            "case_id": source_path,
            "selection_rank": rank,
            "split": split,
            "strata": [corpus],
        }

    monkeypatch.setattr(fetch, "build_case", fake_build_case)
    monkeypatch.setattr(fetch, "validate_line_character_consistency", lambda _: None)

    cases, exclusions = fetch.build_qualification_cases(tmp_path, paths, 2)

    assert len(cases) == 6
    assert len(exclusions) == 3
    assert {case["selection_rank"] for case in cases} == set(range(1, 7))
    assert {case["strata"][0] for case in cases} == set(fetch.CORPORA)


def test_training_selection_excludes_frozen_development_pages() -> None:
    paths = [
        f"/source/{corpus}/img/{index:03d}.jpg"
        for corpus in fetch.CORPORA
        for index in range(fetch.DEVELOPMENT_RESERVE_PER_CORPUS + 3)
    ]

    development = set(
        fetch.select_development(paths, fetch.DEVELOPMENT_RESERVE_PER_CORPUS)
    )
    training = set(fetch.select_training(paths, 3))

    assert not development & training
    assert len(training) == 3 * len(fetch.CORPORA)


def test_all_training_selection_uses_every_non_development_page() -> None:
    paths = [
        f"/source/{corpus}/img/{index:03d}.jpg"
        for corpus in fetch.CORPORA
        for index in range(fetch.DEVELOPMENT_RESERVE_PER_CORPUS + 3)
    ]

    development = set(
        fetch.select_development(paths, fetch.DEVELOPMENT_RESERVE_PER_CORPUS)
    )
    training = set(fetch.select_all_training(paths))

    assert training == set(paths) - development
    assert len(training) == 3 * len(fetch.CORPORA)


def test_all_training_curation_records_malformed_annotation_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ["/source/TKH/img/good.jpg", "/source/TKH/img/malformed.jpg"]

    def fake_build_case(_, source_path: str, split: str, rank: int):
        if source_path.endswith("malformed.jpg"):
            raise ValueError("invalid MTHv2 character box")
        return {
            "case_id": source_path,
            "source_path": source_path,
            "split": split,
            "selection_rank": rank,
            "sha256": {
                "image": "image-good",
                "text_lines": "text-good",
                "characters": "characters-good",
            },
        }

    monkeypatch.setattr(fetch, "build_case", fake_build_case)

    cases, exclusions = fetch.build_training_cases(tmp_path, paths)

    assert cases == [
        {
            "case_id": "/source/TKH/img/good.jpg",
            "source_path": "/source/TKH/img/good.jpg",
            "split": "training",
            "selection_rank": 1,
            "sha256": {
                "image": "image-good",
                "text_lines": "text-good",
                "characters": "characters-good",
            },
        }
    ]
    assert exclusions == [
        {
            "source_path": "/source/TKH/img/malformed.jpg",
            "reason": "invalid MTHv2 character box",
        }
    ]


def test_all_training_curation_excludes_duplicate_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [
        "/source/MTH1000/img/first.png",
        "/source/MTH1000/img/duplicate.png",
        "/source/MTH1000/img/unique.png",
    ]

    def fake_build_case(_, source_path: str, split: str, rank: int):
        stem = Path(source_path).stem
        return {
            "case_id": stem,
            "source_path": source_path,
            "split": split,
            "selection_rank": rank,
            "sha256": {
                "image": "shared" if stem != "unique" else "unique",
                "text_lines": f"text-{stem}",
                "characters": f"characters-{stem}",
            },
        }

    monkeypatch.setattr(fetch, "build_case", fake_build_case)

    cases, exclusions = fetch.build_training_cases(tmp_path, paths)

    assert [case["case_id"] for case in cases] == ["first", "unique"]
    assert [case["selection_rank"] for case in cases] == [1, 2]
    assert exclusions == [
        {
            "source_path": "/source/MTH1000/img/duplicate.png",
            "reason": (
                "duplicate image identity with an earlier development or training "
                "page: shared"
            ),
        }
    ]


def test_mthv2_label_parsers_preserve_reading_order_and_boxes() -> None:
    text, lines = fetch.parse_text_lines(
        "天地,10,20,10,0,20,0,20,20\n玄黄,0,20,0,0,9,0,9,20\n"
    )
    characters = fetch.parse_characters("天 10 0 20 10\n地 10 10 20 20\n")

    assert text == "天地\n玄黄"
    assert lines[0]["polygon"] == [10, 20, 10, 0, 20, 0, 20, 20]
    assert characters == [
        {"text": "天", "bbox": [10, 0, 10, 10]},
        {"text": "地", "bbox": [10, 10, 10, 10]},
    ]


def test_separation_adapter_restores_source_image_coordinates() -> None:
    assert separation_adapter.source_bbox(
        (10, 20, 30, 40),
        {"frame": [100, 200, 1000, 1200], "gutter": [15, 900]},
    ) == [125, 220, 30, 40]


def test_rule_suppression_preserves_glyph_sized_ink() -> None:
    page = np.full((200, 300, 3), 255, np.uint8)
    cv2.line(page, (10, 30), (290, 30), (0, 0, 0), 3)
    cv2.rectangle(page, (100, 100), (120, 120), (0, 0, 0), -1)

    cleaned = separation_adapter.suppress_horizontal_rules(page)

    assert int(cleaned[30, 150].mean()) > 240
    assert int(cleaned[110, 110].mean()) < 10


def test_rfdetr_dataset_tiles_boxes_without_split_leakage(tmp_path: Path) -> None:
    train_image = tmp_path / "train.jpg"
    valid_image = tmp_path / "valid.jpg"
    image = np.full((700, 700, 3), 255, np.uint8)
    assert cv2.imwrite(str(train_image), image)
    assert cv2.imwrite(str(valid_image), image)
    training_manifest = tmp_path / "training.jsonl"
    development_manifest = tmp_path / "development.jsonl"
    _write_jsonl(
        training_manifest,
        [
            {
                "case_id": "train/page",
                "image": str(train_image),
                "characters": [{"bbox": [480, 480, 40, 40]}],
            }
        ],
    )
    _write_jsonl(
        development_manifest,
        [
            {
                "case_id": "valid/page",
                "image": str(valid_image),
                "characters": [{"bbox": [100, 100, 20, 30]}],
            }
        ],
    )

    metadata = rfdetr_dataset.materialize(
        training_manifest,
        development_manifest,
        tmp_path / "coco",
        tile_size=512,
        overlap=128,
    )
    train_coco = json.loads(
        (tmp_path / "coco" / "train" / "_annotations.coco.json").read_text()
    )
    valid_coco = json.loads(
        (tmp_path / "coco" / "valid" / "_annotations.coco.json").read_text()
    )

    assert metadata["counts"]["train"] == {"pages": 1, "tiles": 3, "boxes": 3}
    assert metadata["counts"]["valid"] == {"pages": 1, "tiles": 4, "boxes": 1}
    assert {
        image_record["source_case_id"] for image_record in train_coco["images"]
    } == {"train/page"}
    assert {
        image_record["source_case_id"] for image_record in valid_coco["images"]
    } == {"valid/page"}
    assert all(
        0 < annotation["bbox"][2] <= 40 and 0 < annotation["bbox"][3] <= 40
        for annotation in train_coco["annotations"]
    )


def test_rfdetr_dataset_rejects_page_leakage(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    _write_jsonl(
        manifest, [{"case_id": "same", "image": "missing.jpg", "characters": []}]
    )

    with pytest.raises(ValueError, match="leakage"):
        rfdetr_dataset.materialize(
            manifest,
            manifest,
            tmp_path / "coco",
            tile_size=512,
            overlap=96,
        )


def test_rfdetr_dataset_rejects_asset_hash_leakage(tmp_path: Path) -> None:
    training = tmp_path / "training.jsonl"
    development = tmp_path / "development.jsonl"
    shared_image_sha = "a" * 64
    _write_jsonl(
        training,
        [
            {
                "case_id": "train",
                "source_path": "train.jpg",
                "sha256": {"image": shared_image_sha},
                "image": "missing.jpg",
                "characters": [],
            }
        ],
    )
    _write_jsonl(
        development,
        [
            {
                "case_id": "development",
                "source_path": "development.jpg",
                "sha256": {"image": shared_image_sha},
                "image": "missing.jpg",
                "characters": [],
            }
        ],
    )

    with pytest.raises(ValueError, match="leakage by image"):
        rfdetr_dataset.materialize(
            training,
            development,
            tmp_path / "coco",
            tile_size=512,
            overlap=96,
        )


def test_kuzushiji_manifest_is_book_balanced_and_content_addressed(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "kuzushiji.zip"
    image_payload = cv2.imencode(".jpg", np.full((40, 50, 3), 255, dtype=np.uint8))[
        1
    ].tobytes()
    header = "Unicode,Image,X,Y,Block ID,Char ID,Width,Height\n"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for book_id in ("book-a", "book-b"):
            rows = header
            for index in (1, 2):
                page_id = f"{book_id}_{index:05d}"
                rows += f"U+4E00,{page_id},5,6,B0001,C0001,10,11\n"
                archive.writestr(f"{book_id}/images/{page_id}.jpg", image_payload)
            archive.writestr(f"{book_id}/{book_id}_coordinate.csv", rows)

    output = tmp_path / "dataset"
    metadata = kuzushiji_dataset.build_manifest(
        archive_path,
        output,
        page_count=2,
        seed=361004,
        expected_archive_sha256=kuzushiji_dataset.sha256_file(archive_path),
    )
    records = kuzushiji_dataset.read_jsonl(output / "manifests" / "training.jsonl")

    assert metadata["selection"]["books"] == 2
    assert {record["source_book_id"] for record in records} == {"book-a", "book-b"}
    assert all(record["characters"][0]["bbox"] == [5, 6, 10, 11] for record in records)
    assert all(Path(record["image"]).is_file() for record in records)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        kuzushiji_dataset.build_manifest(
            archive_path,
            output,
            page_count=2,
            seed=361004,
            expected_archive_sha256=None,
        )


def test_kuzushiji_composition_matches_control_tile_exposure(tmp_path: Path) -> None:
    def write_dataset(root: Path, train_cases: list[tuple[str, str]]) -> None:
        for split, cases in (
            ("train", train_cases),
            ("valid", []),
            ("test", []),
        ):
            split_root = root / split
            split_root.mkdir(parents=True)
            images = []
            annotations = []
            for image_id, (file_name, case_id) in enumerate(cases, start=1):
                assert cv2.imwrite(
                    str(split_root / file_name),
                    np.full((20, 20, 3), 255, dtype=np.uint8),
                )
                images.append(
                    {
                        "id": image_id,
                        "file_name": file_name,
                        "width": 20,
                        "height": 20,
                        "source_case_id": case_id,
                    }
                )
                annotations.append(
                    {
                        "id": image_id,
                        "image_id": image_id,
                        "category_id": 1,
                        "bbox": [1, 1, 5, 5],
                        "area": 25,
                        "iscrowd": 0,
                    }
                )
            (split_root / "_annotations.coco.json").write_text(
                json.dumps({"images": images, "annotations": annotations}),
                encoding="utf-8",
            )
        (root / "dataset.json").write_text("{}\n", encoding="utf-8")

    base = tmp_path / "base"
    control = tmp_path / "control"
    additional = tmp_path / "additional"
    write_dataset(base, [("base.png", "mthv2/TKH/base")])
    write_dataset(
        control,
        [
            ("base.png", "mthv2/TKH/base"),
            ("control-1.png", "mthv2/TKH/control-1"),
            ("control-2.png", "mthv2/TKH/control-2"),
        ],
    )
    write_dataset(
        additional,
        [
            ("jp-a1.png", "kuzushiji/book-a/page-1"),
            ("jp-a2.png", "kuzushiji/book-a/page-2"),
            ("jp-b1.png", "kuzushiji/book-b/page-1"),
        ],
    )

    output = tmp_path / "matched"
    metadata = kuzushiji_dataset.compose_matched_dataset(
        base, control, additional, output, seed=361004
    )
    train = kuzushiji_dataset.read_coco(output / "train" / "_annotations.coco.json")

    assert metadata["selection"]["added_kuzushiji_tiles"] == 2
    assert metadata["counts"]["train"]["tiles"] == 3
    assert len(train["images"]) == 3
    assert {image["source_case_id"].split("/")[1] for image in train["images"][1:]} == {
        "book-a",
        "book-b",
    }
    assert train["licenses"][0]["name"] == "CC BY-SA 4.0"


def test_rfdetr_training_freezes_seed_and_pretrained_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "dataset.json").write_text("{}\n", encoding="utf-8")
    pretrained = tmp_path / "rf-detr-nano.pth"
    pretrained.write_bytes(b"frozen weights")
    captured: dict[str, object] = {}

    class FakeRFDETRNano:
        def __init__(self, **kwargs: object) -> None:
            captured["model_kwargs"] = kwargs
            self.model_config = SimpleNamespace(pretrain_weights=str(pretrained))

        def train(self, **kwargs: object) -> None:
            captured["train_kwargs"] = kwargs

    fake_rfdetr = types.ModuleType("rfdetr")
    fake_rfdetr.RFDETRNano = FakeRFDETRNano
    monkeypatch.setitem(sys.modules, "rfdetr", fake_rfdetr)
    monkeypatch.setattr(
        rfdetr_candidate,
        "EXPECTED_PRETRAINED_SHA256",
        rfdetr_candidate.sha256_file(pretrained),
    )
    monkeypatch.setattr(
        rfdetr_candidate, "seed_training", lambda seed: captured.update(seed=seed)
    )
    monkeypatch.setattr(
        rfdetr_candidate, "runtime_versions", lambda: {"python": "test"}
    )
    monkeypatch.setattr(
        rfdetr_candidate,
        "hardware_identity",
        lambda device: {"requested_device": device},
    )
    output = tmp_path / "run"

    rfdetr_candidate.train(
        dataset,
        output,
        epochs=1,
        device="cuda",
        seed=361004,
        candidate_id="rf_detr/brightness-contrast-v1",
        lr_drop=3,
        brightness_contrast=True,
    )

    identity = json.loads((output / "experiment_identity.json").read_text())
    assert captured["seed"] == 361004
    assert captured["train_kwargs"]["seed"] == 361004
    assert identity["candidate_id"] == "rf_detr/brightness-contrast-v1"
    assert identity["pretrained_sha256"] == rfdetr_candidate.sha256_file(pretrained)
    assert identity["training"]["early_stopping"] is False
    assert identity["hardware"]["requested_device"] == "cuda"
    assert identity["training"]["epochs"] == 1
    assert identity["training"]["lr_drop"] == 3
    assert identity["training"]["aug_config"] == {
        "RandomBrightnessContrast": {
            "brightness_limit": 0.1,
            "contrast_limit": 0.1,
            "p": 0.3,
        }
    }
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        rfdetr_candidate.train(
            dataset,
            output,
            epochs=5,
            device="cuda",
            seed=361004,
            candidate_id="rf_detr/seeded-control-v1",
        )


def test_rfdetr_non_maximum_suppression_removes_tile_duplicates() -> None:
    boxes = np.asarray(
        [[10, 10, 30, 30], [11, 11, 31, 31], [50, 50, 70, 70]], dtype=np.float32
    )
    scores = np.asarray([0.8, 0.9, 0.7], dtype=np.float32)

    kept = rfdetr_candidate.non_maximum_suppression(boxes, scores, 0.4)

    assert kept == [1, 2]


def test_localization_audit_renders_worst_page_evidence(tmp_path: Path) -> None:
    image_path = tmp_path / "page.jpg"
    assert cv2.imwrite(str(image_path), np.full((100, 80, 3), 255, np.uint8))
    manifest = tmp_path / "manifest.jsonl"
    baseline = tmp_path / "baseline.jsonl"
    challenger = tmp_path / "challenger.jsonl"
    report = tmp_path / "report.json"
    _write_jsonl(
        manifest,
        [
            {
                "case_id": "page",
                "image": str(image_path),
                "characters": [{"bbox": [5, 5, 20, 20]}],
            }
        ],
    )
    _write_jsonl(
        baseline,
        [{"case_id": "page", "characters": [{"bbox": [5, 5, 20, 20]}]}],
    )
    _write_jsonl(
        challenger,
        [{"case_id": "page", "characters": [{"bbox": [8, 8, 20, 20]}]}],
    )
    report.write_text(
        json.dumps(
            {
                "per_case": [
                    {
                        "case_id": "page",
                        "detection": {"precision": 0.5, "recall": 0.5, "f1": 0.5},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    audit = localization_audit.render_audit(
        manifest, baseline, challenger, report, tmp_path / "audit", count=1
    )

    sheet = cv2.imread(str(tmp_path / "audit" / "worst-pages.png"))
    assert audit["cases"][0]["case_id"] == "page"
    assert sheet is not None and sheet.shape[1] == 3 * localization_audit.PANEL_WIDTH


def test_make_align_suite_builds_balanced_gold_and_normalizes_compatibility_glyphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(make_align_suite, "REPOSITORY_ROOT", tmp_path)
    records = []
    for corpus, expected, annotated in (
        ("TKH", "甲乙", "甲乙"),
        ("MTH1200", "復", "復"),
    ):
        image_path = tmp_path / "assets" / corpus / "page.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(image_path), np.full((100, 100, 3), 255, np.uint8))
        characters = [
            {"text": character, "bbox": [65.0, 20.0 + index * 30, 10.0, 10.0]}
            for index, character in enumerate(annotated)
        ]
        records.append(
            {
                "case_id": f"mthv2/{corpus}/page",
                "image": image_path.relative_to(tmp_path).as_posix(),
                "sha256": {"image": make_align_suite._sha256(image_path)},
                "strata": [corpus, "jpg"],
                "text": expected,
                "text_lines": [
                    {
                        "text": expected,
                        "polygon": [60.0, 10.0, 80.0, 10.0, 80.0, 80.0, 60.0, 80.0],
                    }
                ],
                "characters": characters,
            }
        )
    manifest = tmp_path / "development.jsonl"
    _write_jsonl(manifest, records)

    suite_path = make_align_suite.build(
        manifest,
        tmp_path / "evaluation",
        per_corpus=1,
    )

    cases = [
        json.loads(line)
        for line in (
            tmp_path / "evaluation" / "cases" / "align" / "mthv2-development-v3.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    compatibility_case = next(case for case in cases if "MTH1200" in case["strata"])
    gold = json.loads(
        (tmp_path / compatibility_case["references"]["metric_gold"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    suite = make_align_suite.yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    assert suite["id"] == "align/mthv2-development-v3"
    assert set(suite["primary_metrics"]) == {
        "align_character_box_precision",
        "align_character_box_recall",
        "align_coordinate_error",
    }
    assert suite["hard_limits"]["align_column_order_accuracy"] == {"minimum": 1.0}
    assert len(cases) == 2
    assert [character["ch"] for character in gold["columns"][0]["chars"]] == ["復"]
    transcription = json.loads(
        (
            tmp_path / compatibility_case["inputs"]["page_transcription"]["path"]
        ).read_text(encoding="utf-8")
    )
    assert transcription["route"] == "segmented"
    assert transcription["regions"] == [
        {
            "region_id": "line-0000",
            "kind": "text",
            "bbox": [60.0, 10.0, 20.0, 70.0],
            "text": "復",
        }
    ]

    qualification_records = [{**record, "split": "qualification"} for record in records]
    qualification_manifest = tmp_path / "qualification.jsonl"
    _write_jsonl(qualification_manifest, qualification_records)
    qualification_path = make_align_suite.build(
        qualification_manifest,
        tmp_path / "qualification-evaluation",
        per_corpus=1,
        suite_id="align/mthv2-test-qualification-v1",
        qualification_eligible=True,
    )
    qualification_suite = make_align_suite.yaml.safe_load(
        qualification_path.read_text(encoding="utf-8")
    )
    qualification_cases = [
        json.loads(line)
        for line in (
            tmp_path
            / "qualification-evaluation"
            / "cases"
            / "align"
            / "mthv2-test-qualification-v1.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert qualification_suite["qualification_eligible"] is True
    assert qualification_suite["primary_metrics"]["align_character_box_precision"] == {
        "direction": "maximize",
        "minimum_effect": 0.1,
        "confidence": 0.95,
    }
    assert qualification_suite["hard_limits"]["align_character_box_precision"] == {
        "minimum": 0.85
    }
    assert qualification_suite["operational_limits"] == {}
    assert qualification_suite["promotion"]["paired_bootstrap_samples"] == 10000
    assert {case["strata"][1] for case in qualification_cases} == {
        "mthv2_qualification"
    }
