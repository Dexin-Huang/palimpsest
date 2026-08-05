"""Build the MTHv2 development transcribe suite from official annotations.

MTHv2's ``label_textline`` files are professional human transcriptions with
annotation-provided reading order (lines run right to left in file order),
so this is REAL page-level gold, not derived and not single-shot corrected.
The suite stays development-only because the annotations are still a single
published source without our double-transcription adjudication, but the
adjudication method is honestly ``official_annotation`` and the invented
allowance is print-perfect.

Eight pages per corpus (MTH1000, MTH1200, TKH) in deterministic case order;
images copied content-verified into the evaluation assets tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from make_ancientdoc_transcribe_suite import read_jsonl, sha256_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = REPOSITORY_ROOT / "palimpsest" / "factory" / "evaluation"
TEST_ROOT = REPOSITORY_ROOT / "scratch/ocr_benchmark/mthv2/test-800-image-only-v1"
SUITE_ID = "transcribe/mthv2-development/v1"
SUITE_PATH = EVALUATION_ROOT / "suites/transcribe/mthv2-development-v1.yaml"
CASES_PATH = EVALUATION_ROOT / "cases/transcribe/mthv2-development-v1.jsonl"
GOLD_ROOT = EVALUATION_ROOT / "gold/transcribe/mthv2-development"
ASSET_ROOT = EVALUATION_ROOT / "assets/transcribe/mthv2-development"
PER_CORPUS = 8
LICENSE = (
    "MTHv2 (SCUT HCII Lab); manually annotated dataset released for "
    "academic and non-commercial research"
)


def gold_text(corpus: str, stem: str) -> str:
    label_path = TEST_ROOT / "assets" / corpus / "label_textline" / f"{stem}.txt"
    lines: list[str] = []
    for raw in label_path.read_text(encoding="utf-8-sig").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        text = raw.split(",", 1)[0].strip()
        if text:
            lines.append(text)
    if not lines:
        raise ValueError(f"empty textline gold: {corpus}/{stem}")
    return "\n".join(lines)


def build(check: bool) -> int:
    records = read_jsonl(TEST_ROOT / "localization/gold.jsonl")
    by_corpus: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_corpus[str(record["strata"][0])].append(record)
    chosen: list[dict[str, object]] = []
    for corpus in sorted(by_corpus):
        chosen.extend(
            sorted(by_corpus[corpus], key=lambda record: str(record["case_id"]))[
                :PER_CORPUS
            ]
        )

    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    case_lines: list[str] = []
    for rank, record in enumerate(chosen, 1):
        corpus = str(record["strata"][0])
        stem = str(record["case_id"]).rsplit("/", 1)[-1]
        token = f"{corpus.lower()}-{stem}".replace("_", "-")
        case_id = f"mthv2-{token}-transcribe-development"
        doc_id = "_".join(
            part
            for part in f"mthv2_{corpus}_{stem}".lower().replace("-", "_").split("_")
            if part
        )
        page_id = stem.lower().replace("_", "-")
        image_source = REPOSITORY_ROOT / str(record["image"])
        image_sha256 = str(record["sha256"]["image"])
        if sha256_file(image_source) != image_sha256:
            raise ValueError(f"image hash mismatch: {record['case_id']}")
        suffix = image_source.suffix.lower()
        staged = ASSET_ROOT / f"{token}{suffix}"
        if not check:
            if not staged.exists() or sha256_file(staged) != image_sha256:
                shutil.copyfile(image_source, staged)
        if sha256_file(staged) != image_sha256:
            raise ValueError(f"staged image hash mismatch: {case_id}")

        text = gold_text(corpus, stem)
        gold_payload = {
            "adjudication": {
                "method": "official_annotation",
                "notes": (
                    "MTHv2 official manually annotated text lines joined in "
                    "annotation order (right-to-left reading). A single "
                    "published human source without independent double "
                    "transcription, so development-only despite real gold."
                ),
                "qualification_status": "development_non_qualifying",
                "version": 1,
            },
            "doc_id": doc_id,
            "page_id": page_id,
            "schema_version": 1,
            "text": text,
        }
        gold_bytes = (
            json.dumps(
                gold_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        gold_path = GOLD_ROOT / f"{token}.json"
        if check:
            if not gold_path.exists() or gold_path.read_bytes() != gold_bytes:
                raise SystemExit(f"gold drift: {gold_path}")
        else:
            gold_path.write_bytes(gold_bytes)

        case = {
            "adjudication": {"method": "official_annotation", "version": 1},
            "case_id": case_id,
            "doc_id": doc_id,
            "inputs": {
                "page_image": {
                    "sha256": image_sha256,
                    "path": staged.relative_to(EVALUATION_ROOT).as_posix(),
                }
            },
            "license": LICENSE,
            "page_id": page_id,
            "pages": [
                {
                    "filename": staged.name,
                    "height": 0,
                    "label": str(rank),
                    "order": rank,
                    "page_id": page_id,
                    "url": f"https://github.com/HCIILAB/MTHv2 :: {record['source_path']}",
                    "width": 0,
                }
            ],
            "references": {
                "transcription": {
                    "sha256": hashlib.sha256(gold_bytes).hexdigest(),
                    "path": gold_path.relative_to(EVALUATION_ROOT).as_posix(),
                }
            },
            "schema_version": 1,
            "strata": [
                corpus.lower(),
                "printed_canon",
                "classical_chinese",
                "vertical_script",
                "official_annotation",
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
            "  Development gate on 24 MTHv2 official-test pages, eight per corpus.",
            "  The dataset's annotated text lines are positive partial references.",
            "  Candidate text absent from gold is not penalized by string alignment.",
            "  The invention metric is diagnostic with a non-penalizing maximum.",
            "  Contamination, repetition, and empty output remain hard failures.",
            "  This suite cannot qualify promotion.",
            "case_manifest: transcribe/mthv2-development-v1.jsonl",
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
            "  - mth1000",
            "  - mth1200",
            "  - tkh",
            "slice_policy:",
            "  minimum_cases: 1",
            "  maximum_regression: 0.1",
            "operational_limits: {}",
            "judges: []",
            "downstream_probes: []",
            "promotion:",
            "  minimum_completed_cases: 24",
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
