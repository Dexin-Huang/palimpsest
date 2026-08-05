"""Build the Kuzushiji development transcribe suite from the staged selection.

The CODH Kuzushiji v2 release annotates every character with a bounding box
and Unicode codepoint but publishes no page-level reading text, so page gold
is DERIVED here: characters cluster into right-to-left vertical columns with
the same deterministic rules used across the geometry experiments, read top
to bottom within a column, one column per line. The adjudication method is
``derived_reading_order`` and the suite is development-only: its CER mixes
recognition and ordering error by construction, which is exactly the
difficulty this cursive gate exists to measure.

One page per book, 24 books in deterministic order, images copied
content-verified into the evaluation assets tree so the suite is
self-contained under the default asset root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from geometry_columns import split_columns, split_registers
from make_ancientdoc_transcribe_suite import read_jsonl, sha256_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = REPOSITORY_ROOT / "palimpsest" / "factory" / "evaluation"
SELECTION = (
    REPOSITORY_ROOT / "scratch/ocr_benchmark/kuzushiji-v2/selection600-seed361004-v1"
)
SUITE_ID = "transcribe/kuzushiji-development/v1"
SUITE_PATH = EVALUATION_ROOT / "suites/transcribe/kuzushiji-development-v1.yaml"
CASES_PATH = EVALUATION_ROOT / "cases/transcribe/kuzushiji-development-v1.jsonl"
GOLD_ROOT = EVALUATION_ROOT / "gold/transcribe/kuzushiji-development"
ASSET_ROOT = EVALUATION_ROOT / "assets/transcribe/kuzushiji-development"
BOOKS = 24
ATTRIBUTION = (
    "Japanese Kuzushiji Dataset (National Institute of Japanese Literature and "
    "other holding institutions / processed by CODH), doi:10.20676/00000340, "
    "CC BY-SA 4.0"
)


def derived_text(characters: list[dict[str, object]]) -> str:
    boxes = []
    for character in characters:
        x, y, w, h = (float(v) for v in character["bbox"])
        codepoint = str(character["text"])
        boxes.append(
            {"x": x, "y": y, "w": w, "h": h, "char": chr(int(codepoint[2:], 16))}
        )
    lines: list[str] = []
    for register in split_registers(boxes):
        for column in split_columns(register):
            lines.append("".join(box["char"] for box in column))
    return "\n".join(lines)


def build(check: bool) -> int:
    records = read_jsonl(SELECTION / "manifests/training.jsonl")
    by_book: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_book[str(record["source_book_id"])].append(record)
    chosen = [
        min(by_book[book], key=lambda record: str(record["case_id"]))
        for book in sorted(by_book)[:BOOKS]
    ]

    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    case_lines: list[str] = []
    for rank, record in enumerate(chosen, 1):
        book = str(record["source_book_id"])
        token = str(record["case_id"]).rsplit("/", 1)[-1]
        case_id = f"kuzushiji-{token}-transcribe-development"
        doc_id = f"kuzushiji_{book}"
        page_id = token
        image_source = REPOSITORY_ROOT / str(record["image"])
        image_sha256 = str(record["sha256"]["image"])
        if sha256_file(image_source) != image_sha256:
            raise ValueError(f"image hash mismatch: {record['case_id']}")
        staged = ASSET_ROOT / f"{token}.jpg"
        if not check:
            if not staged.exists() or sha256_file(staged) != image_sha256:
                shutil.copyfile(image_source, staged)
        if sha256_file(staged) != image_sha256:
            raise ValueError(f"staged image hash mismatch: {case_id}")

        characters = record["characters"]
        if not isinstance(characters, list) or not characters:
            raise ValueError(f"case has no characters: {case_id}")
        text = derived_text(characters)
        gold_payload = {
            "adjudication": {
                "method": "derived_reading_order",
                "notes": (
                    "Page text derived deterministically from the CODH "
                    "character annotations: right-to-left column clustering, "
                    "top-to-bottom within a column, one column per line. "
                    "Recognition gold is annotation-grade; ordering is "
                    "algorithmic, so CER mixes recognition and ordering "
                    "error by construction."
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
            "adjudication": {"method": "derived_reading_order", "version": 1},
            "case_id": case_id,
            "doc_id": doc_id,
            "inputs": {
                "page_image": {
                    "sha256": image_sha256,
                    "path": staged.relative_to(EVALUATION_ROOT).as_posix(),
                }
            },
            "license": ATTRIBUTION,
            "page_id": page_id,
            "pages": [
                {
                    "filename": f"{token}.jpg",
                    "height": int(record["height"]),
                    "label": str(rank),
                    "order": rank,
                    "page_id": page_id,
                    "url": f"https://codh.rois.ac.jp/char-shape/ :: {record['source_path']}",
                    "width": int(record["width"]),
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
                f"book-{book}",
                "kuzushiji",
                "cursive_japanese",
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
            "  Development-only cursive gate on 24 Kuzushiji pages, one per book.",
            "  Page text is derived from CODH character annotations by deterministic",
            "  right-to-left clustering. It is a positive partial reference that mixes",
            "  recognition and ordering evidence. Candidate additions are not penalized.",
            "  This suite cannot qualify promotion.",
            "case_manifest: transcribe/kuzushiji-development-v1.jsonl",
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
            "  - cursive_japanese",
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
