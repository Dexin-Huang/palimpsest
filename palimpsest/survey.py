"""Durable model-grounded survey between the source catalog and factory intake.

A survey run samples page images from a bounded catalog window, asks the
triage model to describe what it read (neutral) and to guess the content,
then answer independent yes/no checks. Every evaluation is persisted as
evidence. The derived hit score is the number of true checks (0-5), computed
by us — the model never emits a verdict or an arbitrary score.

The survey store keeps:

- ``survey_evaluations``: one immutable row per (record_id, revision) with
  the checks, hit score, what_was_read, content guess, sampled pages, model
  identity, and cost;
- ``survey_runs``: an audit row per executed window;
- ``survey_cursor``: the durable window position per source, so repeated
  runs advance without re-paying for already-surveyed records.

The queue is derived at read time: evaluations with hits > 0 whose manifest
is not already in the library and whose record is not adopted by a workspace
``catalog_record_id``. Nothing in this module creates a work order; intake
remains the explicit operator action.
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
from palimpsest.factory import prompt_store
from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.gateway import (
    GatewayError,
    ImageContent,
    ModelRequest,
    generate_json,
)
from palimpsest.factory.intake import (
    REQUEST_HEADERS,
    TIMEOUT_SECONDS,
    build_records,
    fetch_manifest,
)
from palimpsest.factory.workspace.io import atomic_write_json

SURVEY_MODEL = "token-plan/qwen3.8-max"
SURVEY_PROMPT = "selection/catalog/interest"
DEFAULT_RECORD_LIMIT = 12
DEFAULT_PAGE_SAMPLES = 3
DEFAULT_RECOMMENDATIONS = 5
DEFAULT_MAX_COST_USD = 1.0
MAX_IMAGE_BYTES = 12 * 1024 * 1024
SURVEY_DB_PATH = LIBRARY_ROOT / "survey.db"
_SCHEMA_VERSION = 2

_CHECK_NAMES = (
    "sustained_text",
    "handwritten",
    "language_identified",
    "transcribable",
    "distinctive",
)

_RESULT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "what_was_read": {"type": "string"},
        "content_guess": {"type": "string"},
        "summary": {"type": "string"},
        "checks": {
            "type": "object",
            "properties": {name: {"type": "boolean"} for name in _CHECK_NAMES},
            "required": list(_CHECK_NAMES),
            "additionalProperties": False,
        },
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "what_was_read",
        "content_guess",
        "summary",
        "checks",
        "risks",
    ],
    "additionalProperties": False,
}


def _hits(result: Mapping[str, Any]) -> int:
    """The derived score: the number of true checks (0-5), computed by us."""
    return sum(1 for name in _CHECK_NAMES if result["checks"].get(name) is True)

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
    hits INTEGER NOT NULL,
    summary TEXT NOT NULL,
    what_was_read TEXT NOT NULL,
    content_guess TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    sampled_pages_json TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_name TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
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
PRAGMA user_version = 2;
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
    """SQLite survey store: immutable evaluations plus durable run audit."""

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
        result = entry["result"]
        usage = entry["usage"]
        self._connection.execute(
            """
            INSERT INTO survey_evaluations (
                record_id, revision, source_id, source_key, source_url,
                manifest_url, hits, summary, what_was_read, content_guess,
                checks_json, risks_json, sampled_pages_json, model,
                prompt_name, prompt_hash, cost_usd, run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["record_id"],
                entry["revision"],
                entry["source_id"],
                entry["source_key"],
                entry["source_url"],
                entry["manifest_url"],
                _hits(result),
                result["summary"],
                result["what_was_read"],
                result["content_guess"],
                json.dumps(result["checks"], ensure_ascii=False),
                json.dumps(result["risks"], ensure_ascii=False),
                json.dumps(entry["sampled_pages"], ensure_ascii=False),
                usage["resolved_model"],
                usage["prompt_name"],
                usage["prompt_hash"],
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
            ORDER BY hits DESC, source_key
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
        hits = self._connection.execute(
            """
            SELECT COUNT(*) FROM survey_evaluations
            WHERE source_id = ? AND hits > 0
            """,
            (source_id,),
        ).fetchone()[0]
        return {
            "evaluated": evaluations,
            "hits": hits,
        }


def add_survey_command(subparsers: argparse._SubParsersAction) -> None:
    survey = subparsers.add_parser(
        "survey",
        help="Durably survey catalog records: evaluate, advance the window, queue",
    )
    survey_sub = survey.add_subparsers(dest="survey_action", required=True)

    run = survey_sub.add_parser("run", help="Evaluate a bounded catalog window")
    run.add_argument("source_id")
    run.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    run.add_argument("--survey-db", type=Path, default=SURVEY_DB_PATH)
    run.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    run.add_argument("--limit", type=_positive_int, default=DEFAULT_RECORD_LIMIT)
    run.add_argument("--pages", type=_positive_int, default=DEFAULT_PAGE_SAMPLES)
    run.add_argument("--keep", type=_positive_int, default=DEFAULT_RECOMMENDATIONS)
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

    queue = survey_sub.add_parser("queue", help="Show unadopted recommendations")
    queue.add_argument("source_id")
    queue.add_argument("--survey-db", type=Path, default=SURVEY_DB_PATH)
    queue.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    queue.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    queue.add_argument("--keep", type=_positive_int, default=0, metavar="N")
    queue.set_defaults(func=cmd_survey_queue)

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
        recommendation_limit=args.keep,
        after=args.after,
        reset_cursor=args.reset_cursor,
        max_cost_usd=args.max_cost,
        output=output,
    )
    print(output)
    for result in report["recommendations"]:
        print(
            f"{result['hits']:>2}/5  {result['source_key']}  {result['summary']}"
        )
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
        f"evaluated={stats['evaluated']} hits={stats['hits']} "
        f"remaining={eligible - stats['evaluated']}"
    )
    if cursor:
        print(f"cursor={cursor}")
    if run:
        print(
            f"last_run records={run['requested_records']} evaluated={run['evaluated_count']} "
            f"failed={run['failure_count']} cost={run['cost_usd']} stopped={run['stop_reason']}"
        )


