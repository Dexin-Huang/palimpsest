"""Build a local non-qualifying align suite from frozen MTHv2 annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import cv2
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUITE_ID = "align/mthv2-development-v3"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, records: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for record in records
    )
    path.write_text(payload, encoding="utf-8", newline="\n")


def _read_manifest(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def _case_asset(
    path: Path,
    digest: str,
    *,
    asset_root: Path,
    object_root: Path | None,
) -> dict[str, str]:
    resolved = path.resolve()
    if resolved.is_relative_to(asset_root):
        return {
            "path": resolved.relative_to(asset_root).as_posix(),
            "sha256": digest,
        }
    if object_root is None:
        raise ValueError(f"asset is outside the declared root: {resolved}")
    destination = object_root / digest
    if destination.is_file():
        if _sha256(destination) != digest:
            raise ValueError(f"object-store hash mismatch: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".partial")
        shutil.copyfile(resolved, temporary)
        if _sha256(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"copied object hash mismatch: {resolved}")
        temporary.replace(destination)
    return {"sha256": digest}


def _select(
    records: list[dict[str, object]], per_corpus: int
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    counts: defaultdict[str, int] = defaultdict(int)
    for record in records:
        strata = record.get("strata")
        if not isinstance(strata, list) or not strata or not isinstance(strata[0], str):
            raise ValueError(f"case {record.get('case_id')!r} has no source corpus")
        corpus = strata[0]
        if counts[corpus] >= per_corpus:
            continue
        selected.append(record)
        counts[corpus] += 1
    if not selected or any(count != per_corpus for count in counts.values()):
        raise ValueError("manifest cannot supply the requested balanced corpus sample")
    return selected


def _line_characters(
    record: dict[str, object],
) -> list[tuple[dict[str, object], list[dict[str, object]]]]:
    text_lines = record.get("text_lines")
    characters = record.get("characters")
    if not isinstance(text_lines, list) or not isinstance(characters, list):
        raise ValueError(f"case {record.get('case_id')!r} has invalid annotations")
    result: list[tuple[dict[str, object], list[dict[str, object]]]] = []
    for line in text_lines:
        if not isinstance(line, dict):
            raise ValueError("text line must be an object")
        polygon = line.get("polygon")
        text = line.get("text")
        if (
            not isinstance(polygon, list)
            or len(polygon) != 8
            or not isinstance(text, str)
        ):
            raise ValueError("text line has invalid text or polygon")
        xs = [float(value) for value in polygon[0::2]]
        ys = [float(value) for value in polygon[1::2]]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        selected: list[dict[str, object]] = []
        for character in characters:
            if not isinstance(character, dict):
                raise ValueError("character annotation must be an object")
            bbox = character.get("bbox")
            glyph = character.get("text")
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not isinstance(glyph, str)
                or not glyph
            ):
                raise ValueError("character annotation has invalid text or bbox")
            x, y, width, height = (float(value) for value in bbox)
            center_x = x + width / 2
            center_y = y + height / 2
            if left <= center_x <= right and top <= center_y <= bottom:
                selected.append(character)
        selected.sort(key=lambda character: float(character["bbox"][1]))
        observed = "".join(str(character["text"]) for character in selected)
        if observed != text:
            if len(observed) != len(text) or unicodedata.normalize(
                "NFKC", observed
            ) != unicodedata.normalize("NFKC", text):
                raise ValueError(
                    f"case {record.get('case_id')!r} line annotation mismatch: "
                    f"expected {text!r}, got {observed!r}"
                )
            selected = [
                {**character, "text": expected}
                for character, expected in zip(selected, text, strict=True)
            ]
        result.append((line, selected))
    return result


def _regions(record: dict[str, object]) -> list[dict[str, object]]:
    regions: list[dict[str, object]] = []
    for index, (line, _) in enumerate(_line_characters(record)):
        polygon = line["polygon"]
        xs = [float(value) for value in polygon[0::2]]
        ys = [float(value) for value in polygon[1::2]]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        regions.append(
            {
                "region_id": f"line-{index:04d}",
                "kind": "text",
                "bbox": [left, top, right - left, bottom - top],
                "text": line["text"],
            }
        )
    return regions


def _gold(record: dict[str, object], image_path: Path) -> dict[str, object]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode source image: {image_path}")
    height, width = image.shape[:2]
    columns: list[dict[str, object]] = []
    for _, characters in _line_characters(record):
        boxes = [
            [float(value) for value in character["bbox"]] for character in characters
        ]
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[0] + box[2] for box in boxes)
        bottom = max(box[1] + box[3] for box in boxes)
        columns.append(
            {
                "bbox": [left, top, right - left, bottom - top],
                "chars": [
                    {
                        "ch": character["text"],
                        "bbox": [float(value) for value in character["bbox"]],
                        "confidence": 1.0,
                        "method": "official_annotation",
                    }
                    for character in characters
                ],
            }
        )
    return {
        "doc_id": "eval_align_mthv2",
        "page_id": Path(str(record["image"])).stem,
        "columns": columns,
        "image_size": [width, height],
        "image_sha256": str(record["sha256"]["image"]),
        "match_iou_threshold": 0.5,
    }


def build(
    manifest: Path,
    output: Path,
    *,
    per_corpus: int,
    suite_id: str = SUITE_ID,
    qualification_eligible: bool = False,
    asset_root: Path | None = None,
    object_root: Path | None = None,
) -> Path:
    output = output.resolve()
    if not output.is_relative_to(REPOSITORY_ROOT):
        raise ValueError("output must remain under the repository root")
    asset_root = (REPOSITORY_ROOT if asset_root is None else asset_root).resolve()
    object_root = object_root.resolve() if object_root is not None else None
    selected = _select(_read_manifest(manifest), per_corpus)
    station, separator, suite_name = suite_id.partition("/")
    if station != "align" or separator != "/" or not suite_name or "/" in suite_name:
        raise ValueError("suite_id must identify one align suite")
    partitions = {str(record.get("split", "development")) for record in selected}
    if qualification_eligible and partitions != {"qualification"}:
        raise ValueError("qualification suites require qualification source records")
    cases: list[dict[str, object]] = []
    for record in selected:
        case_id = str(record["case_id"])
        safe_name = case_id.replace("/", "-").replace("\\", "-")
        image_path = (REPOSITORY_ROOT / str(record["image"])).resolve()
        expected_image_sha256 = str(record["sha256"]["image"])
        if _sha256(image_path) != expected_image_sha256:
            raise ValueError(f"image hash mismatch for {case_id}")
        page_id = image_path.stem
        transcription_path = (
            output / "cases" / "align" / suite_name / f"{safe_name}.input.json"
        )
        gold_path = output / "gold" / "align" / suite_name / f"{safe_name}.json"
        _write_json(
            transcription_path,
            {
                "doc_id": "eval_align_mthv2",
                "page_id": page_id,
                "regions": _regions(record),
                "route": "segmented",
                "text": record["text"],
            },
        )
        _write_json(gold_path, _gold(record, image_path))
        corpus = str(record["strata"][0])
        cases.append(
            {
                "adjudication": {
                    "method": "official_mthv2_character_and_text_line_annotations",
                    "version": 2,
                },
                "case_id": f"align-{case_id}",
                "doc_id": "eval_align_mthv2",
                "inputs": {
                    "page_image_clean": _case_asset(
                        image_path,
                        expected_image_sha256,
                        asset_root=asset_root,
                        object_root=object_root,
                    ),
                    "page_transcription": _case_asset(
                        transcription_path,
                        _sha256(transcription_path),
                        asset_root=asset_root,
                        object_root=object_root,
                    ),
                },
                "license": "MTHv2 non-commercial academic research",
                "page_id": page_id,
                "pages": [
                    {
                        "order": 1,
                        "page_id": page_id,
                        "url": "https://github.com/HCIILAB/MTHv2_Datasets_Release",
                    }
                ],
                "references": {
                    "metric_gold": _case_asset(
                        gold_path,
                        _sha256(gold_path),
                        asset_root=asset_root,
                        object_root=object_root,
                    )
                },
                "schema_version": 1,
                "strata": [corpus, f"mthv2_{record.get('split', 'development')}"],
            }
        )

    case_manifest = output / "cases" / "align" / f"{suite_name}.jsonl"
    _write_jsonl(case_manifest, cases)
    suite_path = output / "suites" / "align" / f"{suite_name}.yaml"
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite = {
        "schema_version": 1,
        "id": suite_id,
        "station": "align",
        "qualification_eligible": qualification_eligible,
        "mission": (
            "Held-out qualification of transcription-to-character alignment on "
            "balanced, sealed MTHv2 test pages."
            if qualification_eligible
            else "Development comparison of transcription-to-character alignment "
            "on balanced, frozen MTHv2 pages."
        ),
        "case_manifest": f"align/{suite_name}.jsonl",
        "primary_metrics": {
            "align_character_box_precision": {
                "direction": "maximize",
                "minimum_effect": 0.1 if qualification_eligible else 0.0,
                "confidence": 0.95 if qualification_eligible else 0.8,
            },
            "align_character_box_recall": {
                "direction": "maximize",
                "minimum_effect": 0.1 if qualification_eligible else 0.0,
                "confidence": 0.95 if qualification_eligible else 0.8,
            },
            "align_coordinate_error": {
                "direction": "minimize",
                "minimum_effect": 0.01 if qualification_eligible else 0.0,
                "confidence": 0.95 if qualification_eligible else 0.8,
            },
        },
        "hard_limits": {
            "align_fabricated_coordinate_rate": {"maximum": 0.0},
            "align_line_association_accuracy": {"minimum": 1.0},
            "align_column_order_accuracy": {"minimum": 1.0},
            **(
                {
                    "align_character_box_precision": {"minimum": 0.85},
                    "align_character_box_recall": {"minimum": 0.85},
                    "align_coordinate_error": {"maximum": 0.05},
                }
                if qualification_eligible
                else {}
            ),
        },
        "protected_slices": sorted({str(record["strata"][0]) for record in selected}),
        "slice_policy": {
            "minimum_cases": per_corpus if qualification_eligible else 1,
            "maximum_regression": 0.02 if qualification_eligible else 0.05,
        },
        "operational_limits": {},
        "judges": [],
        "downstream_probes": [],
        "promotion": {
            "minimum_completed_cases": len(selected),
            "paired_bootstrap_samples": 10000 if qualification_eligible else 1000,
            "seed": 71926 if qualification_eligible else 361004,
            "require_all_hard_limits": True,
            "require_all_downstream_probes": False,
        },
    }
    suite_path.write_text(
        yaml.safe_dump(suite, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    return suite_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-corpus", type=int, default=1)
    parser.add_argument("--suite-id", default=SUITE_ID)
    parser.add_argument("--qualification-eligible", action="store_true")
    parser.add_argument("--asset-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--object-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.per_corpus <= 0:
        raise SystemExit("--per-corpus must be positive")
    suite_path = build(
        args.manifest.resolve(),
        args.out,
        per_corpus=args.per_corpus,
        suite_id=args.suite_id,
        qualification_eligible=args.qualification_eligible,
        asset_root=args.asset_root,
        object_root=args.object_root,
    )
    print(suite_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
