"""Cell worker entry point: ``python -m palimpsest.factory.cellmain``.

Reads one CellSpec as JSON on stdin, executes it, prints the CellOutcome as
JSON on stdout. On failure prints ``{"kind", "message"}`` and exits 1.
The worker's whole world is the spec — it never sees the ledger, the
recipe, or any other cell.
"""

from __future__ import annotations

import json
import sys

from palimpsest.factory.core.cell import CellSpec, execute_cell


def main() -> int:
    spec = CellSpec.from_json(sys.stdin.read())
    try:
        outcome = execute_cell(spec)
    except Exception as error:  # report structured failure to the conductor
        print(
            json.dumps(
                {
                    "kind": type(error).__name__.lower(),
                    "message": str(error),
                }
            )
        )
        return 1
    print(outcome.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
