from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from palimpsest.factory.evaluation.inline_extension import render_candidate

_DEFAULT_MODEL = "openai-codex/gpt-5.6-luna"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an immutable Candidate from exact OMP extension bytes."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--role", choices=("baseline", "challenger"), required=True)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rendered = render_candidate(
        args.source.read_bytes(),
        role=args.role,
        model=args.model,
        output_dir=args.output_dir,
    )
    print(rendered.candidate_path.resolve())


if __name__ == "__main__":
    main()
