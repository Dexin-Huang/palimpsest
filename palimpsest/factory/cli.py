"""The Palimpsest command surface.

Factory commands live at the top level: ``palimpsest run``, not behind a
transitional namespace. Command handlers import heavyweight stations lazily so
basic inventory operations remain fast.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from palimpsest.factory.config import FACTORY_DB_PATH, LIBRARY_ROOT
from palimpsest.factory.core.ledger import Ledger


def add_commands(subparsers) -> None:
    init_db = subparsers.add_parser(
        "init-db", help="Create the factory ledger database"
    )
    status = subparsers.add_parser(
        "status", help="Show items on the line, or one item's stage state"
    )
    intake = subparsers.add_parser(
        "intake", help="Create a work order from an IIIF manifest"
    )
    adopt = subparsers.add_parser(
        "adopt", help="Put an existing library document on the line"
    )
    run = subparsers.add_parser("run", help="Drive a work order through its recipe")
    graph = subparsers.add_parser(
        "graph", help="The contract graph (input → transformation → output)"
    )
    preview = subparsers.add_parser(
        "preview", help="Render preprocessing stages + lassos for given pages"
    )
    tune = subparsers.add_parser(
        "tune",
        help="Offline lasso tuning: compute the CV chain in memory, "
        "render strips, score routing (no ledger, no network)",
    )
    evaluate = subparsers.add_parser(
        "evaluate",
        help="Compare factory transcriptions against a reference "
        "JSONL: contamination/repetition metrics + optional "
        "blind pairwise image judge",
    )
    site = subparsers.add_parser(
        "site", help="Rebuild the hosted library from all published books"
    )

    for parser in (init_db, status, intake, adopt, run):
        parser.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    for parser in (intake, adopt, run, preview, tune, evaluate):
        parser.add_argument("--doc-id", required=True)

    status.add_argument("--doc-id", default=None)

    intake.add_argument("--manifest", required=True)
    intake.add_argument("--recipe", required=True)
    intake.add_argument("--image-size", default="max")
    intake.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)

    adopt.add_argument("--recipe", required=True)
    adopt.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)

    run.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    run.add_argument("--workers", type=int, default=None)
    run.add_argument(
        "--refresh",
        action="append",
        default=[],
        metavar="STATION",
        help="Force re-run of a station even if fresh/outdated (repeatable)",
    )
    run.add_argument(
        "--executor",
        choices=["inline", "subprocess"],
        default="inline",
        help="How cells execute: in-thread, or one isolated process per cell",
    )

    graph.add_argument("--format", choices=["mermaid", "json"], default="mermaid")
    graph.add_argument(
        "--write-docs", action="store_true", help="Regenerate docs/CONTRACTS.md"
    )

    preview.add_argument(
        "--pages", required=True, help="Comma-separated page ids, e.g. f001r,f002v"
    )
    preview.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)

    tune.add_argument("--pages", required=True)
    tune.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    tune.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="transcriptions.jsonl for routing sanity checks",
    )

    evaluate.add_argument("--reference", type=Path, required=True)
    evaluate.add_argument("--pages", required=True)
    evaluate.add_argument("--judge-model", default=None)
    evaluate.add_argument(
        "--image-doc-id",
        default=None,
        help="Doc holding the page images (defaults to --doc-id)",
    )
    evaluate.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)

    site.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    site.add_argument("--site-root", type=Path, default=None)

    for parser, handler in (
        (init_db, cmd_init_db),
        (status, cmd_status),
        (intake, cmd_intake),
        (adopt, cmd_adopt),
        (run, cmd_run),
        (graph, cmd_graph),
        (preview, cmd_preview),
        (tune, cmd_tune),
        (evaluate, cmd_evaluate),
        (site, cmd_site),
    ):
        parser.set_defaults(func=handler)


def _page_ids(value: str) -> list[str]:
    page_ids = [page_id.strip() for page_id in value.split(",") if page_id.strip()]
    if not page_ids:
        raise ValueError("--pages must name at least one page")
    return page_ids


def cmd_init_db(args: argparse.Namespace) -> None:
    with Ledger(args.db):
        pass
    print(f"Factory ledger ready: {args.db}")


def cmd_intake(args: argparse.Namespace) -> None:
    from palimpsest.factory.core.recipe import load as load_recipe
    from palimpsest.factory.intake import build_records, fetch_manifest, write_records

    recipe = load_recipe(args.recipe)
    image_size = (
        int(args.image_size) if str(args.image_size).isdigit() else args.image_size
    )
    manifest = fetch_manifest(args.manifest)
    metadata, page_list = build_records(
        args.doc_id, args.manifest, manifest, image_size=image_size
    )
    with Ledger(args.db) as ledger:
        if ledger.item(args.doc_id) is not None:
            raise ValueError(f"Work order already exists: {args.doc_id}")
        write_records(args.doc_id, metadata, page_list, library_root=args.library_root)
        ledger.adopt(args.doc_id, recipe=recipe.name)
    print(
        f"{args.doc_id} is on the line "
        f"(recipe={recipe.name}, pages={len(page_list['pages'])})"
    )


def cmd_adopt(args: argparse.Namespace) -> None:
    from palimpsest.factory.core.recipe import load as load_recipe
    from palimpsest.factory.intake import validate_records
    from palimpsest.factory.workspace.io import read_json
    from palimpsest.factory.workspace.layout import metadata_path, page_list_path

    recipe = load_recipe(args.recipe)
    metadata = read_json(metadata_path(args.doc_id, args.library_root))
    page_list = read_json(page_list_path(args.doc_id, args.library_root))
    validate_records(args.doc_id, metadata, page_list)
    with Ledger(args.db) as ledger:
        if ledger.item(args.doc_id) is not None:
            raise ValueError(f"Work order already exists: {args.doc_id}")
        ledger.adopt(args.doc_id, recipe=recipe.name)
    print(f"{args.doc_id} is on the line (recipe={recipe.name})")


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

    print(
        f"{report.doc_id} [{report.recipe}]  "
        f"ran={report.count('ran')} fresh={report.count('fresh')} "
        f"outdated={report.count('outdated')} failed={report.count('failed')}  "
        f"cost=${report.cost_usd:.4f}"
    )
    for cell in report.cells:
        if cell.action == "failed":
            print(
                f"  FAILED {cell.station} {cell.page_id or '(manuscript)'}: {cell.error}"
            )
        elif cell.action == "outdated":
            print(
                f"  outdated {cell.station} {cell.page_id or '(manuscript)'} "
                f"— re-run with --refresh {cell.station}"
            )


def cmd_graph(args: argparse.Namespace) -> None:
    from palimpsest.factory import graph

    if args.write_docs:
        print(f"wrote {graph.write_docs()}")
        return
    print(graph.to_mermaid() if args.format == "mermaid" else graph.to_json())


def cmd_preview(args: argparse.Namespace) -> None:
    from palimpsest.factory.preview import build

    written = build(args.doc_id, _page_ids(args.pages), library_root=args.library_root)
    for path in written:
        print(path)
    if not written:
        print("No artifacts found — run the line (or at least deframe) first.")


def cmd_tune(args: argparse.Namespace) -> None:
    from palimpsest.factory.preview import DEFAULT_OUT_DIR, tune

    rows = tune(
        args.doc_id,
        _page_ids(args.pages),
        library_root=args.library_root,
        reference=args.reference,
    )
    header = ["page_id", "route", "regions", "main", "margin", "glyph", "lines"]
    if args.reference:
        header += ["ref_chars", "verdict"]
    print("  ".join(f"{h:>10}" for h in header))
    for row in rows:
        print("  ".join(f"{str(row.get(h, '')):>10}" for h in header))
    print(f"strips: {DEFAULT_OUT_DIR / args.doc_id}")


def cmd_evaluate(args: argparse.Namespace) -> None:
    import sys

    from palimpsest.factory.evaluate import evaluate, render_table

    # judge reasoning may contain characters the Windows console can't encode
    sys.stdout.reconfigure(errors="replace")

    results = evaluate(
        args.doc_id,
        args.reference,
        _page_ids(args.pages),
        library_root=args.library_root,
        judge_model=args.judge_model,
        image_doc_id=args.image_doc_id,
    )
    print(render_table(results))
    for result in results:
        if result.judge_reasoning:
            print(
                f"\n[{result.page_id}] judge ({result.judge_winner}, "
                f"{result.judge_confidence}): {result.judge_reasoning}"
            )


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
                print(f"{item['doc_id']}  [{item['status']}]  recipe={item['recipe']}")
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
                f"{row['station']:<14} {page:<12} {row['station_fingerprint']}"
                f"{model}{cost}  {row['finished_at']}"
            )
