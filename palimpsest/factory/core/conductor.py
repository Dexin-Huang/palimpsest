"""The conductor: drives a work order through its recipe (FACTORY.md §2.4).

The only component that knows ordering, concurrency, and freshness. The unit
of work is one cell — (page × station) or (doc × manuscript-station) — and
every cell decision comes from the two fingerprints (FACTORY.md §2.6):

- missing            → run
- fresh              → skip (config and inputs both match the latest run)
- stale              → run  (inputs drifted: upstream was refreshed)
- outdated           → skip + report (config drifted: rerunning paid work is
                       explicit — pass ``refresh={station}`` to force it)

Page-grain stations run across pages on a thread pool. Manuscript-grain
stations are barriers in the recipe's explicit ordered line.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path

from palimpsest.factory import prompt_store
from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.core.artifact import (
    content_fingerprint,
    provenance_matches,
    read_provenance,
)
from palimpsest.factory.core.cell import CellSpec
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.executors import make as make_executor
from palimpsest.factory.core.ledger import Ledger, fingerprint
from palimpsest.factory.core.recipe import Recipe, StationSpec, load as load_recipe
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.usage import combine_cost
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import page_list_path

DEFAULT_WORKERS = 6
WORK_HEARTBEAT_SECONDS = 15
WORK_LEASE_SECONDS = 600


@dataclass
class CellReport:
    station: str
    page_id: str | None
    action: str  # ran | recovered | fresh | outdated | failed
    error: str | None = None
    cost_usd: float | None = None


@dataclass
class RunReport:
    doc_id: str
    recipe: str
    cells: list[CellReport] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for cell in self.cells if cell.action == action)

    @property
    def cost_usd(self) -> float | None:
        total: float | None = 0.0
        for cell in self.cells:
            if cell.action in {"ran", "failed"}:
                total = combine_cost(total, cell.cost_usd)
        return total


class Conductor:
    def __init__(
        self,
        ledger: Ledger,
        *,
        library_root: Path = LIBRARY_ROOT,
        workers: int = DEFAULT_WORKERS,
        refresh: frozenset[str] = frozenset(),
        executor: str = "inline",
    ) -> None:
        self._ledger = ledger
        self._ledger_lock = threading.Lock()
        self._library_root = library_root
        self._workers = workers
        self._refresh = refresh
        self._executor = make_executor(executor)

    # -- public ---------------------------------------------------------------

    def run(self, doc_id: str) -> RunReport:
        item = self._ledger.item(doc_id)
        if item is None:
            raise KeyError(
                f"No work order for {doc_id!r} — run "
                f"'palimpsest adopt --doc-id {doc_id} --recipe <name>' first"
            )

        stale_before = (
            datetime.now(timezone.utc) - timedelta(seconds=WORK_LEASE_SECONDS)
        ).isoformat()
        owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        with self._ledger_lock:
            self._ledger.reconcile_abandoned(doc_id, stale_before=stale_before)
            work_run_id = self._ledger.claim_work(doc_id, owner=owner)

        stop_heartbeat = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat = threading.Thread(
            target=self._heartbeat_claim,
            args=(work_run_id, stop_heartbeat, heartbeat_errors),
            name=f"palimpsest-heartbeat-{doc_id}",
            daemon=True,
        )
        heartbeat.start()
        work_status = "failed"
        work_error: str | None = None
        try:
            with self._ledger_lock:
                self._ledger.set_item_status(doc_id, "active")
            report = self._drive(doc_id, item, heartbeat_errors)
            if heartbeat_errors:
                raise RuntimeError("Work-order heartbeat failed") from heartbeat_errors[
                    0
                ]
            item_status = "failed" if report.count("failed") else "complete"
            with self._ledger_lock:
                self._ledger.set_item_status(doc_id, item_status)
            work_status = "failed" if item_status == "failed" else "done"
            if work_status == "failed":
                work_error = "one or more cells failed"
            return report
        except BaseException as error:
            work_error = str(error)
            with self._ledger_lock:
                self._ledger.set_item_status(doc_id, "failed")
            raise
        finally:
            stop_heartbeat.set()
            heartbeat.join()
            with self._ledger_lock:
                try:
                    self._ledger.finish_work(
                        work_run_id,
                        status=work_status,
                        error=work_error,
                    )
                except KeyError:
                    if work_error is None:
                        raise

    def _drive(
        self,
        doc_id: str,
        item: sqlite3.Row,
        heartbeat_errors: list[Exception],
    ) -> RunReport:
        recipe = load_recipe(item["recipe"])
        prompts = self._load_prompts(recipe)
        pages = self._pages(doc_id)
        previous_runs = {
            (row["station"], row["page_id"]): row for row in self._ledger.state(doc_id)
        }
        report = RunReport(doc_id=doc_id, recipe=recipe.name)

        index = 0
        while index < len(recipe.steps):
            if heartbeat_errors:
                raise RuntimeError("Work-order heartbeat failed") from heartbeat_errors[
                    0
                ]
            spec = recipe.steps[index]
            if spec.station.grain == "page":
                end = index + 1
                while (
                    end < len(recipe.steps)
                    and recipe.steps[end].station.grain == "page"
                ):
                    end += 1
                cells = self._run_page_batch(
                    doc_id,
                    recipe.steps[index:end],
                    pages,
                    prompts,
                    previous_runs,
                )
                report.cells.extend(cells)
                if any(cell.action == "failed" for cell in cells):
                    break
                index = end
                continue

            cell = self._run_cell(
                doc_id,
                spec,
                pages,
                page=None,
                prompts=prompts,
                previous_runs=previous_runs,
            )
            report.cells.append(cell)
            if cell.action == "failed":
                break
            index += 1
        return report

    def _heartbeat_claim(
        self,
        work_run_id: int,
        stop: threading.Event,
        errors: list[Exception],
    ) -> None:
        while not stop.wait(WORK_HEARTBEAT_SECONDS):
            try:
                with self._ledger_lock:
                    self._ledger.heartbeat_work(work_run_id)
            except Exception as error:
                errors.append(error)
                return

    # -- line execution -------------------------------------------------------

    def _run_page_batch(
        self,
        doc_id: str,
        batch: tuple[StationSpec, ...],
        pages: tuple[dict, ...],
        prompts: dict[str, prompt_store.Prompt],
        previous_runs: dict[tuple[str, str | None], sqlite3.Row],
    ) -> list[CellReport]:
        if not batch:
            return []

        def run_page(page: dict) -> list[CellReport]:
            cells = []
            for spec in batch:
                cell = self._run_cell(
                    doc_id,
                    spec,
                    pages,
                    page=page,
                    prompts=prompts,
                    previous_runs=previous_runs,
                )
                cells.append(cell)
                if cell.action == "failed":
                    break  # stop this page's chain, not the rest of the line
            return cells

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            return [
                cell for page_cells in pool.map(run_page, pages) for cell in page_cells
            ]

    # -- one cell -------------------------------------------------------------

    def _run_cell(
        self,
        doc_id: str,
        spec: StationSpec,
        pages: tuple[dict, ...],
        *,
        page: dict | None,
        prompts: dict[str, prompt_store.Prompt],
        previous_runs: dict[tuple[str, str | None], sqlite3.Row],
    ) -> CellReport:
        """Execute or skip one cell; failures stay contained in its page chain."""
        station = spec.station
        prompt = prompts.get(spec.prompt_name) if spec.prompt_name else None
        config = StationConfig(
            model=spec.model,
            prompt=prompt,
            params=spec.params,
            options=spec.options,
        )
        job = Job(
            doc_id=doc_id,
            pages=pages,
            page=page,
            library_root=self._library_root,
            config=config,
        )
        page_id = job.page_id
        input_paths = station.input_paths(job)
        missing = [path for path in input_paths if not path.exists()]
        if missing:
            return CellReport(
                station.name,
                page_id,
                "failed",
                error=f"missing inputs: {[str(path) for path in missing]}",
                cost_usd=0.0,
            )

        params_hash = fingerprint(
            json.dumps(
                {"params": dict(spec.params), "options": dict(spec.options)},
                sort_keys=True,
                ensure_ascii=True,
            )
        )
        config_fp = fingerprint(
            station.implementation_fingerprint,
            spec.model or "",
            prompt.sha256 if prompt else "",
            params_hash,
        )
        input_fp = fingerprint(
            *(content_fingerprint(path) for path in input_paths),
            *station.signature_extras(job),
        )
        output_path = station.output_path(job)
        latest = previous_runs.get((station.name, page_id))
        output_exists = output_path.is_file()
        output_fp = content_fingerprint(output_path) if output_exists else None
        output_is_current = (
            latest is not None
            and output_exists
            and station.name not in self._refresh
            and latest["input_fingerprint"] == input_fp
            and latest["output_fingerprint"] == output_fp
            and provenance_matches(
                output_path,
                {
                    "station": latest["station"],
                    "station_fingerprint": latest["station_fingerprint"],
                    "config_fingerprint": latest["config_fingerprint"],
                    "input_fingerprint": latest["input_fingerprint"],
                },
            )
        )
        if output_is_current:
            action = (
                "fresh" if latest["config_fingerprint"] == config_fp else "outdated"
            )
            return CellReport(station.name, page_id, action)

        stamp = read_provenance(output_path) if output_exists else None
        recoverable = bool(
            stamp
            and station.name not in self._refresh
            and stamp.get("station") == station.name
            and stamp.get("station_fingerprint") == station.implementation_fingerprint
            and stamp.get("config_fingerprint") == config_fp
            and stamp.get("input_fingerprint") == input_fp
            and stamp.get("output_fingerprint") == output_fp
        )
        with self._ledger_lock:
            run_id = self._ledger.begin_run(
                doc_id,
                station.name,
                page_id=page_id,
                station_fingerprint=station.implementation_fingerprint,
                config_fingerprint=config_fp,
                input_fingerprint=input_fp,
                model=spec.model,
                prompt_name=spec.prompt_name,
                prompt_hash=prompt.sha256 if prompt else None,
                params_hash=params_hash,
            )
        if recoverable:
            with self._ledger_lock:
                self._ledger.complete_run(
                    run_id,
                    output_path=str(output_path),
                    output_fingerprint=output_fp,
                    tokens_in=stamp.get("tokens_in"),
                    tokens_out=stamp.get("tokens_out"),
                    cost_usd=stamp.get("cost_usd"),
                )
            return CellReport(station.name, page_id, "recovered", cost_usd=0.0)

        cell = CellSpec(
            doc_id=doc_id,
            station=station.name,
            page_id=page_id,
            library_root=str(self._library_root),
            config_fingerprint=config_fp,
            input_fingerprint=input_fp,
            model=spec.model,
            prompt_name=spec.prompt_name,
            prompt_sha256=prompt.sha256 if prompt else None,
            params=dict(spec.params),
            options=dict(spec.options),
        )
        try:
            outcome = self._executor.execute(cell)
            outcome_path = Path(outcome.output_path)
            if outcome_path != output_path:
                raise ValueError(
                    f"Executor returned {outcome_path}, expected {output_path}"
                )
            output_fp = content_fingerprint(output_path)
            with self._ledger_lock:
                self._ledger.complete_run(
                    run_id,
                    output_path=outcome.output_path,
                    output_fingerprint=output_fp,
                    tokens_in=outcome.tokens_in,
                    tokens_out=outcome.tokens_out,
                    cost_usd=outcome.cost_usd,
                )
            return CellReport(station.name, page_id, "ran", cost_usd=outcome.cost_usd)
        except Exception as error:  # one cell's failure never takes down the line
            kind = getattr(error, "kind", type(error).__name__.lower())
            tokens_in = getattr(error, "tokens_in", None)
            tokens_out = getattr(error, "tokens_out", None)
            cost_usd = getattr(error, "cost_usd", None)
            with self._ledger_lock:
                self._ledger.fail_run(
                    run_id,
                    kind=kind,
                    detail=str(error),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                )
            return CellReport(
                station.name,
                page_id,
                "failed",
                error=str(error),
                cost_usd=cost_usd,
            )

    # -- resolution helpers ----------------------------------------------------

    @staticmethod
    def _load_prompts(recipe: Recipe) -> dict[str, prompt_store.Prompt]:
        prompts = {}
        for spec in recipe.steps:
            if spec.prompt_name and spec.prompt_name not in prompts:
                prompts[spec.prompt_name] = prompt_store.load(spec.prompt_name)
        return prompts

    def _pages(self, doc_id: str) -> tuple[dict, ...]:
        page_list = read_json(page_list_path(doc_id, self._library_root))
        validate_payload("page_list", page_list, expected_doc_id=doc_id)
        return tuple(sorted(page_list["pages"], key=lambda page: page.get("order", 0)))
