"""Executors: who runs a cell. The fleet dial.

The conductor decides WHAT runs (freshness, ordering, ledger); an executor
decides only HOW one fully-specified cell executes:

- ``inline``      — in the conductor's worker thread (fast, default)
- ``subprocess``  — a fresh Python process per cell: crash isolation, no
                    shared interpreter state, and the exact execution shape
                    an agent worker slots into later

Executors receive a ``CellSpec`` and return a ``CellOutcome``. They never
touch the ledger.
"""

from __future__ import annotations

import json
import subprocess
import sys

from palimpsest.factory.core.cell import CellOutcome, CellSpec, execute_cell

DEFAULT_TIMEOUT_SECONDS = 1800.0


class CellExecutionError(RuntimeError):
    """A cell failed in a worker; ``kind`` is the original exception type."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class InlineExecutor:
    def execute(self, spec: CellSpec) -> CellOutcome:
        return execute_cell(spec)


class SubprocessExecutor:
    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    def execute(self, spec: CellSpec) -> CellOutcome:
        completed = subprocess.run(
            [sys.executable, "-m", "palimpsest.factory.cellmain"],
            input=spec.to_json(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self._timeout,
        )
        if completed.returncode != 0:
            try:
                error = json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                error = {}
            raise CellExecutionError(
                kind=error.get("kind", "worker_crash"),
                message=error.get("message")
                or completed.stderr.strip()
                or f"cell worker exited {completed.returncode}",
            )
        return CellOutcome.from_json(completed.stdout)


EXECUTORS = {"inline": InlineExecutor, "subprocess": SubprocessExecutor}


def make(name: str):
    try:
        return EXECUTORS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown executor {name!r}; have {sorted(EXECUTORS)}"
        ) from None
