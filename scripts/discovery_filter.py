#!/usr/bin/env python3
"""Filter discovery registries into an appended JSONL list."""

import argparse
import json
import re
from pathlib import Path


def parse_range(value: str) -> tuple[int, int]:
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError("range must be like 1000-1200")
    return int(parts[0]), int(parts[1])


def load_records(paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def score(record: dict) -> float:
    return (
        record.get("discovery", {}).get("wtf_factor")
        or record.get("scholarship", {}).get("obscurity_score")
        or 0
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter discovery registries")
    parser.add_argument(
        "--input-dir", default="discovery/registry", help="Directory with JSONL registries"
    )
    parser.add_argument("--collection", help="Shelfmark prefix (e.g., Pal.lat)")
    parser.add_argument("--range", dest="range_", help="Numeric range like 1000-1200")
    parser.add_argument("--min-score", type=float, default=0)
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--append", action="store_true", help="Append new records")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    paths = sorted(input_dir.glob("*.jsonl"))
    records = load_records(paths)

    by_id: dict[str, dict] = {}
    for record in records:
        mid = record.get("manuscript_id") or record.get("id")
        if mid:
            by_id[mid] = record

    filtered = list(by_id.values())

    if args.collection:
        prefix = args.collection
        pattern = re.compile(rf"{re.escape(prefix)}\.(\d+)", re.I)
        filtered = [
            r for r in filtered if pattern.search(r.get("shelfmark", "")) is not None
        ]

    if args.range_:
        start, end = parse_range(args.range_)
        pattern = re.compile(r"(\d+)")
        subset = []
        for record in filtered:
            shelf = record.get("shelfmark", "")
            m = pattern.search(shelf)
            if not m:
                continue
            num = int(m.group(1))
            if start <= num <= end:
                subset.append(record)
        filtered = subset

    if args.min_score:
        filtered = [r for r in filtered if score(r) >= args.min_score]

    output = Path(args.output)
    existing = set()
    if args.append and output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("manuscript_id"))
            except json.JSONDecodeError:
                continue

    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    written = 0
    with output.open(mode, encoding="utf-8") as handle:
        for record in filtered:
            mid = record.get("manuscript_id")
            if args.append and mid in existing:
                continue
            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")
            written += 1

    print(f"Wrote {written} records to {output}")


if __name__ == "__main__":
    main()
