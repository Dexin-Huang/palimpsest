"""Build the AncientDoc development transcribe suite from frozen manifests.

Deterministically renders the 28 AncientDoc development cases into the
factory evaluation layout: per-case gold references under
``palimpsest/factory/evaluation/gold/transcribe/ancientdoc-development/``,
a case manifest, and a suite specification. Images stay at their existing
content-verified locations under ``scratch/``; cases reference them by
repository-relative path, so ``bench verify --asset-root .`` checks every
hash without duplicating binaries.

Scope flags come from the read-scope-selection analysis: each volume's gold
either includes small-character text (``scope_full``) or excludes it
(``scope_primary``). The flag rides in every case's strata and both flags
are protected slices, so scope-driven regressions stay visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = REPOSITORY_ROOT / "palimpsest" / "factory" / "evaluation"
INPUTS = (
    REPOSITORY_ROOT
    / "scratch/ocr_benchmark/ancientdoc/v1/manifests/development-inputs.jsonl"
)
GOLD = (
    REPOSITORY_ROOT
    / "scratch/ocr_benchmark/ancientdoc/v1/manifests/development-gold.jsonl"
)
SCOPE_REPORT = (
    REPOSITORY_ROOT
    / "scratch/ocr_benchmark/runs/scope-selection-consensus-grain-v1/report.json"
)
SUITE_ID = "transcribe/ancientdoc-development/v1"
SUITE_PATH = EVALUATION_ROOT / "suites/transcribe/ancientdoc-development-v1.yaml"
CASES_PATH = EVALUATION_ROOT / "cases/transcribe/ancientdoc-development-v1.jsonl"
DATASET_REVISION = "149c447ebff66792cee28e02000682820858f17b"
GOLD_ROOT = EVALUATION_ROOT / "gold/transcribe/ancientdoc-development"
DATASET_URL = "https://huggingface.co/datasets/ByteDance/AncientDoc"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(check: bool) -> int:
    inputs = read_jsonl(INPUTS)
    gold_rows = {str(row["case_id"]): row for row in read_jsonl(GOLD)}
    scope_report = json.loads(SCOPE_REPORT.read_text(encoding="utf-8"))
    book_scopes = scope_report["scope_selection"]["book_scopes"]

    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    case_lines: list[str] = []
    category_names = {
        "传记类": "cat-zhuanji",
        "儒家类": "cat-rujia",
        "兵家类": "cat-bingjia",
        "别集类": "cat-bieji",
        "医家类": "cat-yijia",
        "天文算法类": "cat-tianwen-suanfa",
        "小说家类": "cat-xiaoshuojia",
        "总集类": "cat-zongji",
        "杂家类": "cat-zajia",
        "楚辞类": "cat-chuci",
        "类书类": "cat-leishu",
        "艺术类": "cat-yishu",
        "诗文评类": "cat-shiwenping",
        "谱录类": "cat-pulu",
    }

    for record in inputs:
        source_case_id = str(record["case_id"])
        gold_row = gold_rows[source_case_id]
        category, book = (str(value) for value in record["strata"])
        category_slice = category_names[category]
        book_slice = "book-" + hashlib.sha256(book.encode("utf-8")).hexdigest()[:8]
        scope = f"scope_{book_scopes[book]['scope']}"
        token = source_case_id.split("/", 1)[1]
        case_id = f"ancientdoc-{token}-transcribe-development"
        doc_id = f"ancientdoc_{token}"
        page_id = f"p{int(record['selection_rank']):02d}"

        image_relative = str(record["image"]).replace("\\", "/")
        image_path = REPOSITORY_ROOT / image_relative
        image_sha256 = str(record["sha256"]["image"])
        if sha256_file(image_path) != image_sha256:
            raise ValueError(f"image hash mismatch: {source_case_id}")
        image = cv2.imdecode(
            np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            raise ValueError(f"cannot decode image: {source_case_id}")
        height, width = image.shape[:2]

        gold_payload = {
            "adjudication": {
                "method": "dataset_reference",
                "notes": (
                    "ByteDance/AncientDoc dataset transcription used as-is; "
                    "gold scope varies per volume (measured 2026-07-28: this "
                    f"volume is {scope}), and the text is not independently "
                    "double-transcribed."
                ),
                "qualification_status": "development_non_qualifying",
                "version": 1,
            },
            "doc_id": doc_id,
            "page_id": page_id,
            "schema_version": 1,
            "text": str(gold_row["text"]),
        }
        gold_bytes = (
            json.dumps(
                gold_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        gold_path = GOLD_ROOT / f"{token}.json"
        if check:
            if not gold_path.exists() or gold_path.read_bytes() != gold_bytes:
                raise SystemExit(f"gold drift: {gold_path}")
        else:
            gold_path.write_bytes(gold_bytes)
        gold_relative = gold_path.relative_to(EVALUATION_ROOT).as_posix()

        case = {
            "adjudication": {"method": "dataset_reference", "version": 1},
            "case_id": case_id,
            "doc_id": doc_id,
            "inputs": {
                "page_image": {
                    "sha256": image_sha256,
                    "source": (
                        "https://huggingface.co/datasets/ByteDance/AncientDoc/"
                        f"resolve/{DATASET_REVISION}/"
                        + quote(str(record["source_path"]), safe="/")
                    ),
                }
            },
            "license": "CC0-1.0 (ByteDance/AncientDoc)",
            "page_id": page_id,
            "pages": [
                {
                    "filename": image_path.name,
                    "height": height,
                    "label": str(record["selection_rank"]),
                    "order": int(record["selection_rank"]),
                    "page_id": page_id,
                    "url": f"{DATASET_URL}/blob/main/{quote(str(record['source_path']), safe='/')}",
                    "width": width,
                }
            ],
            "references": {
                "transcription": {
                    "sha256": hashlib.sha256(gold_bytes).hexdigest(),
                    "path": gold_relative,
                }
            },
            "schema_version": 1,
            "strata": [
                category_slice,
                book_slice,
                scope,
                "classical_chinese",
                "vertical_script",
            ],
        }
        case_lines.append(
            json.dumps(
                case,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    cases_bytes = ("\n".join(case_lines) + "\n").encode("utf-8")
    suite_document = "\n".join(
        (
            "schema_version: 1",
            f"id: {json.dumps(SUITE_ID)}",
            "station: transcribe",
            "qualification_eligible: false",
            "mission: >-",
            "  Development-only comparison on 28 AncientDoc pages with dataset text.",
            "  Gold scope varies by volume and known omissions remain, so each record",
            "  is a positive partial reference. Candidate additions are not penalized.",
            "  The invention metric is diagnostic with a non-penalizing maximum.",
            "  This suite cannot qualify promotion.",
            "case_manifest: transcribe/ancientdoc-development-v1.jsonl",
            "primary_metrics:",
            "  partial_gold_character_error_rate:",
            "    direction: minimize",
            "    minimum_effect: 0.0",
            "    confidence: 0.8",
            "  page_completeness:",
            "    direction: maximize",
            "    minimum_effect: 0.0",
            "    confidence: 0.8",
            "hard_limits:",
            "  contamination_rate: {maximum: 0.0}",
            "  empty_output_rate: {maximum: 0.0}",
            "  invented_character_rate: {maximum: 1.0}",
            "  repetition_rate: {maximum: 0.1}",
            "protected_slices:",
            "  - scope_primary",
            "  - scope_full",
            "slice_policy:",
            "  minimum_cases: 1",
            "  maximum_regression: 0.1",
            "operational_limits: {}",
            "judges: []",
            "downstream_probes: []",
            "promotion:",
            "  minimum_completed_cases: 28",
            "  paired_bootstrap_samples: 1000",
            "  seed: 361004",
            "  require_all_hard_limits: true",
            "  require_all_downstream_probes: false",
            "",
        )
    ).encode("utf-8")

    if check:
        if CASES_PATH.read_bytes() != cases_bytes:
            raise SystemExit("case manifest drift")
        if SUITE_PATH.read_bytes() != suite_document:
            raise SystemExit("suite drift")
    else:
        CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CASES_PATH.write_bytes(cases_bytes)
        SUITE_PATH.write_bytes(suite_document)

    print(
        f"suite {SUITE_ID}: {len(case_lines)} cases, "
        f"cases sha256 {hashlib.sha256(cases_bytes).hexdigest()}, "
        f"suite sha256 {hashlib.sha256(suite_document).hexdigest()}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return build(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
