"""The conductor: drives a work order through its recipe (FACTORY.md §2.4).

The only component that knows ordering, concurrency, and freshness. The unit
of work is one cell — (page × station) or (doc × manuscript-station) — and
every cell decision comes from the two fingerprints (FACTORY.md §2.6):

- missing            → run
- fresh              → skip (config and inputs both match the latest run)
- stale              → run  (inputs drifted: upstream was refreshed)
- outdated           → skip + report (config drifted: rerunning paid work is
                       explicit — pass ``refresh={station}`` to force it)

Page-line stations run across pages on a thread pool; a page station whose
consumed kinds include a manuscript-produced kind (a jig) gates the line:
everything before it streams, the jig builds, then the line resumes.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from palimpsest.factory import prompt_store
from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.core.ledger import Ledger, fingerprint
from palimpsest.factory.core.recipe import Recipe, StationSpec, load as load_recipe
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.workspace.io import atomic_write_json, read_json, utc_now
from palimpsest.factory.workspace.layout import page_list_path

DEFAULT_WORKERS = 6


def _content_hash(path: Path) -> str:
    """Hash an artifact's CONTENT, excluding its provenance stamp.

    The stamp carries a timestamp, so hashing raw bytes would make every
    refresh look like new content and cascade staleness even when the model
    reproduced the identical output — violating the no-cascade-without-cause
    rule (FACTORY.md §2.6). Binary artifacts hash as raw bytes.
    """
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        else:
            if isinstance(payload, dict):
                payload.pop("provenance", None)
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class CellReport:
    station: str
    page_id: str | None
    action: str          # ran | fresh | outdated | failed
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
    def cost_usd(self) -> float:
        return sum(cell.cost_usd or 0.0 for cell in self.cells)


class Conductor:
    def __init__(
        self,
        ledger: Ledger,
        *,
        library_root: Path = LIBRARY_ROOT,
        workers: int = DEFAULT_WORKERS,
        refresh: frozenset[str] = frozenset(),
    ) -> None:
        self._ledger = ledger
        self._ledger_lock = threading.Lock()
        self._library_root = library_root
        self._workers = workers
        self._refresh = refresh
        self._prompts: dict[str, prompt_store.Prompt] = {}

    # -- public ---------------------------------------------------------------

    def run(self, doc_id: str) -> RunReport:
        item = self._item(doc_id)
        recipe = load_recipe(item["recipe"])
        pages = self._pages(doc_id)
        report = RunReport(doc_id=doc_id, recipe=recipe.name)

        manuscript_pending = list(recipe.manuscript_stations)
        segment: list[StationSpec] = []
        for spec in recipe.page_stations:
            jig_kinds = self._jig_kinds(spec, recipe)
            if jig_kinds:
                self._run_page_segment(doc_id, segment, pages, report)
                segment = []
                for jig_spec in [s for s in manuscript_pending
                                 if s.station.produces in jig_kinds]:
                    self._run_cell(doc_id, jig_spec, pages, page=None, report=report)
                    manuscript_pending.remove(jig_spec)
            segment.append(spec)
        self._run_page_segment(doc_id, segment, pages, report)

        for spec in manuscript_pending:
            self._run_cell(doc_id, spec, pages, page=None, report=report)
        return report

    # -- planning -------------------------------------------------------------

    @staticmethod
    def _jig_kinds(spec: StationSpec, recipe: Recipe) -> set[str]:
        manuscript_kinds = {s.station.produces for s in recipe.manuscript_stations}
        return set(spec.station.consumes) & manuscript_kinds

    def _run_page_segment(
        self, doc_id: str, segment: list[StationSpec],
        pages: tuple[dict, ...], report: RunReport,
    ) -> None:
        if not segment:
            return

        def run_page(page: dict) -> None:
            for spec in segment:
                if not self._run_cell(doc_id, spec, pages, page=page, report=report):
                    return  # a failed cell stops this page's chain, not the line

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            list(pool.map(run_page, pages))

    # -- one cell -------------------------------------------------------------

    def _run_cell(
        self, doc_id: str, spec: StationSpec, pages: tuple[dict, ...],
        *, page: dict | None, report: RunReport,
    ) -> bool:
        """Execute (or skip) one cell. Returns False only on failure."""
        station = spec.station
        job = Job(
            doc_id=doc_id, pages=pages, page=page,
            library_root=self._library_root,
            config=self._config(spec),
        )
        page_id = job.page_id

        missing = [p for p in station.input_paths(job) if not p.exists()]
        if missing:
            self._report(report, CellReport(
                station.name, page_id, "failed",
                error=f"missing inputs: {[str(p) for p in missing]}"))
            return False

        config_fp, input_fp = self._fingerprints(spec, job)
        latest = self._latest(doc_id, station.name, page_id)
        output_exists = station.output_path(job).exists()
        if latest is not None and output_exists and station.name not in self._refresh:
            if latest["input_fingerprint"] == input_fp:
                if latest["config_fingerprint"] == config_fp:
                    self._report(report, CellReport(station.name, page_id, "fresh"))
                    return True
                self._report(report, CellReport(station.name, page_id, "outdated"))
                return True  # outdated is not a failure; downstream still consistent

        with self._ledger_lock:
            run_id = self._ledger.begin_run(
                doc_id, station.name, page_id=page_id,
                station_version=station.version,
                config_fingerprint=config_fp, input_fingerprint=input_fp,
                model=spec.model, prompt_name=spec.prompt_name,
                prompt_hash=job.config.prompt.sha256 if job.config.prompt else None,
                params_hash=self._params_hash(spec),
            )
        try:
            result = station.run(job)
            output_path = station.output_path(job)
            if result.payload is not None:
                validate_payload(station.produces, result.payload)
                payload = dict(result.payload)
                payload["provenance"] = self._provenance(
                    spec, job, config_fp, input_fp, result)
                atomic_write_json(output_path, payload)
            else:
                atomic_write_json(
                    output_path.with_suffix(output_path.suffix + ".provenance.json"),
                    self._provenance(spec, job, config_fp, input_fp, result),
                )
            output_fp = _content_hash(output_path)
            with self._ledger_lock:
                self._ledger.complete_run(
                    run_id,
                    output_path=str(output_path),
                    output_fingerprint=output_fp,
                    tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                    cost_usd=result.cost_usd,
                )
            self._report(report, CellReport(
                station.name, page_id, "ran", cost_usd=result.cost_usd))
            return True
        except Exception as error:  # one cell's failure never takes down the line
            with self._ledger_lock:
                self._ledger.fail_run(
                    run_id, kind=type(error).__name__.lower(), detail=str(error))
            self._report(report, CellReport(
                station.name, page_id, "failed", error=str(error)))
            return False

    # -- resolution helpers ----------------------------------------------------

    def _config(self, spec: StationSpec) -> StationConfig:
        prompt = None
        if spec.prompt_name:
            if spec.prompt_name not in self._prompts:
                self._prompts[spec.prompt_name] = prompt_store.load(spec.prompt_name)
            prompt = self._prompts[spec.prompt_name]
        return StationConfig(
            model=spec.model, prompt=prompt,
            params=spec.params, options=spec.options,
        )

    def _params_hash(self, spec: StationSpec) -> str:
        return fingerprint(json.dumps(
            {"params": dict(spec.params), "options": dict(spec.options)},
            sort_keys=True, ensure_ascii=True,
        ))

    def _fingerprints(self, spec: StationSpec, job: Job) -> tuple[str, str]:
        config_fp = fingerprint(
            spec.station.version, spec.model or "",
            job.config.prompt.sha256 if job.config.prompt else "",
            self._params_hash(spec),
        )
        file_hashes = [
            _content_hash(path)
            for path in spec.station.input_paths(job) if path.exists()
        ]
        input_fp = fingerprint(*file_hashes, *spec.station.signature_extras(job))
        return config_fp, input_fp

    def _provenance(
        self, spec: StationSpec, job: Job,
        config_fp: str, input_fp: str, result,
    ) -> dict:
        stamp = {
            "station": spec.station.name,
            "station_version": spec.station.version,
            "config_fingerprint": config_fp,
            "input_fingerprint": input_fp,
            "created_at": utc_now(),
        }
        if spec.model:
            stamp["model"] = spec.model
        if job.config.prompt:
            stamp["prompt_name"] = job.config.prompt.name
            stamp["prompt_sha256"] = job.config.prompt.sha256
        if spec.params or spec.options:
            stamp["params"] = {**dict(spec.params), **dict(spec.options)}
        if result.tokens_in is not None:
            stamp["tokens_in"] = result.tokens_in
            stamp["tokens_out"] = result.tokens_out
        if result.cost_usd is not None:
            stamp["cost_usd"] = result.cost_usd
        return stamp

    def _latest(self, doc_id: str, station: str, page_id: str | None):
        with self._ledger_lock:
            rows = self._ledger.state(doc_id)
        for row in rows:
            if row["station"] == station and row["page_id"] == page_id:
                return row
        return None

    def _item(self, doc_id: str):
        with self._ledger_lock:
            items = [row for row in self._ledger.list_items() if row["doc_id"] == doc_id]
        if not items:
            raise KeyError(
                f"No work order for {doc_id!r} — run "
                f"'palimpsest factory adopt --doc-id {doc_id} --recipe <name>' first"
            )
        return items[0]

    def _pages(self, doc_id: str) -> tuple[dict, ...]:
        page_list = read_json(page_list_path(doc_id, self._library_root))
        return tuple(sorted(page_list["pages"], key=lambda p: p.get("order", 0)))

    def _report(self, report: RunReport, cell: CellReport) -> None:
        with self._ledger_lock:
            report.cells.append(cell)
