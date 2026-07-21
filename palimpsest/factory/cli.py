"""The Palimpsest command surface.

Factory commands live at the top level: ``palimpsest run``, not behind a
transitional namespace. Command handlers import heavyweight stations lazily so
basic inventory operations remain fast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from palimpsest.factory.config import (
    FACTORY_DB_PATH,
    FACTORY_ROOT,
    LIBRARY_ROOT,
)
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
    site = subparsers.add_parser(
        "site", help="Rebuild the hosted library from all published books"
    )
    bench = subparsers.add_parser(
        "bench", help="Verify, run, report, and promote immutable evaluations"
    )
    _add_bench_commands(bench)

    for parser in (init_db, status, intake, adopt, run):
        parser.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    for parser in (intake, adopt, run, preview, tune):
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
        (site, cmd_site),
    ):
        parser.set_defaults(func=handler)


_CANDIDATES_ROOT = FACTORY_ROOT / "candidates"
_JUDGES_ROOT = FACTORY_ROOT / "judges"
_SUITES_ROOT = FACTORY_ROOT / "evaluation" / "suites"
_EVALUATION_ASSETS_ROOT = FACTORY_ROOT / "evaluation"
_RUNS_ROOT = LIBRARY_ROOT / "evaluations" / "runs"
_OBJECT_ROOT = LIBRARY_ROOT / "evaluations" / "objects"


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


def _add_record_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidates-root", type=Path, default=_CANDIDATES_ROOT)
    parser.add_argument("--judges-root", type=Path, default=_JUDGES_ROOT)
    parser.add_argument("--suites-root", type=Path, default=_SUITES_ROOT)
    parser.add_argument("--asset-root", type=Path, default=_EVALUATION_ASSETS_ROOT)
    parser.add_argument("--object-root", type=Path, default=_OBJECT_ROOT)


def _add_bench_commands(bench: argparse.ArgumentParser) -> None:
    commands = bench.add_subparsers(dest="bench_command", required=True)

    list_command = commands.add_parser(
        "list", help="Inventory tracked records and indexed evaluation history"
    )
    list_command.add_argument("--station", default=None)
    list_command.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    _add_record_roots(list_command)
    list_command.set_defaults(func=cmd_bench_list)

    verify = commands.add_parser(
        "verify", help="Resolve tracked records and indexed reports without execution"
    )
    verify.add_argument("--suite", type=Path, required=True)
    verify.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    _add_record_roots(verify)
    verify.set_defaults(func=cmd_bench_verify)

    fetch = commands.add_parser(
        "fetch", help="Fetch declared source assets into the content-addressed cache"
    )
    fetch.add_argument("--suite", type=Path, required=True)
    fetch.add_argument("--judges-root", type=Path, default=_JUDGES_ROOT)
    fetch.add_argument("--asset-root", type=Path, default=_EVALUATION_ASSETS_ROOT)
    fetch.add_argument("--object-root", type=Path, default=_OBJECT_ROOT)
    fetch.set_defaults(func=cmd_bench_fetch)

    run = commands.add_parser("run", help="Run one isolated paired evaluation")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--baseline", type=Path, required=True)
    run.add_argument("--challenger", type=Path, required=True)
    run_identity = run.add_mutually_exclusive_group(required=True)
    run_identity.add_argument("--run-id")
    run_identity.add_argument("--resume", metavar="RUN")
    run.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    run.add_argument("--runs-root", type=Path, default=_RUNS_ROOT)
    run.add_argument("--asset-root", type=Path, default=_EVALUATION_ASSETS_ROOT)
    run.add_argument("--object-root", type=Path, default=_OBJECT_ROOT)
    run.add_argument("--cases", nargs="+", default=None, metavar="CASE")
    run.add_argument("--max-cost", type=_nonnegative_float, default=None, metavar="USD")
    run.add_argument(
        "--executor", choices=["inline", "subprocess"], default="subprocess"
    )
    run.add_argument("--workers", type=_positive_int, default=1)
    run.set_defaults(func=cmd_bench_run)

    report = commands.add_parser("report", help="Read one canonical indexed report")
    report.add_argument("run")
    report.add_argument("--format", choices=["table", "json"], default="table")
    report.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    report.set_defaults(func=cmd_bench_report)

    propose = commands.add_parser(
        "propose", help="Create an immutable qualified recipe proposal"
    )
    propose.add_argument("run")
    propose.add_argument("--recipe", required=True)
    propose.add_argument("--recipe-root", type=Path, required=True)
    propose.add_argument("--baseline", type=Path, required=True)
    propose.add_argument("--challenger", type=Path, required=True)
    propose.add_argument("--waiver", type=Path, default=None)
    propose.add_argument("--output", type=Path, required=True)
    propose.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    propose.set_defaults(func=cmd_bench_propose)

    promote = commands.add_parser(
        "promote", help="Apply and record one explicitly approved proposal"
    )
    promote.add_argument("proposal", type=Path)
    promote.add_argument("--recipe-root", type=Path, required=True)
    canary_source = promote.add_mutually_exclusive_group(required=True)
    canary_source.add_argument("--canary", metavar="DOC")
    canary_source.add_argument("--canary-evidence", type=Path)
    promote.add_argument("--canary-evidence-output", type=Path, default=None)
    promote.add_argument("--library-root", type=Path, default=LIBRARY_ROOT)
    promote.add_argument("--canary-root", type=Path, default=None)
    promote.add_argument(
        "--executor", choices=["inline", "subprocess"], default="subprocess"
    )
    promote.add_argument("--workers", type=_positive_int, default=1)
    promote.add_argument("--approved-by", required=True)
    promote.add_argument("--history-root", type=Path, required=True)
    promote.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    promote.set_defaults(func=cmd_bench_promote)

    rollback = commands.add_parser(
        "rollback", help="Restore the exact candidate named by a promotion"
    )
    rollback.add_argument("promotion", type=Path)
    rollback.add_argument("--recipe-root", type=Path, required=True)
    rollback.add_argument("--current", type=Path, required=True)
    rollback.add_argument("--previous", type=Path, required=True)
    rollback.add_argument("--approved-by", required=True)
    rollback.add_argument("--history-root", type=Path, required=True)
    rollback.add_argument("--proposal-output", type=Path, default=None)
    rollback.add_argument("--canary-evidence", type=Path, default=None)
    rollback.add_argument("--db", type=Path, default=FACTORY_DB_PATH)
    rollback.set_defaults(func=cmd_bench_rollback)


def _page_ids(value: str) -> list[str]:
    page_ids = [page_id.strip() for page_id in value.split(",") if page_id.strip()]
    if not page_ids:
        raise ValueError("--pages must name at least one page")
    return page_ids


def cmd_init_db(args: argparse.Namespace) -> None:
    with Ledger(args.db):
        pass
    print(f"Factory ledger ready: {args.db}")


def _tracked_yaml(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            (
                *root.rglob("*.yaml"),
                *root.rglob("*.yml"),
            ),
            key=lambda path: path.as_posix(),
        )
    )


def _trusted_resolvers(
    judges_root: Path,
) -> tuple[object, Mapping[str, object], Mapping[str, object]]:
    from palimpsest.factory.evaluation.judge import load_judge
    from palimpsest.factory.evaluation.metrics import MetricRegistry
    from palimpsest.factory.evaluation.station_metrics import register_station_metrics
    from palimpsest.factory.evaluation.response_schemas import trusted_response_schemas

    metrics = MetricRegistry()
    register_station_metrics(metrics)
    response_schemas = trusted_response_schemas()
    judges = {
        judge.id: judge
        for path in _tracked_yaml(judges_root)
        for judge in (load_judge(path, response_schema_resolver=response_schemas),)
    }
    return metrics, {}, judges


def _resolve_suite(
    path: Path,
    *,
    judges_root: Path,
    asset_root: Path,
    verify_local: bool = True,
) -> object:
    from palimpsest.factory.evaluation.suite import load_suite

    metrics, probes, judges = _trusted_resolvers(judges_root)
    return load_suite(
        path,
        metric_resolver=metrics,
        probe_resolver=probes,
        judge_resolver=judges,
        asset_root=asset_root,
        verify_local=verify_local,
    )


def _resolve_candidates(root: Path) -> tuple[object, ...]:
    from palimpsest.factory.evaluation.candidate import load_candidate

    return tuple(load_candidate(path) for path in _tracked_yaml(root))


def _resolve_suites(
    root: Path,
    *,
    judges_root: Path,
    asset_root: Path,
) -> tuple[object, ...]:
    return tuple(
        _resolve_suite(path, judges_root=judges_root, asset_root=asset_root)
        for path in _tracked_yaml(root)
    )


def _verify_source_objects(cases: Sequence[object], object_root: Path) -> int:
    from palimpsest.factory.evaluation.suite import CaseAsset

    checked = 0

    def verify(value: object) -> None:
        nonlocal checked
        if isinstance(value, Mapping):
            for nested in value.values():
                verify(nested)
            return
        if not isinstance(value, CaseAsset) or value.source is None:
            return
        path = object_root / value.sha256
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 16), b""):
                    digest.update(chunk)
        except OSError as error:
            raise ValueError(
                f"Missing external object {value.sha256}; run `palimpsest bench fetch`"
            ) from error
        if digest.hexdigest() != value.sha256:
            raise ValueError(f"External object hash mismatch: {path}")
        checked += 1

    for case in cases:
        verify(case.inputs)
        verify(case.references)
    return checked


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label} JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _indexed_report(store: object, run_id: str) -> dict[str, Any]:
    from palimpsest.factory.evaluation.report import report_fingerprint

    run = store.run(run_id)
    if run is None:
        raise ValueError(f"Unknown evaluation run: {run_id}")
    if run.report_path is None or run.report_fingerprint is None:
        raise ValueError(f"Evaluation run has no completed report: {run_id}")
    report = _read_json_object(Path(run.report_path), label="evaluation report")
    claimed = report.get("report_fingerprint")
    if (
        report.get("run_id") != run_id
        or claimed != run.report_fingerprint
        or report_fingerprint(report) != claimed
    ):
        raise ValueError(f"Indexed evaluation report is no longer canonical: {run_id}")
    return report


def _print_rows(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    rendered = [
        ["unknown" if value is None else str(value) for value in row] for row in rows
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in rendered))
        for index, header in enumerate(headers)
    ]
    print(
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    )
    for row in rendered:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _report_table(report: Mapping[str, object]) -> str:
    identity_fields = (
        ("run", report.get("run_id")),
        ("status", report.get("status")),
        ("decision", report.get("decision")),
    )
    rows: list[tuple[object, ...]] = list(identity_fields)
    for name in ("suite", "baseline", "challenger"):
        identity = report.get(name)
        if isinstance(identity, Mapping):
            rows.append((name, identity.get("id")))
            rows.append((f"{name}_fingerprint", identity.get("fingerprint")))
        else:
            rows.append((name, None))
    rows.extend(
        (
            ("started_at", report.get("started_at")),
            ("finished_at", report.get("finished_at")),
            ("report_fingerprint", report.get("report_fingerprint")),
        )
    )
    rendered = [
        (str(name), "unknown" if value is None else str(value)) for name, value in rows
    ]
    width = max(len(name) for name, _ in rendered)
    lines = [f"{name.ljust(width)}  {value}" for name, value in rendered]
    cases = report.get("cases")
    if isinstance(cases, list) and cases:
        lines.extend(("", "case  side  succeeded  latency_seconds  cost_usd  error"))
        for case in cases:
            if not isinstance(case, Mapping):
                continue
            for side_name in ("baseline", "challenger"):
                side = case.get(side_name)
                if not isinstance(side, Mapping):
                    continue
                values = (
                    case.get("case_id"),
                    side_name,
                    side.get("succeeded"),
                    side.get("latency_seconds"),
                    side.get("cost_usd"),
                    side.get("error_kind"),
                )
                lines.append(
                    "  ".join(
                        "unknown" if value is None else str(value) for value in values
                    )
                )
    return "\n".join(lines)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def cmd_bench_list(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.store import EvaluationStore

    candidates = _resolve_candidates(args.candidates_root)
    suites = _resolve_suites(
        args.suites_root,
        judges_root=args.judges_root,
        asset_root=args.asset_root,
    )
    _, _, judges = _trusted_resolvers(args.judges_root)
    station = args.station
    rows: list[tuple[object, ...]] = []
    rows.extend(
        ("candidate", record.id, record.station, record.fingerprint)
        for record in candidates
        if station is None or record.station == station
    )
    rows.extend(
        ("judge", record.id, "-", record.fingerprint) for record in judges.values()
    )
    rows.extend(
        ("suite", record.id, record.station, record.fingerprint)
        for record in suites
        if station is None or record.station == station
    )
    with EvaluationStore(args.db) as store:
        rows.extend(
            ("run", run.run_id, run.suite_id, run.report_fingerprint)
            for run in store.runs()
            if station is None
            or any(
                suite.id == run.suite_id and suite.station == station
                for suite in suites
            )
        )
        rows.extend(
            (
                promotion.action,
                promotion.promotion_id,
                promotion.station,
                promotion.next_candidate_fingerprint,
            )
            for promotion in store.promotions()
            if station is None or promotion.station == station
        )
    _print_rows(("kind", "id", "station/suite", "fingerprint"), rows)


def cmd_bench_verify(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.store import EvaluationStore
    from palimpsest.factory.evaluation.suite import validate_candidate_suite

    candidates = _resolve_candidates(args.candidates_root)
    suite = _resolve_suite(
        args.suite,
        judges_root=args.judges_root,
        asset_root=args.asset_root,
    )
    for candidate in candidates:
        if candidate.station == suite.station:
            validate_candidate_suite(candidate, suite)
    tracked_suites = _resolve_suites(
        args.suites_root,
        judges_root=args.judges_root,
        asset_root=args.asset_root,
    )
    source_objects = _verify_source_objects(suite.cases, args.object_root)
    source_objects += sum(
        _verify_source_objects(tracked.cases, args.object_root)
        for tracked in tracked_suites
        if tracked.fingerprint != suite.fingerprint
    )
    _, _, judges = _trusted_resolvers(args.judges_root)
    with EvaluationStore(args.db) as store:
        reports = tuple(
            _indexed_report(store, run.run_id)
            for run in store.runs()
            if run.report_path is not None
        )
    print(
        "verified "
        f"suite={suite.id} cases={len(suite.cases)} "
        f"objects={source_objects} candidates={len(candidates)} "
        f"judges={len(judges)} reports={len(reports)}"
    )


def cmd_bench_fetch(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.assets import fetch_assets

    suite = _resolve_suite(
        args.suite,
        judges_root=args.judges_root,
        asset_root=args.asset_root,
        verify_local=False,
    )
    records = fetch_assets(
        suite.cases,
        object_root=args.object_root,
        asset_root=args.asset_root,
    )
    for record in records:
        print(f"{record.status}  {record.sha256}  {record.path}")


def cmd_bench_run(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.candidate import load_candidate
    from palimpsest.factory.evaluation.runner import (
        filesystem_asset_resolver,
        run_evaluation,
    )
    from palimpsest.factory.evaluation.store import EvaluationStore
    from palimpsest.factory.evaluation.judging import GatewayJudgeExecutor

    resume = getattr(args, "resume", None)
    run_id = resume or args.run_id

    suite = _resolve_suite(
        args.suite,
        judges_root=_JUDGES_ROOT,
        asset_root=args.asset_root,
    )
    baseline = load_candidate(args.baseline)
    challenger = load_candidate(args.challenger)
    selected = None
    if args.cases is not None:
        by_id = {case.case_id: case for case in suite.cases}
        if len(set(args.cases)) != len(args.cases):
            raise ValueError("--cases cannot contain duplicate case IDs")
        missing = [case_id for case_id in args.cases if case_id not in by_id]
        if missing:
            raise ValueError(f"Unknown suite case IDs: {missing}")
        selected = tuple(by_id[case_id] for case_id in args.cases)
    _verify_source_objects(
        suite.cases if selected is None else selected,
        args.object_root,
    )
    resolver = filesystem_asset_resolver(args.asset_root, args.object_root)
    judge_executor = GatewayJudgeExecutor() if getattr(suite, "judges", ()) else None
    judge_arguments = (
        {"judge_executor": judge_executor} if judge_executor is not None else {}
    )
    with EvaluationStore(args.db) as store:
        result = run_evaluation(
            run_id=run_id,
            suite=suite,
            baseline=baseline,
            challenger=challenger,
            store=store,
            run_root=args.runs_root,
            asset_resolver=resolver,
            executor=args.executor,
            max_cost_usd=getattr(args, "max_cost", None),
            resume=resume,
            workers=args.workers,
            cases=selected,
            **judge_arguments,
        )
    print(result.report_path)


def cmd_bench_report(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.store import EvaluationStore

    with EvaluationStore(args.db) as store:
        report = _indexed_report(store, args.run)
    if args.format == "json":
        print(
            json.dumps(
                report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        )
    else:
        print(_report_table(report))


def cmd_bench_propose(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.candidate import load_candidate
    from palimpsest.factory.evaluation.promotion import (
        load_reproducibility_waiver,
        propose_recipe_change,
        save_recipe_proposal,
    )
    from palimpsest.factory.evaluation.store import EvaluationStore

    baseline = load_candidate(args.baseline)
    challenger = load_candidate(args.challenger)
    waiver = None if args.waiver is None else load_reproducibility_waiver(args.waiver)
    with EvaluationStore(args.db) as store:
        report = _indexed_report(store, args.run)
    proposal = propose_recipe_change(
        report=report,
        recipe_root=args.recipe_root,
        recipe=args.recipe,
        station=challenger.station,
        current_candidate=baseline,
        next_candidate=challenger,
        waiver=waiver,
    )
    print(save_recipe_proposal(args.output, proposal))


def _index_promotion(db: Path, record: object, artifact_path: Path) -> None:
    from palimpsest.factory.evaluation.promotion import to_evaluation_promotion_index
    from palimpsest.factory.evaluation.store import EvaluationStore

    try:
        with EvaluationStore(db) as store:
            store.record_promotion(to_evaluation_promotion_index(record))
    except Exception as error:
        raise RuntimeError(
            f"Promotion evidence was persisted at {artifact_path}, but indexing failed"
        ) from error


def cmd_bench_promote(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.promotion import (
        commit_recipe_decision,
        create_promotion_record,
        load_canary_evidence,
        load_recipe_proposal,
        save_canary_evidence,
    )

    if args.canary is not None:
        missing = [
            flag
            for flag, value in (
                ("--canary-root", args.canary_root),
                ("--canary-evidence-output", args.canary_evidence_output),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"--canary requires {' and '.join(missing)}")
    proposal = load_recipe_proposal(args.proposal)
    if args.canary_evidence is not None:
        canary = load_canary_evidence(args.canary_evidence)
    else:
        from palimpsest.factory.evaluation.canary import run_proposal_canary

        canary = run_proposal_canary(
            proposal=proposal,
            doc_id=args.canary,
            library_root=args.library_root,
            canary_root=args.canary_root,
            db_path=args.db,
            recipe_root=args.recipe_root,
            executor=args.executor,
            workers=args.workers,
        )
        save_canary_evidence(args.canary_evidence_output, canary)
    record = create_promotion_record(
        proposal,
        canary=canary,
        approved_by=args.approved_by,
        created_at=_utc_now(),
    )
    artifact = commit_recipe_decision(
        proposal,
        record,
        recipe_root=args.recipe_root,
        history_root=args.history_root,
    )
    _index_promotion(args.db, record, artifact)
    print(artifact)


def cmd_bench_rollback(args: argparse.Namespace) -> None:
    from palimpsest.factory.evaluation.candidate import load_candidate
    from palimpsest.factory.evaluation.promotion import (
        commit_recipe_decision,
        create_rollback_proposal,
        create_rollback_record,
        load_canary_evidence,
        load_promotion_record,
        save_recipe_proposal,
    )

    promotion = load_promotion_record(args.promotion)
    current = load_candidate(args.current)
    previous = load_candidate(args.previous)
    proposal = create_rollback_proposal(
        promotion,
        recipe_root=args.recipe_root,
        current_candidate=current,
        previous_candidate=previous,
    )
    canary = (
        None
        if args.canary_evidence is None
        else load_canary_evidence(args.canary_evidence)
    )
    record = create_rollback_record(
        proposal,
        promotion=promotion,
        approved_by=args.approved_by,
        created_at=_utc_now(),
        canary=canary,
    )
    if args.proposal_output is not None:
        save_recipe_proposal(args.proposal_output, proposal)
    artifact = commit_recipe_decision(
        proposal,
        record,
        recipe_root=args.recipe_root,
        history_root=args.history_root,
    )
    _index_promotion(args.db, record, artifact)
    print(artifact)


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
