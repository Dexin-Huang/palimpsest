#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Show transcription run status")
    parser.add_argument(
        "--status",
        required=True,
        help="Path to status.json (e.g., exports/transcriptions_full/_runs/status.json)",
    )
    args = parser.parse_args()

    status_path = Path(args.status)
    if not status_path.exists():
        raise SystemExit(f"Status file not found: {status_path}")

    status = json.loads(status_path.read_text(encoding="utf-8"))

    print(f"Updated: {status.get('generated_at')}")
    print(f"Expected: {status.get('expected_total')}")
    print(f"Pass1: {status.get('pass1_total')}")
    print(f"Final: {status.get('final_total')}")
    print(f"Missing pass1: {len(status.get('missing_pass1', []))}")
    print(f"Missing pass2: {len(status.get('missing_pass2', []))}")

    if status.get("missing_pass2"):
        sample = status["missing_pass2"][:10]
        print("Missing pass2 sample:")
        for item in sample:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
