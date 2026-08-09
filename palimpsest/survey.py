"""Agent-filled survey between the source catalog and factory intake.

A survey run gives an OMP agent (Luna) one job per record: read the catalog
metadata, inspect sampled page images (it can zoom/crop), run a quick web
check on the item's identity, and fill out a factual checklist. The agent
reports observations; it never scores, ranks, or judges "interesting".

The survey store keeps:

- ``survey_evaluations``: one immutable row per (record_id, revision) with
  the full checklist, sampled pages, model identity, session, and cost;
- ``survey_runs``: an audit row per executed window;
- ``survey_cursor``: the durable window position per source, so repeated
  runs advance without re-paying for already-surveyed records.

Interest is a separate, mutable filter: ``survey filter`` scans stored
checklists against a rules file and prints the queue. Changing our mind
re-runs the filter for free; the survey evidence is recorded once, at cost.
Nothing here creates a work order; intake remains the explicit operator
action.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests

from palimpsest.catalog.database import CATALOG_DB_PATH, CatalogDB
from palimpsest.factory import agent_cell, prompt_store
from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.gateway import ImageContent
from palimpsest.factory.intake import (
    REQUEST_HEADERS,
    TIMEOUT_SECONDS,
    build_records,
    fetch_manifest,
)
from palimpsest.factory.workspace.io import atomic_write_json

SURVEY_MODEL = "gpt-5.6-luna"
SURVEY_PROMPT = "selection/catalog/checklist"
SURVEY_TOOLS = ("web_search", "read")
DEFAULT_RECORD_LIMIT = 12
DEFAULT_PAGE_SAMPLES = 3
DEFAULT_MAX_COST_USD = 10.0
MAX_IMAGE_BYTES = 12 * 1024 * 1024
SURVEY_DB_PATH = LIBRARY_ROOT / "survey.db"
_SCHEMA_VERSION = 3

TASK = (
    "Perform the survey defined in AGENTS.md: read evidence/catalog.json and "
    "the images in images/ (crop with python and view the crop to zoom), run "
    "web searches to check whether the item is already known, and write "
    "out/checklist.json exactly per the AGENTS.md output contract. Prefer "
    "node_repl filesystem APIs; if node_repl is unavailable or its writes "
    "fail, use another workspace-write mechanism rather than stopping. Then "
    "give the short final summary."
)

_CHECKLIST_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "physical_form": {
            "type": "string",
            "enum": ["handwritten", "printed", "mixed", "other"],
        },
        "sustained_text": {"type": "boolean"},
        "language_script": {"type": ["string", "null"]},
        "transcribable": {"type": "boolean"},
        "content_guess": {"type": "string"},
        "known_publicly": {"type": ["boolean", "null"]},
        "web_check": {"type": "string"},
        "evidence": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "physical_form",
        "sustained_text",
        "language_script",
        "transcribable",
        "content_guess",
        "known_publicly",
        "web_check",
        "evidence",
        "risks",
    ],
    "additionalProperties": False,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS survey_schema (
    version INTEGER PRIMARY KEY NOT NULL
);
INSERT INTO survey_schema (version) VALUES (?)
    ON CONFLICT (version) DO NOTHING;
CREATE TABLE IF NOT EXISTS survey_evaluations (
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    manifest_url TEXT NOT NULL,
    physical_form TEXT NOT NULL,
    sustained_text INTEGER NOT NULL,
    language_script TEXT,
    transcribable INTEGER NOT NULL,
    content_guess TEXT NOT NULL,
    known_publicly INTEGER,
    web_check TEXT NOT NULL,
    evidence TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    checklist_json TEXT NOT NULL,
    sampled_pages_json TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_name TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    session_id TEXT,
    tokens INTEGER NOT NULL,
    cost_usd REAL,
    run_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (record_id, revision)
);
CREATE TABLE IF NOT EXISTS survey_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    after_source_key TEXT,
    requested_records INTEGER NOT NULL,
    evaluated_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    cost_usd REAL,
    stop_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS survey_cursor (
    source_id TEXT PRIMARY KEY,
    last_source_key TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
PRAGMA user_version = 3;
"""


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed < 0 or parsed == float("inf") or parsed != parsed:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


