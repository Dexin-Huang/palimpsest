"""Factory CLI, wired as ``palimpsest factory ...`` during the greenfield
build. Promoted to the top-level command surface at cutover (FACTORY.md §5.6).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.factory.config import FACTORY_DB_PATH, LIBRARY_ROOT
from palimpsest.factory.core.ledger import Ledger


def add_factory_subparser(subparsers) -> None:
    parser = subparsers.add_parser("factory", help="Factory line (docs/FACTORY.md)")
    factory_subparsers = parser.add_subparsers(dest="factory_command", required=True)

    init_db = factory_subparsers.add_parser(
        "init-db", help="Create the factory ledger database"
    )
    init_db.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    init_db.set_defaults(func=cmd_init_db)

    status = factory_subparsers.add_parser(
        "status", help="Show items on the line, or one item's stage state"
    )
    status.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    status.add_argument("--doc-id", default=None)
    status.set_defaults(func=cmd_status)

    adopt = factory_subparsers.add_parser(
        "adopt", help="Put an existing library document on the line"
    )
    adopt.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    adopt.add_argument("--doc-id", required=True)
    adopt.add_argument("--recipe", required=True)
    adopt.add_argument("--mode", choices=["source", "opportunity"], default="source")
    adopt.set_defaults(func=cmd_adopt)

    run = factory_subparsers.add_parser(
        "run", help="Drive a work order through its recipe"
    )
    run.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    run.add_argument("--doc-id", required=True)
    run.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    run.add_argument("--workers", type=int, default=None)
    run.add_argument(
        "--refresh", action="append", default=[], metavar="STATION",
        help="Force re-run of a station even if fresh/outdated (repeatable)",
    )
    run.add_argument(
        "--executor", choices=["inline", "subprocess"], default="inline",
        help="How cells execute: in-thread, or one isolated process per cell",
    )
    run.set_defaults(func=cmd_run)

    graph = factory_subparsers.add_parser(
        "graph", help="The contract graph (input → transformation → output)"
    )
    graph.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    graph.add_argument("--write-docs", action="store_true",
                       help="Regenerate docs/CONTRACTS.md")
    graph.set_defaults(func=cmd_graph)

    preview = factory_subparsers.add_parser(
        "preview", help="Render preprocessing stages + lassos for given pages"
    )
    preview.add_argument("--doc-id", required=True)
    preview.add_argument("--pages", required=True,
                         help="Comma-separated page ids, e.g. f001r,f002v")
    preview.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    preview.set_defaults(func=cmd_preview)

    site = factory_subparsers.add_parser(
        "site", help="Rebuild the hosted library from all published books"
    )
    site.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    site.add_argument("--site-root", type=Path, default=None)
    site.set_defaults(func=cmd_site)


def cmd_init_db(args: argparse.Namespace) -> None:
    with Ledger(args.db):
        pass
    print(f"Factory ledger ready: {args.db}")


def cmd_adopt(args: argparse.Namespace) -> None:
    with Ledger(args.db) as ledger:
        ledger.adopt(args.doc_id, recipe=args.recipe, mode=args.mode)
    print(f"{args.doc_id} is on the line (recipe={args.recipe}, mode={args.mode})")


def cmd_run(args: argparse.Namespace) -> None:
    from palimpsest.factory.core.conductor import DEFAULT_WORKERS, Conductor

    with Ledger(args.db) as ledger:
        conductor = Conductor(
            ledger,
            library_root=args.library_root,
            workers=args.workers or DEFAULT_WORKERS,
            refresh=frozenset(args.refresh),
            executor=args.executor,
        )
        report = conductor.run(args.doc_id)

    print(f"{report.doc_id} [{report.recipe}]  "
          f"ran={report.count('ran')} fresh={report.count('fresh')} "
          f"outdated={report.count('outdated')} failed={report.count('failed')}  "
          f"cost=${report.cost_usd:.4f}")
    for cell in report.cells:
        if cell.action == "failed":
            print(f"  FAILED {cell.station} {cell.page_id or '(manuscript)'}: {cell.error}")
        elif cell.action == "outdated":
            print(f"  outdated {cell.station} {cell.page_id or '(manuscript)'} "
                  f"— re-run with --refresh {cell.station}")


def cmd_graph(args: argparse.Namespace) -> None:
    from palimpsest.factory import graph

    if args.write_docs:
        print(f"wrote {graph.write_docs()}")
        return
    print(graph.to_mermaid() if args.format == "mermaid" else graph.to_json())


def cmd_preview(args: argparse.Namespace) -> None:
    from palimpsest.factory.preview import build

    written = build(args.doc_id, args.pages.split(","),
                    library_root=args.library_root)
    for path in written:
        print(path)
    if not written:
        print("No artifacts found — run the line (or at least deframe) first.")


def cmd_site(args: argparse.Namespace) -> None:
    from palimpsest.factory.site import DEFAULT_SITE_ROOT, build

    site_root = args.site_root or DEFAULT_SITE_ROOT
    shelved = build(args.library_root, site_root)
    print(f"site/ rebuilt with {len(shelved)} book(s): {', '.join(shelved) or '—'}")
    print(f"open {site_root / 'index.html'}")


def cmd_status(args: argparse.Namespace) -> None:
    with Ledger(args.db) as ledger:
        if args.doc_id is None:
            items = ledger.list_items()
            if not items:
                print("No items on the line.")
                return
            for item in items:
                score = item["triage_score"]
                print(
                    f"{item['doc_id']}  [{item['status']}]  recipe={item['recipe']}"
                    f"  mode={item['mode']}  head={item['head'] or '-'}"
                    f"  triage={score if score is not None else '-'}"
                )
            return

        rows = ledger.state(args.doc_id)
        if not rows:
            print(f"No completed stage runs for {args.doc_id}.")
            return
        for row in rows:
            page = row["page_id"] or "(manuscript)"
            cost = f"  ${row['cost_usd']:.4f}" if row["cost_usd"] is not None else ""
            model = f"  {row['model']}" if row["model"] else ""
            print(
                f"{row['station']:<14} {page:<12} {row['station_version']}"
                f"{model}{cost}  {row['finished_at']}"
            )
