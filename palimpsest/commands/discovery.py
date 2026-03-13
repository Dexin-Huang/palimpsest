"""Discovery commands - simplified DB-first flow."""

from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.config import DEFAULT_MODEL_SCHOLAR_AGENT, DEFAULT_MODEL_TRIAGE
from palimpsest.discovery.workflow import (
    add_manuscripts,
    enrich_manuscripts,
    ingest_sources,
    list_sources,
    run_scout,
    scrape_sources,
    show_stats,
    triage_manuscripts,
)


def cmd_add(args: argparse.Namespace) -> None:
    add_manuscripts(
        db_path=args.db,
        count=args.count,
        collection=args.collection,
        range_value=args.range_,
        delay=args.delay,
    )


def cmd_triage(args: argparse.Namespace) -> None:
    triage_manuscripts(
        db_path=args.db,
        limit=args.limit,
        force=args.force,
        collections=args.collection,
        manuscript_ids=args.manuscript_id,
        dry_run=args.dry_run,
        model=args.model,
        workers=args.workers,
    )


def cmd_enrich(args: argparse.Namespace) -> None:
    enrich_manuscripts(
        db_path=args.db,
        manifest_dir=Path(args.manifest_dir),
        fetch_missing=args.fetch_missing,
        collections=args.collection,
        manuscript_ids=args.manuscript_id,
    )


def cmd_stats(args: argparse.Namespace) -> None:
    show_stats(db_path=args.db)


def cmd_sources_list(args: argparse.Namespace) -> None:
    list_sources(source=args.source)


def cmd_sources_scrape(args: argparse.Namespace) -> None:
    scrape_sources(
        source=args.source,
        collection=args.collection,
        max_pages=args.max_pages,
        limit=args.limit,
        delay=args.delay,
        include_details=not args.no_details,
        preview=args.preview,
        output=args.output,
    )


def cmd_sources_ingest(args: argparse.Namespace) -> None:
    ingest_sources(
        source=args.source,
        collection=args.collection,
        db_path=args.db,
        max_pages=args.max_pages,
        limit=args.limit,
        delay=args.delay,
        include_details=not args.no_details,
        fetch_manifests=not args.no_manifest_fetch,
        update_existing=args.update_existing,
        run_triage=args.triage,
        force_triage=args.force_triage,
        model=args.model,
        workers=args.workers,
    )


