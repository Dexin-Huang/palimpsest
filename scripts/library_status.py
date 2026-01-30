#!/usr/bin/env python3
"""Quick status summary for library documents."""

import argparse
import json
from collections import Counter
from pathlib import Path

from palimpsest.library.config import LIBRARY_ROOT


def load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize library status")
    parser.add_argument("--library-root", default=str(LIBRARY_ROOT), help="Library root path")
    parser.add_argument("--status", help="Filter by status")
    parser.add_argument("--doc-id", help="Show a single document entry")
    parser.add_argument("--list", action="store_true", help="List doc_id and status")
    parser.add_argument("--limit", type=int, default=50, help="Max rows when listing")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    registry_path = Path(args.library_root) / "index.jsonl"
    entries = load_registry(registry_path)
    if args.doc_id:
        for entry in entries:
            if entry.get("doc_id") == args.doc_id:
                print(json.dumps(entry, indent=2))
                return
        raise SystemExit(f"doc_id not found: {args.doc_id}")

    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]

    counts = Counter(e.get("status", "unknown") for e in entries)
    total = sum(counts.values())
    print(f"Total: {total}")
    for status, count in counts.most_common():
        print(f"{status}: {count}")

    if args.list:
        for entry in entries[: args.limit]:
            print(f"{entry.get('doc_id','')}  {entry.get('status','')}")


if __name__ == "__main__":
    main()
