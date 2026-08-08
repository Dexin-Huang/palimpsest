"""The Palimpsest command surface.

Factory commands live at the top level: ``palimpsest run``, not behind a
transitional namespace. Command handlers import heavyweight stations lazily so
basic inventory operations remain fast.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from palimpsest.catalog.database import CATALOG_DB_PATH
from palimpsest.factory.config import (
    FACTORY_DB_PATH,
    FACTORY_ROOT,
    LIBRARY_ROOT,
    MODEL_PROVIDER_WORKERS,
)
from palimpsest.factory.core.ledger import Ledger


def add_commands(subparsers) -> None:
    init_db = subparsers.add_parser(
        "init-db", help="Create the factory ledger database"
    )
    status = subparsers.add_parser(
        "status", help="Show items on the line, or one item's stage state"
    )
    park = subparsers.add_parser(
        "park", help="Park a work order without deleting its production history"
    )
    intake = subparsers.add_parser(
        "intake", help="Create a work order from an IIIF manifest"
    )
    adopt = subparsers.add_parser(
        "adopt", help="Put an existing library document on the line"
    )
    run = subparsers.add_parser("run", help="Drive a work order through its recipe")
    doctor = subparsers.add_parser(
        "doctor", help="Check authoritative state, products, and recipes"
    )
    snapshot = subparsers.add_parser(
        "snapshot",
        help="Create, verify, or restore a content-verified library snapshot",
    )
    _add_snapshot_commands(snapshot)
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
    site = subparsers.add_parser(
        "site", help="Rebuild the hosted library from all published books"
    )
    export_library_command = subparsers.add_parser(
        "export-library",
        help="Export validated books and reader assets without a presentation layer",
    )
    publish_library = subparsers.add_parser(
        "publish",
        help="Export and publish an immutable library release to object storage",
    )

    for parser in (init_db, status, park, intake, adopt, run, doctor):
        parser.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    for parser in (park, intake, adopt, preview, tune):
        parser.add_argument("--doc-id", required=True)

    status.add_argument("--doc-id", default=None)
    status.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    doctor.add_argument("--catalog-db", type=Path, default=CATALOG_DB_PATH)
    doctor.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    doctor.add_argument("--recipes-root", type=Path, default=FACTORY_ROOT / "recipes")
    doctor.add_argument("--json", action="store_true")

    intake.add_argument("--manifest", required=True)
    intake.add_argument("--recipe", required=True)
    intake.add_argument("--image-size", default="max")
    intake.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)

    adopt.add_argument("--recipe", required=True)
    adopt.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    adopt.add_argument(
        "--switch",
        action="store_true",
        help="Update the recipe of an existing work order",
    )

    run.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    run_target = run.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--doc-id")
    run_target.add_argument(
        "--active",
        action="store_true",
        help="Run a bounded queue of active work orders in creation order",
    )
    run.add_argument(
        "--limit",
        type=_positive_int,
        default=1,
        help="Maximum active work orders to run in queue mode (default: 1)",
    )
    run.add_argument(
        "--max-total-cost",
        type=_nonnegative_float,
        default=None,
        metavar="USD",
        help="Queue dispatch ceiling checked between work orders",
    )
    run.add_argument(
        "--workers",
        type=_positive_int,
        default=None,
        help="Maximum concurrent local page cells (default: 6)",
    )
    run.add_argument(
        "--model-workers",
        type=_positive_int,
        default=None,
        help="Maximum concurrent model-backed page cells "
        "(default: min(--workers, PALIMPSEST_MODEL_PROVIDER_WORKERS))",
    )
    run.add_argument(
        "--refresh",
        action="append",
        default=[],
        metavar="STATION",
        help="Force re-run of a station even if fresh/outdated (repeatable)",
    )
    run.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="PAGE_ID",
        help="Run only this page (repeatable; cannot cross a manuscript barrier)",
    )
    run.add_argument(
        "--through",
        metavar="STATION",
        help="Stop after this station, inclusive",
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

    site.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    site.add_argument("--site-root", type=Path, default=None)
    for parser in (export_library_command, publish_library):
        parser.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    export_library_command.add_argument("--output", type=Path, required=True)
    publish_library.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Local bundle path (default: repository publication directory)",
    )
    publish_library.add_argument("--bucket", required=True)
    publish_library.add_argument("--profile", required=True)
    publish_library.add_argument("--endpoint-url", required=True)
    publish_library.add_argument(
        "--public-base-url",
        default=None,
        help="Public origin used to print the downstream import URL",
    )

    for parser, handler in (
        (init_db, cmd_init_db),
        (status, cmd_status),
        (park, cmd_park),
        (doctor, cmd_doctor),
        (intake, cmd_intake),
        (adopt, cmd_adopt),
        (run, cmd_run),
        (graph, cmd_graph),
        (preview, cmd_preview),
        (tune, cmd_tune),
        (site, cmd_site),
        (export_library_command, cmd_export_library),
        (publish_library, cmd_publish_library),
    ):
        parser.set_defaults(func=handler)


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
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def _add_snapshot_commands(snapshot: argparse.ArgumentParser) -> None:
    commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = commands.add_parser(
        "create", help="Write one atomic snapshot from quiescent library state"
    )
    create.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    create.add_argument("--factory-db", type=Path, default=FACTORY_DB_PATH)
    create.add_argument("--catalog-db", type=Path, default=CATALOG_DB_PATH)
    create.add_argument("--output", type=Path, required=True)
    create.set_defaults(func=cmd_snapshot_create)

    verify = commands.add_parser(
        "verify", help="Verify every snapshot payload and SQLite backup"
    )
    verify.add_argument("archive", type=Path)
    verify.set_defaults(func=cmd_snapshot_verify)

    restore = commands.add_parser(
        "restore", help="Restore a verified snapshot into a new library root"
    )
    restore.add_argument("archive", type=Path)
    restore.add_argument("--output", type=Path, required=True)
    restore.set_defaults(func=cmd_snapshot_restore)


def _page_ids(value: str) -> list[str]:
    page_ids = [page_id.strip() for page_id in value.split(",") if page_id.strip()]
    if not page_ids:
        raise ValueError("--pages must name at least one page")
    return page_ids


def cmd_init_db(args: argparse.Namespace) -> None:
    with Ledger(args.db):
        pass
    print(f"Factory ledger ready: {args.db}")


def cmd_doctor(args: argparse.Namespace) -> None:
    from palimpsest.factory.health import inspect_factory

    report = inspect_factory(
        factory_db=args.db,
        catalog_db=args.catalog_db,
        library_root=args.library_root,
        recipes_root=args.recipes_root,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        for check in report["checks"]:
            print(
                f"{check['status'].upper():<4}  {check['name']:<24}  {check['detail']}"
            )
        print(f"doctor: {report['status']}")
    if report["status"] == "fail":
        raise SystemExit(1)


def cmd_snapshot_create(args: argparse.Namespace) -> None:
    from palimpsest.factory.snapshot import create_snapshot

    result = create_snapshot(
        args.library_root,
        args.output,
        database_paths=(args.factory_db, args.catalog_db),
    )
    print(json.dumps(result, sort_keys=True))


def cmd_snapshot_verify(args: argparse.Namespace) -> None:
    from palimpsest.factory.snapshot import verify_snapshot

    print(json.dumps(verify_snapshot(args.archive), sort_keys=True))


def cmd_snapshot_restore(args: argparse.Namespace) -> None:
    from palimpsest.factory.snapshot import restore_snapshot

    print(json.dumps(restore_snapshot(args.archive, args.output), sort_keys=True))


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
    if args.switch:
        with Ledger(args.db) as ledger:
            ledger.switch_recipe(args.doc_id, recipe.name)
        print(f"{args.doc_id} recipe switched to {recipe.name}")
        return
    metadata = read_json(metadata_path(args.doc_id, args.library_root))
    page_list = read_json(page_list_path(args.doc_id, args.library_root))
    validate_records(args.doc_id, metadata, page_list)
    with Ledger(args.db) as ledger:
        if ledger.item(args.doc_id) is not None:
            raise ValueError(f"Work order already exists: {args.doc_id}")
        ledger.adopt(args.doc_id, recipe=recipe.name)
    print(f"{args.doc_id} is on the line (recipe={recipe.name})")


def cmd_run(args: argparse.Namespace) -> None:
    if args.doc_id is not None:
        if args.max_total_cost is not None or args.limit != 1:
            raise ValueError("--limit and --max-total-cost apply only with --active")
        _print_run_report(_drive_work_order(args, args.doc_id))
        return

    if args.refresh or args.page or args.through:
        raise ValueError("queue mode rejects --refresh, --page, and --through")
    if args.max_total_cost is None:
        raise ValueError("--active requires --max-total-cost")
    if args.max_total_cost == 0:
        print("queue: cost ceiling is zero; no work dispatched")
        return

    with Ledger(args.db) as ledger:
        doc_ids = [
            item["doc_id"] for item in ledger.list_items() if item["status"] == "active"
        ][: args.limit]
    if not doc_ids:
        print("queue: no active work orders")
        return

    total_cost = 0.0
    completed = 0
    for doc_id in doc_ids:
        if total_cost >= args.max_total_cost:
            print(
                f"queue stopped at observed cost ${total_cost:.4f}; "
                f"ceiling=${args.max_total_cost:.4f}"
            )
            break
        report = _drive_work_order(args, doc_id)
        _print_run_report(report)
        if report.count("failed"):
            raise RuntimeError(f"queue stopped after failed work order {doc_id}")
        if report.cost_usd is None:
            raise RuntimeError(f"queue stopped after {doc_id} returned unknown cost")
        total_cost += report.cost_usd
        completed += 1
    print(
        f"queue: completed={completed}/{len(doc_ids)} "
        f"known_cost=${total_cost:.4f} ceiling=${args.max_total_cost:.4f}"
    )


def _drive_work_order(args: argparse.Namespace, doc_id: str):
    from palimpsest.factory.core.conductor import DEFAULT_WORKERS, Conductor

    workers = args.workers or DEFAULT_WORKERS
    model_workers = (
        args.model_workers
        if args.model_workers is not None
        else min(workers, MODEL_PROVIDER_WORKERS)
    )
    with Ledger(args.db) as ledger:
        conductor = Conductor(
            ledger,
            library_root=args.library_root,
            workers=workers,
            model_workers=model_workers,
            refresh=frozenset(args.refresh),
            executor=args.executor,
            page_ids=tuple(args.page),
            through=args.through,
        )
        return conductor.run(doc_id)


def _print_run_report(report) -> None:
    cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
    scope = "partial" if report.partial else "complete"
    print(
        f"{report.doc_id} [{report.recipe}]  scope={scope} "
        f"ran={report.count('ran')} fresh={report.count('fresh')} "
        f"outdated={report.count('outdated')} failed={report.count('failed')}  "
        f"cost={cost}"
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


def cmd_export_library(args: argparse.Namespace) -> None:
    from palimpsest.factory.publication_bundle import export_library

    library = export_library(args.library_root, args.output)
    doc_ids = [book.doc_id for book in library.books]
    print(
        f"publication Library rebuilt with {len(doc_ids)} Book object(s): "
        f"{', '.join(doc_ids) or '—'}"
    )
    print(f"{library.bundle_id}  {args.output / 'library.json'}")


def cmd_publish_library(args: argparse.Namespace) -> None:
    from palimpsest.factory.publication_bundle import (
        DEFAULT_BUNDLE_ROOT,
        export_library,
    )
    from palimpsest.factory.publication_store import publish_bundle

    output = args.output or DEFAULT_BUNDLE_ROOT
    library = export_library(args.library_root, output)
    release = publish_bundle(
        output,
        bundle_id=library.bundle_id,
        bucket=args.bucket,
        profile=args.profile,
        endpoint_url=args.endpoint_url,
        public_base_url=args.public_base_url,
    )
    print(f"{release.bundle_id}  {release.object_uri}")
    if release.public_url is not None:
        print(release.public_url)


def cmd_site(args: argparse.Namespace) -> None:
    from palimpsest.factory.site import DEFAULT_SITE_ROOT, build

    site_root = args.site_root or DEFAULT_SITE_ROOT
    shelved = build(args.library_root, site_root)
    print(f"site/ rebuilt with {len(shelved)} book(s): {', '.join(shelved) or '—'}")
    print(f"open {site_root / 'index.html'}")


def cmd_park(args: argparse.Namespace) -> None:
    with Ledger(args.db) as ledger:
        ledger.set_item_status(args.doc_id, "parked")
    print(f"{args.doc_id} [parked] — production history preserved")


def cmd_status(args: argparse.Namespace) -> None:
    from palimpsest.factory.health import terminal_product_status

    with Ledger(args.db) as ledger:
        if args.doc_id is None:
            items = ledger.list_items()
            if not items:
                print("No items on the line.")
                return
            for item in items:
                product = terminal_product_status(
                    item["doc_id"], item["status"], args.library_root
                )
                print(
                    f"{item['doc_id']}  [{item['status']}]  "
                    f"recipe={item['recipe']}  product={product}"
                )
            return

        item = ledger.item(args.doc_id)
        if item is None:
            print(f"No work order for {args.doc_id}.")
            return
        product = terminal_product_status(
            item["doc_id"], item["status"], args.library_root
        )
        print(
            f"{item['doc_id']}  [{item['status']}]  "
            f"recipe={item['recipe']}  product={product}"
        )

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
