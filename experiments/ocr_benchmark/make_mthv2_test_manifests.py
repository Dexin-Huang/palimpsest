"""Build candidate-blind and scorer-only manifests for the official MTHv2 test split."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import fetch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TEST_INDEX_SHA256 = "8990155fde71576d9d5269f0066bb54f204fe4c855fe8532b257c1619a28aa08"


def repository_relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


def write_jsonl(path: Path, records: list[dict[str, object]]) -> str:
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return hashlib.sha256(payload.encode()).hexdigest()


def build(root: Path, output: Path) -> None:
    source_paths, index_payload = fetch.fetch_index("test")
    index_sha256 = hashlib.sha256(index_payload).hexdigest()
    if index_sha256 != EXPECTED_TEST_INDEX_SHA256:
        raise RuntimeError(
            f"official test index changed: expected {EXPECTED_TEST_INDEX_SHA256}, "
            f"received {index_sha256}"
        )

    inputs: list[dict[str, object]] = []
    gold: list[dict[str, object]] = []
    annotation_exclusions: list[dict[str, str]] = []
    for rank, source_path in enumerate(source_paths, start=1):
        image_member = fetch.archive_image_path(source_path)
        image_path = fetch.local_member_path(root, image_member)
        if not image_path.is_file():
            raise FileNotFoundError(f"missing acquired test image: {image_path}")
        corpus = image_member.split("/", 1)[0]
        stem = Path(image_member).stem
        case = {
            "schema_version": 1,
            "case_id": f"mthv2/{corpus}/{stem}",
            "dataset": "MTHv2",
            "split": "official_test",
            "selection_rank": rank,
            "strata": [corpus, image_path.suffix.lower().lstrip(".")],
            "image": repository_relative(image_path),
            "source_path": source_path,
            "sha256": {"image": fetch.sha256_file(image_path)},
        }
        inputs.append(case)

        char_member = fetch.related_members(image_member)[2]
        char_path = fetch.local_member_path(root, char_member)
        try:
            characters = fetch.parse_characters(
                char_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, ValueError) as error:
            annotation_exclusions.append(
                {
                    "case_id": str(case["case_id"]),
                    "source_path": source_path,
                    "reason": str(error),
                }
            )
            continue
        gold.append(
            {
                **case,
                "characters": characters,
                "sha256": {
                    **case["sha256"],
                    "characters": fetch.sha256_file(char_path),
                },
            }
        )

    input_sha256 = write_jsonl(output / "inputs.jsonl", inputs)
    gold_sha256 = write_jsonl(output / "gold.jsonl", gold)
    scorable_ids = {str(case["case_id"]) for case in gold}
    scorable_inputs = [case for case in inputs if str(case["case_id"]) in scorable_ids]
    scorable_input_sha256 = write_jsonl(
        output / "scorable-inputs.jsonl", scorable_inputs
    )
    metadata = {
        "schema_version": 1,
        "dataset": "MTHv2",
        "split": "official_test",
        "source_repository": "https://github.com/HCIILAB/MTHv2_Datasets_Release",
        "source_commit": fetch.MTHV2_COMMIT,
        "test_index_sha256": index_sha256,
        "candidate_contract": {
            "manifest": "inputs.jsonl",
            "fields": sorted(inputs[0]),
            "contains_transcription": False,
            "contains_gold_boxes": False,
        },
        "scorer_contract": {
            "manifest": "gold.jsonl",
            "fields": sorted(gold[0]),
        },
        "manifest_sha256": {
            "inputs": input_sha256,
            "scorable_inputs": scorable_input_sha256,
            "gold": gold_sha256,
        },
        "counts": {
            "official_test_pages": len(inputs),
            "scorable_pages": len(gold),
            "official_test_by_corpus": Counter(
                str(case["strata"][0]) for case in inputs
            ),
            "scorable_by_corpus": Counter(str(case["strata"][0]) for case in gold),
            "gold_character_boxes": sum(len(case["characters"]) for case in gold),
        },
        "annotation_exclusions": annotation_exclusions,
    }
    (output / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build(args.root.resolve(), args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
