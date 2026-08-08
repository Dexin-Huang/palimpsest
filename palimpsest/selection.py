"""Model-grounded triage between the source catalog and factory intake."""

from __future__ import annotations

import argparse
import json
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

SELECTION_MODEL = "token-plan/qwen3.8-max"
SELECTION_PROMPT = "selection/catalog/interest"
DEFAULT_RECORD_LIMIT = 12
DEFAULT_PAGE_SAMPLES = 3
DEFAULT_RECOMMENDATIONS = 5
DEFAULT_MAX_COST_USD = 1.0
MAX_IMAGE_BYTES = 12 * 1024 * 1024

_RESULT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["prioritize", "consider", "skip"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "significance": {"type": "string"},
        "language_or_script": {"type": "string"},
        "transcription_feasibility": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "score",
        "summary",
        "significance",
        "language_or_script",
        "transcription_feasibility",
        "evidence",
        "risks",
    ],
    "additionalProperties": False,
}


def add_selection_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "select",
        help="Use Qwen and sampled IIIF pages to shortlist catalog records",
    )
    parser.add_argument("source_id")
    parser.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    parser.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    parser.add_argument("--limit", type=_positive_int, default=DEFAULT_RECORD_LIMIT)
    parser.add_argument("--pages", type=_positive_int, default=DEFAULT_PAGE_SAMPLES)
    parser.add_argument("--keep", type=_positive_int, default=DEFAULT_RECOMMENDATIONS)
    parser.add_argument("--after", default=None, metavar="SOURCE_KEY")
    parser.add_argument(
        "--max-cost",
        type=_nonnegative_float,
        default=DEFAULT_MAX_COST_USD,
        metavar="USD",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.set_defaults(func=cmd_select)


def cmd_select(args: argparse.Namespace) -> None:
    output = args.output or _default_output(args.library_root, args.source_id)
    report = select_catalog(
        source_id=args.source_id,
        catalog_db=args.db,
        library_root=args.library_root,
        record_limit=args.limit,
        page_samples=args.pages,
        recommendation_limit=args.keep,
        after=args.after,
        max_cost_usd=args.max_cost,
        output=output,
    )
    print(output)
    for result in report["recommendations"]:
        print(
            f"{result['score']:>3}  {result['verdict']:<10}  "
            f"{result['source_key']}  {result['summary']}"
        )
    if report["stop_reason"] is not None:
        print(f"stopped: {report['stop_reason']}")


def select_catalog(
    *,
    source_id: str,
    catalog_db: Path,
    library_root: Path,
    record_limit: int,
    page_samples: int,
    recommendation_limit: int,
    after: str | None,
    max_cost_usd: float,
    output: Path,
) -> dict[str, Any]:
    """Evaluate a bounded catalog window without creating production work."""
    if record_limit < 1 or page_samples < 1 or recommendation_limit < 1:
        raise ValueError("selection limits must be positive")
    if max_cost_usd < 0:
        raise ValueError("selection max cost must be non-negative")

    with CatalogDB(catalog_db) as database:
        available = database.selection_records(source_id, after=after)
    known_manifests = _known_manifest_urls(library_root)
    records = [
        record
        for record in available
        if record["record"]["manifest_url"] not in known_manifests
    ][:record_limit]

    prompt = prompt_store.load(SELECTION_PROMPT)
    evaluations: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    total_cost: float | None = 0.0
    stop_reason: str | None = None

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
                    model=SELECTION_MODEL,
                    prompt=_selection_request(prompt.text, record, sampled_pages),
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
        evaluations.append(
            {
                "record_id": record["record_id"],
                "source_key": record["source_key"],
                "revision": record["revision"],
                "source_url": record["source_url"],
                "manifest_url": record["record"]["manifest_url"],
                "catalog_record": record["record"],
                "sampled_pages": sampled_pages,
                "result": result,
                "usage": {
                    "requested_model": SELECTION_MODEL,
                    "resolved_model": response.model,
                    "prompt_tokens": response.prompt_tokens,
                    "output_tokens": response.billable_output_tokens,
                    "cost_usd": response_cost,
                },
            }
        )

    recommendations = sorted(
        (entry for entry in evaluations if entry["result"]["verdict"] != "skip"),
        key=lambda entry: (-entry["result"]["score"], entry["source_key"]),
    )[:recommendation_limit]
    flattened = [
        {
            "record_id": entry["record_id"],
            "source_key": entry["source_key"],
            "manifest_url": entry["manifest_url"],
            **entry["result"],
        }
        for entry in recommendations
    ]
    report = {
        "schema_version": 1,
        "created_at": _utc_timestamp(),
        "source_id": source_id,
        "after_source_key": after,
        "requested_records": record_limit,
        "sampled_pages_per_record": page_samples,
        "requested_model": SELECTION_MODEL,
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
        "catalog_selection", manifest_url, manifest, image_size=1200
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


def _selection_request(
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


def _default_output(library_root: Path, source_id: str) -> Path:
    safe_source = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in source_id
    )
    return library_root / "selections" / f"{_utc_compact()}-{safe_source}.json"


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