def cmd_survey_queue(args: argparse.Namespace) -> None:
    adopted = _adopted_record_ids(args.library_root)
    with SurveyDB(args.survey_db) as survey:
        evaluations = survey.latest_evaluations(args.source_id)
    queued = [
        entry
        for entry in evaluations
        if entry["hits"] > 0 and entry["record_id"] not in adopted
    ]
    if args.keep:
        queued = queued[: args.keep]
    for entry in queued:
        checks = json.loads(entry["checks_json"])
        true_checks = ", ".join(name for name in _CHECK_NAMES if checks.get(name))
        print(f"{entry['hits']:>2}/5  {entry['source_key']}  {entry['summary']}")
        print(f"         checks: {true_checks}")
        print(f"         content: {entry['content_guess']}")
        print(f"         read:    {entry['what_was_read']}")
    print(f"queue size: {len(queued)}")


def survey_catalog(
    *,
    source_id: str,
    catalog_db: Path,
    survey_db: Path,
    library_root: Path,
    record_limit: int,
    page_samples: int,
    recommendation_limit: int,
    after: str | None,
    reset_cursor: bool,
    max_cost_usd: float,
    output: Path,
) -> dict[str, Any]:
    """Evaluate a bounded catalog window and persist every decision as evidence."""
    if record_limit < 1 or page_samples < 1 or recommendation_limit < 1:
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
                stop_reason = "a prior model call returned unknown cost"
                break
            if total_cost >= max_cost_usd:
                stop_reason = f"the ${max_cost_usd:.4f} cost ceiling was reached"
                break
            try:
                sampled_pages, images = _sample_record(record, page_samples)
                result, response = generate_json(
                    ModelRequest(
                        model=SURVEY_MODEL,
                        prompt=_survey_request(prompt.text, record, sampled_pages),
                        images=images,
                        max_output_tokens=4096,
                        media_resolution="medium",
                        json_output=True,
                        json_schema=_RESULT_SCHEMA,
                    )
                )
            except (requests.RequestException, GatewayError, ValueError) as error:
                failures.append(
                    {
                        "record_id": record["record_id"],
                        "source_key": record["source_key"],
                        "error": f"{type(error).__name__}: {str(error)[:500]}",
                    }
                )
                continue

            response_cost = response.cost_usd
            total_cost = None if response_cost is None else total_cost + response_cost
            entry = {
                "record_id": record["record_id"],
                "revision": record["revision"],
                "source_id": source_id,
                "source_key": record["source_key"],
                "source_url": record["source_url"],
                "manifest_url": record["record"]["manifest_url"],
                "sampled_pages": sampled_pages,
                "result": result,
                "usage": {
                    "requested_model": SURVEY_MODEL,
                    "resolved_model": response.model,
                    "prompt_name": prompt.name,
                    "prompt_hash": prompt.sha256,
                    "cost_usd": response_cost,
                },
            }
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

    recommendations = sorted(
        (entry for entry in evaluations if _hits(entry["result"]) > 0),
        key=lambda entry: (-_hits(entry["result"]), entry["source_key"]),
    )[:recommendation_limit]
    flattened = [
        {
            "record_id": entry["record_id"],
            "source_key": entry["source_key"],
            "manifest_url": entry["manifest_url"],
            "hits": _hits(entry["result"]),
            **entry["result"],
        }
        for entry in recommendations
    ]
    report = {
        "schema_version": 2,
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
        "recommendations": flattened,
    }
    atomic_write_json(output, report)
    return report


def _sample_record(
    record: Mapping[str, Any], page_samples: int
) -> tuple[list[dict[str, Any]], tuple[ImageContent, ...]]:
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
    images = tuple(_download_image(page["url"]) for page in sampled)
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


def _survey_request(
    prompt: str,
    record: Mapping[str, Any],
    sampled_pages: list[dict[str, Any]],
) -> str:
    evidence = {
        "record_id": record["record_id"],
        "source_key": record["source_key"],
        "source_url": record["source_url"],
        "catalog_record": record["record"],
        "sampled_pages_in_image_order": sampled_pages,
    }
    return (
        f"{prompt}\n\nEvidence:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"
    )


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
