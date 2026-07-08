"""Factory CLI, wired as ``palimpsest factory ...`` during the greenfield
build. Promoted to the top-level command surface at cutover (FACTORY.md §5.6).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.factory.config import FACTORY_DB_PATH
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


def cmd_init_db(args: argparse.Namespace) -> None:
    with Ledger(args.db):
        pass
    print(f"Factory ledger ready: {args.db}")


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
