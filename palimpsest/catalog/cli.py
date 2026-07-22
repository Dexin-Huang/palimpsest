"""Command surface for source registration, sync, and inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.heads import build_head
from palimpsest.catalog.sync import sync_source
from palimpsest.factory.config import CATALOG_DB_PATH


def add_catalog_commands(subparsers: argparse._SubParsersAction) -> None:
    catalog = subparsers.add_parser(
        "catalog", help="Harvest and normalize external manuscript catalogs"
    )
    catalog.add_argument("--db", type=Path, default=CATALOG_DB_PATH)
    commands = catalog.add_subparsers(dest="catalog_command", required=True)

    initialize = commands.add_parser("init", help="Initialize the source catalog")
    initialize.set_defaults(func=cmd_init_catalog)

    source = commands.add_parser("source", help="Register or list source heads")
    source_commands = source.add_subparsers(dest="source_command", required=True)

    source_list = source_commands.add_parser("list", help="List registered sources")
    source_list.set_defaults(func=cmd_list_sources)

    add_jsonl = source_commands.add_parser(
        "add-jsonl", help="Register a canonical JSONL source"
    )
    add_jsonl.add_argument("source_id")
    add_jsonl.add_argument("path", type=Path)
    add_jsonl.add_argument("--page-size", type=int, default=100)
    add_jsonl.set_defaults(func=cmd_add_jsonl)

    add_gallica = source_commands.add_parser(
        "add-gallica", help="Register a Gallica SRU query"
    )
    add_gallica.add_argument("source_id")
    add_gallica.add_argument("--query", required=True)
    add_gallica.add_argument("--repository", default="Bibliothèque nationale de France")
    add_gallica.add_argument("--collection", default="Gallica")
    add_gallica.add_argument("--page-size", type=int, default=50)
    add_gallica.add_argument("--minimum-interval", type=float, default=1.0)
    add_gallica.set_defaults(func=cmd_add_gallica)

    synchronize = commands.add_parser("sync", help="Refresh one registered source")
    synchronize.add_argument("source_id")
    synchronize.add_argument("--resume", action="store_true")
    synchronize.set_defaults(func=cmd_sync_catalog)

    stats = commands.add_parser("stats", help="Report source catalog coverage")
    stats.set_defaults(func=cmd_catalog_stats)

    records = commands.add_parser("records", help="Emit normalized records as JSONL")
    records.add_argument("source_id")
    records.add_argument("--limit", type=int, default=100)
    records.set_defaults(func=cmd_catalog_records)


def cmd_init_catalog(args: argparse.Namespace) -> None:
    with CatalogDB(args.db):
        pass
    print(args.db)


def cmd_list_sources(args: argparse.Namespace) -> None:
    with CatalogDB(args.db) as database:
        values = [
            {
                "source_id": source.source_id,
                "head": source.head,
                "enabled": source.enabled,
                "config": dict(source.config),
            }
            for source in database.sources()
        ]
    print(json.dumps(values, ensure_ascii=False, sort_keys=True))


def cmd_add_jsonl(args: argparse.Namespace) -> None:
    path = args.path.resolve()
    if not path.is_file():
        raise ValueError(f"JSONL source does not exist: {path}")
    config = {"path": str(path), "page_size": args.page_size}
    _add_source(args.db, args.source_id, "normalized-jsonl", config)


def cmd_add_gallica(args: argparse.Namespace) -> None:
    config = {
        "query": args.query,
        "repository": args.repository,
        "collection": args.collection,
        "page_size": args.page_size,
        "minimum_interval_seconds": args.minimum_interval,
    }
    _add_source(args.db, args.source_id, "gallica-sru", config)


def cmd_sync_catalog(args: argparse.Namespace) -> None:
    with CatalogDB(args.db) as database:
        result = sync_source(database, args.source_id, resume=args.resume)
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))


def cmd_catalog_stats(args: argparse.Namespace) -> None:
    with CatalogDB(args.db) as database:
        result = database.stats()
    print(json.dumps(result, sort_keys=True))


def cmd_catalog_records(args: argparse.Namespace) -> None:
    with CatalogDB(args.db) as database:
        records = database.records(args.source_id, limit=args.limit)
    for record in records:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def _add_source(
    database_path: Path,
    source_id: str,
    head: str,
    config: dict[str, Any],
) -> None:
    build_head(head, config)
    with CatalogDB(database_path) as database:
        database.add_source(source_id, head, config)
    print(
        json.dumps(
            {"source_id": source_id, "head": head},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
