"""Freeze human-attested specimens and character-disjoint reconstruction cases.

The benchmark refuses silver sequence-alignment labels. Both adaptation examples
and held-out targets must come from the immutable human crop-attestation record.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from palimpsest.image_labeling import resolve_recorded_path, sha256


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "experiments" / "scribe_template_retrieval" / "out"
OUT = HERE / "out"
DATASET_PATH = OUT / "annotation_dataset.json"
GENERATION_PATH = SOURCE / "generation.json"
BUDGETS = (8, 16, 32, 64)
SEED = 3477


def crop_path(record: dict) -> Path:
    return resolve_recorded_path(record["crop_path"], DATASET_PATH.parent)


def quality(record: dict) -> tuple[float, str]:
    return (-float(record["cv_score"]), record["proposal_id"])


def representative_records(records: list[dict]) -> dict[str, dict]:
    by_character: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_character[record["attested_character"]].append(record)
    return {
        character: min(character_records, key=quality)
        for character, character_records in by_character.items()
    }


def specimen_order(records: list[dict]) -> list[dict]:
    """Return deterministic, quality-first representatives across columns."""

    representatives = representative_records(records)
    by_column: dict[int, list[dict]] = defaultdict(list)
    for record in representatives.values():
        by_column[int(record["column_index"])].append(record)
    for column_records in by_column.values():
        column_records.sort(key=quality)

    ordered: list[dict] = []
    while len(ordered) < len(representatives):
        added = False
        for column_index in sorted(by_column):
            if by_column[column_index]:
                ordered.append(by_column[column_index].pop(0))
                added = True
        if not added:
            break
    return ordered


def frozen_record(record: dict) -> dict:
    path = crop_path(record)
    if sha256(path) != record["crop_sha256"]:
        raise ValueError(f"Attested crop hash mismatch: {path}")
    return {
        "crop_id": record["proposal_id"],
        "character": record["attested_character"],
        "page_id": record["page_id"],
        "column_index": record["column_index"],
        "slot_index": record["slot_index"],
        "bbox": record["bbox"],
        "crop_path": path.relative_to(ROOT).as_posix(),
        "crop_sha256": record["crop_sha256"],
        "cv_score": record["cv_score"],
        "label_status": "human_attested_gold",
        "label_was_corrected": record["label_was_corrected"],
    }


def gold_records(annotation: dict, role: str) -> list[dict]:
    return [
        {
            **record["metadata"],
            "proposal_id": record["item_id"],
            "attested_character": record["label"],
            "bbox": record["bbox"],
            "crop_path": record["accepted_image_path"],
            "crop_sha256": record["accepted_image_sha256"],
            "label_was_corrected": record["label_was_overridden"],
        }
        for record in annotation["records"]
        if record["queue"] == role
    ]


def build_benchmark() -> dict:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            "Human image annotation is required before building a benchmark: "
            f"{DATASET_PATH}"
        )
    annotation = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if (
        annotation.get("schema_version") != 1
        or annotation.get("kind") != "human_image_annotation_dataset"
        or annotation.get("project_id") != "p3477-generative-hand-font-crops"
    ):
        raise ValueError("Annotation dataset has the wrong contract")
    if annotation.get("status") != "human_attested_gold" or not annotation.get(
        "dataset_ready"
    ):
        raise ValueError("Annotation dataset is not immutable human gold")

    project_path = resolve_recorded_path(
        annotation["project_path"], DATASET_PATH.parent
    )
    if sha256(project_path) != annotation["project_sha256"]:
        raise ValueError("Annotation project fingerprint does not match dataset")
    project = json.loads(project_path.read_text(encoding="utf-8"))
    metadata = project["metadata"]
    proposal_path = resolve_recorded_path(metadata["proposal_path"], ROOT)
    if sha256(proposal_path) != metadata["proposal_sha256"]:
        raise ValueError("Crop proposal fingerprint does not match annotation")

    generation = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    reference_records = gold_records(annotation, "writer_specimen")
    held_out_records = gold_records(annotation, "held_out_evaluation")
    ordered_specimen = specimen_order(reference_records)
    frozen: dict[str, dict] = {}

    def freeze(record: dict) -> dict:
        proposal_id = record["proposal_id"]
        if proposal_id not in frozen:
            frozen[proposal_id] = frozen_record(record)
        return frozen[proposal_id]

    budgets = tuple(budget for budget in BUDGETS if budget <= len(ordered_specimen))
    if not budgets:
        raise ValueError(
            f"Need at least {min(BUDGETS)} distinct specimen characters; "
            f"found {len(ordered_specimen)}"
        )

    reference_characters = {
        record["attested_character"] for record in reference_records
    }
    strict_unseen = [
        record
        for record in held_out_records
        if record["attested_character"] not in reference_characters
    ]
    if not strict_unseen:
        raise ValueError("Attested strict-unseen target slice is empty")

    specimens = {
        str(budget): [freeze(record) for record in ordered_specimen[:budget]]
        for budget in budgets
    }
    specimen_characters = {
        budget: {record["character"] for record in records}
        for budget, records in specimens.items()
    }
    for budget, characters in specimen_characters.items():
        if len(characters) != int(budget):
            raise ValueError(f"Specimen budget {budget} contains duplicate identities")

    strict_targets = [freeze(record) for record in strict_unseen]
    budget_targets = {
        budget: [
            freeze(record)
            for record in held_out_records
            if record["attested_character"] not in specimen_characters[budget]
        ]
        for budget in specimens
    }

    return {
        "schema_version": 2,
        "experiment": "generative-hand-font-v1",
        "purpose": "writer-identity reconstruction and installable font generation",
        "evidence_status": "human_attested_gold",
        "qualification_eligible": False,
        "generated_pixels_are_documentary_evidence": False,
        "seed": SEED,
        "source_records": {
            "annotation_dataset_path": DATASET_PATH.relative_to(ROOT).as_posix(),
            "annotation_dataset_sha256": sha256(DATASET_PATH),
            "annotation_project_path": project_path.relative_to(ROOT).as_posix(),
            "annotation_project_sha256": sha256(project_path),
            "proposal_path": metadata["proposal_path"],
            "proposal_sha256": metadata["proposal_sha256"],
            "source_manifest_sha256": metadata["source_manifest_sha256"],
            "generation_path": GENERATION_PATH.relative_to(ROOT).as_posix(),
            "generation_sha256": sha256(GENERATION_PATH),
            "reference_page": "page_0000",
            "held_out_page": "page_0001",
        },
        "canonical_content": {
            "font_path": generation["source_font"],
            "font_sha256": generation["source_font_sha256"],
            "role": "character identity and topology only",
        },
        "wrong_writer_control": {
            "font_path": generation["wrong_style_font"],
            "font_sha256": generation["wrong_style_font_sha256"],
        },
        "pretrained_baseline": {
            "implementation_url": generation["implementation_url"],
            "implementation_commit": generation["implementation_commit"],
            "checkpoint_sha256": generation["checkpoint_sha256"],
        },
        "specimen_budgets": specimens,
        "targets": {
            "strict_unseen_from_reference_page": strict_targets,
            "excluded_from_each_specimen_budget": budget_targets,
        },
        "output_repertoire": generation["candidate_characters"],
        "counts": {
            "reference_crops": len(reference_records),
            "reference_characters": len(reference_characters),
            "held_out_crops": len(held_out_records),
            "held_out_characters": len(
                {record["attested_character"] for record in held_out_records}
            ),
            "strict_unseen_crops": len(strict_targets),
            "strict_unseen_characters": len(
                {record["character"] for record in strict_targets}
            ),
            "output_repertoire": len(generation["candidate_characters"]),
        },
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    benchmark = build_benchmark()
    path = OUT / "benchmark.json"
    path.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(benchmark["counts"], ensure_ascii=False, indent=2))
    for budget, records in benchmark["specimen_budgets"].items():
        print(f"budget {budget}: {''.join(record['character'] for record in records)}")
    print(f"benchmark: {path}")


if __name__ == "__main__":
    main()