def cmd_scout(args: argparse.Namespace) -> None:
    run_scout(
        db_path=args.db,
        repository=args.repository,
        collection=args.collection,
        min_score=args.min_score,
        limit=args.limit,
        out_dir=args.out_dir,
        model=args.model,
        with_web_search=args.with_web_search,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        max_thinking_tokens=args.max_thinking_tokens,
        permission_mode=args.permission_mode,
    )


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("discovery", help="Discovery utilities")
    sub = parser.add_subparsers(dest="discovery_cmd", required=True)

    add = sub.add_parser("add", help="Add N new manuscripts to DB and triage them")
    add.add_argument("count", type=int, help="Number of new manuscripts to add")
    add.add_argument("--collection", default="Reg.lat", help="Collection (default: Reg.lat)")
    add.add_argument("--range", dest="range_", help="Shelfmark range like 1500-2100")
    add.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    add.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    add.set_defaults(func=cmd_add)

    triage = sub.add_parser("triage", help="Run Gemini triage on manuscripts in DB")
    triage.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    triage.add_argument("--limit", type=int, help="Max items to process")
    triage.add_argument("--force", action="store_true", help="Re-triage already scored items")
    triage.add_argument("--collection", action="append", help="Restrict to collection (repeatable)")
    triage.add_argument("--manuscript-id", action="append", help="Restrict to manuscript id (repeatable)")
    triage.add_argument("--dry-run", action="store_true", help="Show what would be triaged")
    triage.add_argument("--model", default=DEFAULT_MODEL_TRIAGE, help="Gemini model")
    triage.add_argument("--workers", type=int, default=10, help="Parallel workers")
    triage.set_defaults(func=cmd_triage)

    enrich = sub.add_parser("enrich", help="Re-enrich DB from cached manifests")
    enrich.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    enrich.add_argument("--manifest-dir", default="discovery/manifests", help="Manifest cache")
    enrich.add_argument("--fetch-missing", action="store_true", help="Fetch live manifests when cache entries are missing")
    enrich.add_argument("--collection", action="append", help="Restrict to collection (repeatable)")
    enrich.add_argument("--manuscript-id", action="append", help="Restrict to manuscript id (repeatable)")
    enrich.set_defaults(func=cmd_enrich)

    stats = sub.add_parser("stats", help="Show database statistics")
    stats.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    stats.set_defaults(func=cmd_stats)

    scout = sub.add_parser("scout", help="Run the dedicated Claude scouting agent over DB candidates")
    scout.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    scout.add_argument("--repository", help="Restrict candidates to one repository code, e.g. IDP or BAV")
    scout.add_argument("--collection", help="Restrict candidates to one collection key")
    scout.add_argument("--min-score", type=int, default=8, help="Minimum combined interest score")
    scout.add_argument("--limit", type=int, default=12, help="Maximum candidates to include")
    scout.add_argument("--out-dir", help="Explicit scout workspace output directory")
    scout.add_argument("--model", default=DEFAULT_MODEL_SCHOLAR_AGENT, help="Claude model for the scout agent")
    scout.add_argument("--with-web-search", action="store_true", help="Allow WebSearch during scout runs")
    scout.add_argument("--max-turns", type=int, default=60, help="Maximum turns for the scout run")
    scout.add_argument("--max-budget-usd", type=float, default=None, help="Maximum budget in USD")
    scout.add_argument("--max-thinking-tokens", type=int, default=None, help="Maximum thinking tokens per response")
    scout.add_argument("--permission-mode", default="default", choices=["default", "plan"], help="Claude permission mode")
    scout.set_defaults(func=cmd_scout)

    sources = sub.add_parser("sources", help="List or scrape curated discovery sources")
    sources_sub = sources.add_subparsers(dest="sources_cmd", required=True)

    sources_list = sources_sub.add_parser("list", help="List registered discovery sources")
    sources_list.add_argument("--source", help="Restrict to one source id")
    sources_list.set_defaults(func=cmd_sources_list)

    sources_scrape = sources_sub.add_parser("scrape", help="Scrape one curated source collection into JSONL refs")
    sources_scrape.add_argument("--source", required=True, help="Source adapter id")
    sources_scrape.add_argument("--collection", required=True, help="Curated collection key")
    sources_scrape.add_argument("--max-pages", type=int, default=1, help="Max result pages")
    sources_scrape.add_argument("--limit", type=int, help="Max refs to return")
    sources_scrape.add_argument("--delay", type=float, default=0.2, help="Delay between requests")
    sources_scrape.add_argument("--no-details", action="store_true", help="Skip per-record detail fetches")
    sources_scrape.add_argument("--preview", type=int, default=5, help="How many refs to print")
    sources_scrape.add_argument("--output", help="Optional JSONL output path")
    sources_scrape.set_defaults(func=cmd_sources_scrape)

    sources_ingest = sources_sub.add_parser(
        "ingest",
        help="Scrape one curated source collection into the discovery DB and optionally triage it",
    )
    sources_ingest.add_argument("--source", required=True, help="Source adapter id")
    sources_ingest.add_argument("--collection", required=True, help="Curated collection key")
    sources_ingest.add_argument("--db", default="discovery/manuscripts.db", help="Database path")
    sources_ingest.add_argument("--max-pages", type=int, default=1, help="Max result pages")
    sources_ingest.add_argument("--limit", type=int, help="Max refs to ingest")
    sources_ingest.add_argument("--delay", type=float, default=0.2, help="Delay between requests")
    sources_ingest.add_argument("--no-details", action="store_true", help="Skip per-record detail fetches")
    sources_ingest.add_argument("--no-manifest-fetch", action="store_true", help="Do not fetch manifests for canvas counts")
    sources_ingest.add_argument("--update-existing", action="store_true", help="Update existing manuscript records")
    sources_ingest.add_argument("--triage", action="store_true", help="Run triage after ingest")
    sources_ingest.add_argument("--force-triage", action="store_true", help="Re-triage items even if already scored")
    sources_ingest.add_argument("--model", default=DEFAULT_MODEL_TRIAGE, help="Gemini model")
    sources_ingest.add_argument("--workers", type=int, default=10, help="Parallel triage workers")
    sources_ingest.set_defaults(func=cmd_sources_ingest)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Discovery commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparser(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