class SurveyDB:
    """SQLite survey store: immutable checklist evaluations plus run audit."""

    def __init__(self, path: Path = SURVEY_DB_PATH):
        self._path = path

    def __enter__(self) -> "SurveyDB":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        stored = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if stored not in (0, _SCHEMA_VERSION):
            raise RuntimeError(
                f"survey store schema {stored} is not supported by this version "
                f"(expected {_SCHEMA_VERSION}); remove {self._path} and re-survey"
            )
        self._connection.executescript(_SCHEMA)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._connection.close()

    def cursor_for(self, source_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT last_source_key FROM survey_cursor WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return row["last_source_key"] if row else None

    def advance_cursor(self, source_id: str, last_source_key: str) -> None:
        self._connection.execute(
            """
            INSERT INTO survey_cursor (source_id, last_source_key, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (source_id) DO UPDATE SET
                last_source_key = excluded.last_source_key,
                updated_at = excluded.updated_at
            """,
            (source_id, last_source_key, _utc_timestamp()),
        )
        self._connection.commit()

    def already_surveyed(
        self, source_id: str, record_id: str, revision: int
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM survey_evaluations
            WHERE source_id = ? AND record_id = ? AND revision = ?
            """,
            (source_id, record_id, revision),
        ).fetchone()
        return row is not None

    def record_run(
        self,
        *,
        source_id: str,
        after_source_key: str | None,
        requested_records: int,
        evaluated_count: int,
        failure_count: int,
        cost_usd: float | None,
        stop_reason: str | None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO survey_runs (
                source_id, after_source_key, requested_records, evaluated_count,
                failure_count, cost_usd, stop_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                after_source_key,
                requested_records,
                evaluated_count,
                failure_count,
                cost_usd,
                stop_reason,
                _utc_timestamp(),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def record_evaluation(self, *, run_id: int, entry: Mapping[str, Any]) -> None:
        checklist = entry["checklist"]
        usage = entry["usage"]
        self._connection.execute(
            """
            INSERT INTO survey_evaluations (
                record_id, revision, source_id, source_key, source_url,
                manifest_url, physical_form, sustained_text, language_script,
                transcribable, content_guess, known_publicly, web_check,
                evidence, risks_json, checklist_json, sampled_pages_json,
                model, prompt_name, prompt_hash, session_id, tokens, cost_usd,
                run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["record_id"],
                entry["revision"],
                entry["source_id"],
                entry["source_key"],
                entry["source_url"],
                entry["manifest_url"],
                checklist["physical_form"],
                1 if checklist["sustained_text"] else 0,
                checklist["language_script"],
                1 if checklist["transcribable"] else 0,
                checklist["content_guess"],
                (
                    None
                    if checklist["known_publicly"] is None
                    else (1 if checklist["known_publicly"] else 0)
                ),
                checklist["web_check"],
                checklist["evidence"],
                json.dumps(checklist["risks"], ensure_ascii=False),
                json.dumps(checklist, ensure_ascii=False),
                json.dumps(entry["sampled_pages"], ensure_ascii=False),
                usage["resolved_model"],
                usage["prompt_name"],
                usage["prompt_hash"],
                entry.get("session_id"),
                entry.get("tokens", 0),
                usage["cost_usd"],
                run_id,
                _utc_timestamp(),
            ),
        )
        self._connection.commit()

    def latest_evaluations(self, source_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT * FROM survey_evaluations
            WHERE source_id = ?
            ORDER BY rowid DESC
            """,
            (source_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_run(self, source_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT * FROM survey_runs
            WHERE source_id = ?
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return dict(row) if row else None

    def stats(self, source_id: str) -> dict[str, Any]:
        evaluations = self._connection.execute(
            "SELECT COUNT(*) FROM survey_evaluations WHERE source_id = ?",
            (source_id,),
        ).fetchone()[0]
        return {"evaluated": evaluations}


def add_survey_command(subparsers: argparse._SubParsersAction) -> None:
    survey = subparsers.add_parser(
        "survey",
        help="Agent-filled catalog survey: checklist evidence, cursor, and queue",
    )
    survey_sub = survey.add_subparsers(dest="survey_action", required=True)

    run = survey_sub.add_parser("run", help="Survey a bounded catalog window")
    run.add_argument("source_id")
    run.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    run.add_argument("--survey-db", type=Path, default=SURVEY_DB_PATH)
    run.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    run.add_argument("--limit", type=_positive_int, default=DEFAULT_RECORD_LIMIT)
    run.add_argument("--pages", type=_positive_int, default=DEFAULT_PAGE_SAMPLES)
    run.add_argument("--keep", type=_positive_int, default=10)
    run.add_argument("--after", default=None, metavar="SOURCE_KEY")
    run.add_argument("--reset-cursor", action="store_true")
    run.add_argument(
        "--max-cost",
        type=_nonnegative_float,
        default=DEFAULT_MAX_COST_USD,
        metavar="USD",
    )
    run.add_argument("--output", type=Path, default=None)
    run.set_defaults(func=cmd_survey_run)

    status = survey_sub.add_parser("status", help="Show survey progress per source")
    status.add_argument("source_id")
    status.add_argument("--survey-db", type=Path, default=SURVEY_DB_PATH)
    status.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    status.set_defaults(func=cmd_survey_status)

    filter_cmd = survey_sub.add_parser(
        "filter", help="Apply the current interest rules to stored checklists"
    )
    filter_cmd.add_argument("source_id")
    filter_cmd.add_argument("--survey-db", type=Path, default=SURVEY_DB_PATH)
    filter_cmd.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    filter_cmd.add_argument("--rules", type=Path, default=None, metavar="FILE")
    filter_cmd.add_argument("--keep", type=_positive_int, default=0, metavar="N")
    filter_cmd.set_defaults(func=cmd_survey_filter)

    survey.set_defaults(func=None)


def cmd_survey_run(args: argparse.Namespace) -> None:
    output = args.output or _default_output(args.library_root, args.source_id)
    report = survey_catalog(
        source_id=args.source_id,
        catalog_db=args.db,
        survey_db=args.survey_db,
        library_root=args.library_root,
        record_limit=args.limit,
        page_samples=args.pages,
        after=args.after,
        reset_cursor=args.reset_cursor,
        max_cost_usd=args.max_cost,
        output=output,
    )
    print(output)
    for entry in report["evaluations"][: args.keep]:
        checklist = entry["checklist"]
        known = (
            "unknown"
            if checklist["known_publicly"] is None
            else ("known" if checklist["known_publicly"] else "not-known")
        )
        print(
            f"{entry['source_key']}  {checklist['physical_form']} "
            f"lang={checklist['language_script']} known={known} "
            f"transcribable={checklist['transcribable']}"
        )
        print(f"    guess: {checklist['content_guess']}")
    if report["stop_reason"] is not None:
        print(f"stopped: {report['stop_reason']}")


def cmd_survey_status(args: argparse.Namespace) -> None:
    with SurveyDB(args.survey_db) as survey:
        with CatalogDB(args.db) as catalog:
            eligible = len(catalog.selection_records(args.source_id))
        stats = survey.stats(args.source_id)
        cursor = survey.cursor_for(args.source_id)
        run = survey.latest_run(args.source_id)
    print(
        f"source={args.source_id} eligible={eligible} "
        f"evaluated={stats['evaluated']} remaining={eligible - stats['evaluated']}"
    )
    if cursor:
        print(f"cursor={cursor}")
    if run:
        print(
            f"last_run records={run['requested_records']} evaluated={run['evaluated_count']} "
            f"failed={run['failure_count']} cost={run['cost_usd']} stopped={run['stop_reason']}"
        )


def cmd_survey_filter(args: argparse.Namespace) -> None:
    rules = _load_rules(args.rules)
    adopted = _adopted_record_ids(args.library_root)
    with SurveyDB(args.survey_db) as survey:
        evaluations = survey.latest_evaluations(args.source_id)
    queued = [
        entry
        for entry in evaluations
        if _filter_score(entry, rules) > 0 and entry["record_id"] not in adopted
    ]
    queued.sort(key=lambda entry: -_filter_score(entry, rules))
    if args.keep:
        queued = queued[: args.keep]
    for entry in queued:
        score = _filter_score(entry, rules)
        reasons = _filter_reasons(entry, rules)
        print(f"{score:>2}  {entry['source_key']}  {entry['content_guess']}")
        print(f"         matches: {', '.join(reasons)}")
        if entry["web_check"]:
            print(f"         web: {entry['web_check'][:160]}")
    print(f"queue size: {len(queued)}")


def survey_catalog(
    *,
    source_id: str,
    catalog_db: Path,
    survey_db: Path,
    library_root: Path,
    record_limit: int,
    page_samples: int,
    after: str | None,
    reset_cursor: bool,
    max_cost_usd: float,
    output: Path,
) -> dict[str, Any]:
    """Survey a bounded catalog window and persist agent checklists as evidence."""
    if record_limit < 1 or page_samples < 1:
        raise ValueError("survey limits must be positive")
    if max_cost_usd < 0:
        raise ValueError("survey max cost must be non-negative")

    known_manifests = _known_manifest_urls(library_root)
    prompt = prompt_store.load(SURVEY_PROMPT)

    with SurveyDB(survey_db) as survey:
        cursor = None if reset_cursor else (after or survey.cursor_for(source_id))
        with CatalogDB(catalog_db) as database:
            available = database.selection_records(source_id, after=cursor)
        records = [
            record
            for record in available
            if record["record"]["manifest_url"] not in known_manifests
        ][:record_limit]

        evaluations: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        total_cost: float | None = 0.0
        stop_reason: str | None = None
        evaluated_count = 0

        for record in records:
            if total_cost is None:
                stop_reason = "a prior agent call returned unknown cost"
                break
            if total_cost >= max_cost_usd:
                stop_reason = f"the ${max_cost_usd:.4f} cost ceiling was reached"
                break
            try:
                entry = _survey_record(
                    record, source_id, prompt, library_root, page_samples
                )
            except (
                requests.RequestException,
                agent_cell.AgentCellError,
                ValueError,
            ) as error:
                failures.append(
                    {
                        "record_id": record["record_id"],
                        "source_key": record["source_key"],
                        "error": f"{type(error).__name__}: {str(error)[:500]}",
                    }
                )
                continue

            response_cost = entry["usage"]["cost_usd"]
            total_cost = (
                None if response_cost is None else total_cost + response_cost
            )
            evaluations.append(entry)
            evaluated_count += 1

        run_id = survey.record_run(
            source_id=source_id,
            after_source_key=cursor,
            requested_records=record_limit,
            evaluated_count=evaluated_count,
            failure_count=len(failures),
            cost_usd=total_cost,
            stop_reason=stop_reason,
        )
        for entry in evaluations:
            survey.record_evaluation(run_id=run_id, entry=entry)
        if evaluations:
            last_key = max(entry["source_key"] for entry in evaluations)
            survey.advance_cursor(source_id, last_key)

    report = {
        "schema_version": 3,
        "created_at": _utc_timestamp(),
        "source_id": source_id,
        "after_source_key": cursor,
        "requested_records": record_limit,
        "sampled_pages_per_record": page_samples,
        "requested_model": SURVEY_MODEL,
        "prompt": {"name": prompt.name, "sha256": prompt.sha256},
        "maximum_cost_usd": max_cost_usd,
        "cost_usd": total_cost,
        "stop_reason": stop_reason,
        "evaluations": evaluations,
        "failures": failures,
    }
    atomic_write_json(output, report)
    return report


def _survey_record(
    record: Mapping[str, Any],
    source_id: str,
    prompt: Any,
    library_root: Path,
    page_samples: int,
) -> dict[str, Any]:
    """One agent pass over a record: sample, stage, run, validate, checklist."""
    work_root = _work_root(library_root, source_id, record["record_id"])
    samples_dir = work_root / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    pages, image_files = _sample_record(record, page_samples, samples_dir)
    workspace = agent_cell.stage_workspace(
        work_root / "cell",
        skill=prompt.text,
        evidence={"catalog": record["record"]},
        images=image_files,
    )
    run = agent_cell.run(
        workspace,
        TASK,
        model=SURVEY_MODEL,
        executor="omp",
        tool_names=SURVEY_TOOLS,
    )
    checklist = agent_cell.read_artifact(workspace, "checklist.json")
    validation = _checklist_errors(checklist)
    if validation:
        repair = agent_cell.resume(
            workspace,
            run.session_id,
            _repair_message(validation),
            executor="omp",
        )
        checklist = agent_cell.read_artifact(workspace, "checklist.json")
        validation = _checklist_errors(checklist)
        run = _merge_agent_runs(run, repair)
    if validation:
        raise ValueError(
            "survey checklist rejected after repair — "
            + "; ".join(validation[:5])
            + (f" (+{len(validation) - 5} more)" if len(validation) > 5 else "")
        )
    return {
        "record_id": record["record_id"],
        "revision": record["revision"],
        "source_id": source_id,
        "source_key": record["source_key"],
        "source_url": record["source_url"],
        "manifest_url": record["record"]["manifest_url"],
        "sampled_pages": pages,
        "checklist": checklist,
        "session_id": run.session_id,
        "tokens": run.tokens,
        "usage": {
            "requested_model": SURVEY_MODEL,
            "resolved_model": SURVEY_MODEL,
            "prompt_name": prompt.name,
            "prompt_hash": prompt.sha256,
            "cost_usd": run.cost_usd,
        },
    }


def _merge_agent_runs(primary: Any, repair: Any) -> Any:
    """Fold a repair turn's usage into the primary AgentRun for the ledger."""
    return type(primary)(
        session_id=primary.session_id,
        tokens=primary.tokens + repair.tokens,
        log_path=primary.log_path,
        cost_usd=(
            None
            if primary.cost_usd is None or repair.cost_usd is None
            else primary.cost_usd + repair.cost_usd
        ),
        process_stats=primary.process_stats,
    )


def _checklist_errors(checklist: Any) -> list[str]:
    if not isinstance(checklist, dict):
        return ["checklist must be a JSON object"]
    errors: list[str] = []
    for name in _CHECKLIST_SCHEMA["required"]:
        if name not in checklist:
            errors.append(f"missing field {name}")
    if "physical_form" in checklist and checklist["physical_form"] not in (
        "handwritten",
        "printed",
        "mixed",
        "other",
    ):
        errors.append("physical_form must be handwritten|printed|mixed|other")
    for name in ("sustained_text", "transcribable"):
        if name in checklist and not isinstance(checklist[name], bool):
            errors.append(f"{name} must be a boolean")
    if (
        "known_publicly" in checklist
        and checklist["known_publicly"] is not None
        and not isinstance(checklist["known_publicly"], bool)
    ):
        errors.append("known_publicly must be boolean or null")
    if "language_script" in checklist and not isinstance(
        checklist["language_script"], (str, type(None))
    ):
        errors.append("language_script must be a string or null")
    for name in ("content_guess", "web_check", "evidence"):
        if name in checklist and not isinstance(checklist[name], str):
            errors.append(f"{name} must be a string")
    if "risks" in checklist and (
        not isinstance(checklist["risks"], list)
        or not all(isinstance(item, str) for item in checklist["risks"])
    ):
        errors.append("risks must be a list of strings")
    return errors


def _repair_message(errors: list[str]) -> str:
    return (
        "The acceptance harness reviewed out/checklist.json and rejected it: "
        + "; ".join(errors)
        + ". Rewrite out/checklist.json exactly per the AGENTS.md output "
        "contract, then give the short final summary."
    )


def _sample_record(
    record: Mapping[str, Any],
    page_samples: int,
    samples_dir: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    manifest_url = record["record"]["manifest_url"]
    manifest = fetch_manifest(manifest_url)
    _, page_list = build_records(
        "catalog_survey", manifest_url, manifest, image_size=1200
    )
    sampled = _evenly_spaced(page_list["pages"], page_samples)
    pages = [
        {
            "page_id": page["page_id"],
            "order": page["order"],
            "label": page.get("label"),
            "url": page["url"],
        }
        for page in sampled
    ]
    images: list[Path] = []
    for index, page in enumerate(sampled, start=1):
        content = _download_image(page["url"])
        suffix = ".jpg" if content.mime == "image/jpeg" else ".png"
        target = samples_dir / f"page_{index:02d}{suffix}"
        target.write_bytes(content.data)
        images.append(target)
    return pages, images


def _evenly_spaced(pages: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count >= len(pages):
        return list(pages)
    indexes = [
        round((index + 1) * (len(pages) - 1) / (count + 1)) for index in range(count)
    ]
    return [pages[index] for index in indexes]


def _download_image(url: str) -> ImageContent:
    with requests.get(
        url,
        timeout=TIMEOUT_SECONDS,
        headers=REQUEST_HEADERS,
        stream=True,
    ) as response:
        response.raise_for_status()
        mime = (
            response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        )
        if not mime.startswith("image/"):
            raise ValueError(
                f"Sample page is not an image: {url} ({mime or 'unknown'})"
            )
        declared_size = response.headers.get("Content-Length")
        if declared_size is not None and int(declared_size) > MAX_IMAGE_BYTES:
            raise ValueError(f"Sample page exceeds {MAX_IMAGE_BYTES} bytes: {url}")
        body = bytearray()
        for chunk in response.iter_content(1024 * 1024):
            body.extend(chunk)
            if len(body) > MAX_IMAGE_BYTES:
                raise ValueError(f"Sample page exceeds {MAX_IMAGE_BYTES} bytes: {url}")
    return ImageContent(bytes(body), mime=mime)


def _work_root(library_root: Path, source_id: str, record_id: str) -> Path:
    safe_source = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in source_id
    )
    safe_record = record_id.replace(":", "_")
    return library_root / "surveys" / "work" / safe_source / safe_record


def _known_manifest_urls(library_root: Path) -> set[str]:
    urls: set[str] = set()
    if not library_root.is_dir():
        return urls
    for metadata_path in library_root.glob("*/metadata.json"):
        payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        source = payload.get("source")
        if isinstance(source, Mapping):
            manifest_url = source.get("manifest_url")
            if isinstance(manifest_url, str) and manifest_url:
                urls.add(manifest_url)
    return urls


def _adopted_record_ids(library_root: Path) -> set[str]:
    adopted: set[str] = set()
    if not library_root.is_dir():
        return adopted
    for metadata_path in library_root.glob("*/metadata.json"):
        payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        record_id = payload.get("catalog_record_id")
        if isinstance(record_id, str) and record_id:
            adopted.add(record_id)
    return adopted


def _default_output(library_root: Path, source_id: str) -> Path:
    safe_source = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in source_id
    )
    return library_root / "surveys" / f"{_utc_compact()}-{safe_source}.json"


# ---------------------------------------------------------------------------
# Interest filter: the mutable knob over stored checklists.
# ---------------------------------------------------------------------------

_DEFAULT_RULES = {
    "name": "nuggets",
    "require": {"transcribable": True, "sustained_text": True},
    "languages": ["Chinese", "Latin", "zh", "la"],
    "forgotten_only": True,
    "keywords": ["alchemy", "recipe", "medicine", "diary", "letter", "life"],
}

_RULES_FIELDS = {"name", "require", "languages", "forgotten_only", "keywords"}


def _load_rules(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _DEFAULT_RULES
    payload = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(payload) - _RULES_FIELDS
    if unknown:
        raise ValueError(f"unknown filter rule fields: {sorted(unknown)}")
    return payload


def _row_checklist(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a stored evaluation row back to checklist semantics."""
    known = entry.get("known_publicly")
    return {
        "physical_form": entry.get("physical_form"),
        "sustained_text": bool(entry.get("sustained_text")),
        "language_script": entry.get("language_script"),
        "transcribable": bool(entry.get("transcribable")),
        "content_guess": entry.get("content_guess"),
        "known_publicly": None if known is None else bool(known),
        "web_check": entry.get("web_check"),
        "evidence": entry.get("evidence"),
        "risks": json.loads(entry.get("risks_json") or "[]"),
    }


def _filter_score(entry: Mapping[str, Any], rules: Mapping[str, Any]) -> int:
    checklist = _row_checklist(entry)
    require = rules.get("require", {})
    for field, expected in require.items():
        if checklist.get(field) != expected:
            return 0
    score = 0
    languages = rules.get("languages")
    if languages and checklist.get("language_script") in languages:
        score += 1
    if rules.get("forgotten_only") and checklist.get("known_publicly") is False:
        score += 1
    keywords = rules.get("keywords")
    if keywords and any(
        keyword in (checklist.get("content_guess") or "").lower()
        for keyword in keywords
    ):
        score += 1
    return score


def _filter_reasons(entry: Mapping[str, Any], rules: Mapping[str, Any]) -> list[str]:
    checklist = _row_checklist(entry)
    require = rules.get("require", {})
    if any(
        checklist.get(field) != expected for field, expected in require.items()
    ):
        return []
    reasons: list[str] = []
    languages = rules.get("languages")
    if languages and checklist.get("language_script") in languages:
        reasons.append(f"language {checklist.get('language_script')}")
    if rules.get("forgotten_only") and checklist.get("known_publicly") is False:
        reasons.append("not-known (web check)")
    keywords = rules.get("keywords")
    if keywords:
        matched = [
            keyword
            for keyword in keywords
            if keyword in (checklist.get("content_guess") or "").lower()
        ]
        if matched:
            reasons.append(f"keyword {', '.join(matched)}")
    return reasons
