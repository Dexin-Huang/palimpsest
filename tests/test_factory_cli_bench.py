from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from palimpsest.cli import build_parser
from palimpsest.factory import cli
from palimpsest.factory.evaluation.report import (
    CaseSideOutcome,
    PairedCaseOutcome,
    ReportIdentity,
    build_report,
    write_report,
)
from palimpsest.factory.evaluation.store import EvaluationStore


class _StoreContext:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def runs(self):
        return ()

    def promotions(self):
        return ()


@pytest.mark.parametrize(
    ("argv", "handler", "expected"),
    [
        (
            ["bench", "list", "--station", "read"],
            cli.cmd_bench_list,
            {"station": "read"},
        ),
        (
            ["bench", "verify", "--suite", "suite.yaml"],
            cli.cmd_bench_verify,
            {"suite": Path("suite.yaml")},
        ),
        (
            ["bench", "fetch", "--suite", "suite.yaml", "--object-root", "objects"],
            cli.cmd_bench_fetch,
            {"object_root": Path("objects")},
        ),
        (
            [
                "bench",
                "run",
                "--suite",
                "suite.yaml",
                "--baseline",
                "base.yaml",
                "--challenger",
                "next.yaml",
                "--run-id",
                "run-7",
                "--db",
                "index.db",
                "--runs-root",
                "runs",
                "--asset-root",
                "assets",
                "--object-root",
                "objects",
                "--executor",
                "inline",
                "--workers",
                "3",
                "--cases",
                "a",
                "b",
            ],
            cli.cmd_bench_run,
            {"run_id": "run-7", "workers": 3, "cases": ["a", "b"]},
        ),
        (
            ["bench", "report", "run-7", "--format", "json"],
            cli.cmd_bench_report,
            {"run": "run-7", "format": "json"},
        ),
        (
            [
                "bench",
                "propose",
                "run-7",
                "--recipe",
                "latin",
                "--recipe-root",
                "recipes",
                "--baseline",
                "base.yaml",
                "--challenger",
                "next.yaml",
                "--output",
                "proposal.json",
            ],
            cli.cmd_bench_propose,
            {
                "run": "run-7",
                "output": Path("proposal.json"),
            },
        ),
        (
            [
                "bench",
                "promote",
                "proposal.json",
                "--recipe-root",
                "recipes",
                "--canary-evidence",
                "canary.json",
                "--approved-by",
                "Operator",
                "--history-root",
                "history",
            ],
            cli.cmd_bench_promote,
            {"proposal": Path("proposal.json"), "approved_by": "Operator"},
        ),
        (
            [
                "bench",
                "rollback",
                "promotion.json",
                "--recipe-root",
                "recipes",
                "--current",
                "next.yaml",
                "--previous",
                "base.yaml",
                "--approved-by",
                "Operator",
                "--history-root",
                "history",
                "--proposal-output",
                "rollback-proposal.json",
            ],
            cli.cmd_bench_rollback,
            {"promotion": Path("promotion.json"), "previous": Path("base.yaml")},
        ),
    ],
)
def test_bench_parser_routes_every_subcommand(argv, handler, expected) -> None:
    args = build_parser().parse_args(argv)

    assert args.func is handler
    for name, value in expected.items():
        assert getattr(args, name) == value


def test_bench_help_and_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["--help"], ["bench", "--help"]):
        with pytest.raises(SystemExit) as raised:
            build_parser().parse_args(argv)
        assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "bench" in output
    assert "verify" in output
    assert "rollback" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["bench", "verify"],
        ["bench", "run", "--suite", "suite.yaml", "--baseline", "base.yaml"],
        ["bench", "propose", "run-7", "--recipe", "latin"],
        [
            "bench",
            "promote",
            "proposal.json",
            "--recipe-root",
            "recipes",
            "--approved-by",
            "Operator",
            "--history-root",
            "history",
        ],
        [
            "bench",
            "rollback",
            "promotion.json",
            "--recipe-root",
            "recipes",
            "--approved-by",
            "Operator",
            "--history-root",
            "history",
        ],
        ["bench", "report", "run-7", "--unknown-option"],
        [
            "bench",
            "run",
            "--suite",
            "suite.yaml",
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--run-id",
            "run-7",
            "--workers",
            "0",
        ],
        [
            "bench",
            "run",
            "--suite",
            "suite.yaml",
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--run-id",
            "new-run",
            "--resume",
            "old-run",
        ],
        [
            "bench",
            "run",
            "--suite",
            "suite.yaml",
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--run-id",
            "run-7",
            "--max-cost",
            "nan",
        ],
        [
            "bench",
            "run",
            "--suite",
            "suite.yaml",
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--run-id",
            "run-7",
            "--max-cost",
            "-0.01",
        ],
    ],
)
def test_incomplete_commands_are_rejected_by_parser(argv) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_list_inventories_tracked_records_and_indexed_history(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = SimpleNamespace(id="read/base", station="read", fingerprint="1" * 64)
    suite = SimpleNamespace(id="read/suite/v1", station="read", fingerprint="2" * 64)
    judge = SimpleNamespace(id="judge/read", fingerprint="3" * 64)

    class InventoryStore(_StoreContext):
        def runs(self):
            return (
                SimpleNamespace(
                    run_id="run-1",
                    suite_id=suite.id,
                    report_fingerprint=None,
                ),
            )

        def promotions(self):
            return (
                SimpleNamespace(
                    action="promote",
                    promotion_id="promotion-1",
                    station="read",
                    next_candidate_fingerprint="4" * 64,
                ),
            )

    monkeypatch.setattr(cli, "_resolve_candidates", Mock(return_value=(candidate,)))
    monkeypatch.setattr(cli, "_resolve_suites", Mock(return_value=(suite,)))
    monkeypatch.setattr(
        cli, "_trusted_resolvers", Mock(return_value=(object(), {}, {judge.id: judge}))
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.store.EvaluationStore", InventoryStore
    )
    args = build_parser().parse_args(["bench", "list", "--station", "read"])

    args.func(args)

    output = capsys.readouterr().out
    for record_id in (
        "read/base",
        "judge/read",
        "read/suite/v1",
        "run-1",
        "promotion-1",
    ):
        assert record_id in output


def test_verify_is_offline_and_resolves_all_tracked_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = SimpleNamespace(station="read")
    suite = SimpleNamespace(
        id="read/suite/v1", station="read", cases=(object(),), fingerprint="f" * 64
    )
    validate = Mock()
    monkeypatch.setattr(cli, "_resolve_candidates", Mock(return_value=(candidate,)))
    monkeypatch.setattr(cli, "_resolve_suite", Mock(return_value=suite))
    monkeypatch.setattr(cli, "_resolve_suites", Mock(return_value=(suite,)))
    monkeypatch.setattr(
        cli, "_trusted_resolvers", Mock(return_value=(object(), {}, {}))
    )
    monkeypatch.setattr(cli, "_verify_source_objects", Mock(return_value=1))
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.store.EvaluationStore", _StoreContext
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.suite.validate_candidate_suite", validate
    )
    paid = Mock(side_effect=AssertionError("network/model work is forbidden"))
    monkeypatch.setattr("palimpsest.factory.gateway.generate", paid)
    args = build_parser().parse_args(
        [
            "bench",
            "verify",
            "--suite",
            "suite.yaml",
            "--db",
            str(tmp_path / "db.sqlite"),
        ]
    )

    args.func(args)

    validate.assert_called_once_with(candidate, suite)
    paid.assert_not_called()
    assert "cases=1" in capsys.readouterr().out


def test_fetch_delegates_declared_suite_cases_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (object(), object())
    suite = SimpleNamespace(cases=cases)
    fetch = Mock(return_value=())
    resolve = Mock(return_value=suite)
    monkeypatch.setattr(cli, "_resolve_suite", resolve)
    monkeypatch.setattr("palimpsest.factory.evaluation.assets.fetch_assets", fetch)
    args = build_parser().parse_args(
        [
            "bench",
            "fetch",
            "--suite",
            "suite.yaml",
            "--asset-root",
            str(tmp_path / "assets"),
            "--object-root",
            str(tmp_path / "objects"),
        ]
    )

    args.func(args)

    assert resolve.call_args.kwargs["verify_local"] is False
    fetch.assert_called_once_with(
        cases, object_root=tmp_path / "objects", asset_root=tmp_path / "assets"
    )


def test_run_delegates_once_with_exact_resolved_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case_a = SimpleNamespace(case_id="a")
    case_b = SimpleNamespace(case_id="b")
    suite = SimpleNamespace(cases=(case_a, case_b))
    baseline = object()
    challenger = object()
    workflow = Mock(
        return_value=SimpleNamespace(
            report_path=tmp_path / "runs" / "run-7" / "report.json"
        )
    )
    resolver = object()
    monkeypatch.setattr(cli, "_resolve_suite", Mock(return_value=suite))
    monkeypatch.setattr(cli, "_verify_source_objects", Mock(return_value=0))
    load_candidate = Mock(side_effect=[baseline, challenger])
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.candidate.load_candidate", load_candidate
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.runner.filesystem_asset_resolver",
        Mock(return_value=resolver),
    )
    monkeypatch.setattr("palimpsest.factory.evaluation.runner.run_evaluation", workflow)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.store.EvaluationStore", _StoreContext
    )
    args = build_parser().parse_args(
        [
            "bench",
            "run",
            "--suite",
            "suite.yaml",
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--resume",
            "run-7",
            "--db",
            str(tmp_path / "db.sqlite"),
            "--runs-root",
            str(tmp_path / "runs"),
            "--asset-root",
            str(tmp_path / "assets"),
            "--object-root",
            str(tmp_path / "objects"),
            "--executor",
            "inline",
            "--workers",
            "2",
            "--max-cost",
            "3.5",
            "--cases",
            "b",
        ]
    )

    args.func(args)

    workflow.assert_called_once()
    passed = workflow.call_args.kwargs
    assert passed == {
        "run_id": "run-7",
        "suite": suite,
        "baseline": baseline,
        "challenger": challenger,
        "store": workflow.call_args.kwargs["store"],
        "run_root": tmp_path / "runs",
        "asset_resolver": resolver,
        "executor": "inline",
        "max_cost_usd": 3.5,
        "resume": "run-7",
        "workers": 2,
        "cases": (case_b,),
    }
    assert "report.json" in capsys.readouterr().out


def _indexed_unknown_report(tmp_path: Path) -> tuple[Path, str]:
    db = tmp_path / "factory.db"
    side = CaseSideOutcome(
        candidate_id="read/base",
        candidate_fingerprint="1" * 64,
        succeeded=True,
        output_path="output.json",
        output_fingerprint="2" * 64,
        latency_seconds=1.25,
        tokens_in=None,
        tokens_out=None,
        cost_usd=None,
    )
    challenger = CaseSideOutcome(
        candidate_id="read/next",
        candidate_fingerprint="3" * 64,
        succeeded=True,
        output_path="output.json",
        output_fingerprint="4" * 64,
        latency_seconds=1.0,
        tokens_in=None,
        tokens_out=None,
        cost_usd=None,
    )
    report = build_report(
        run_id="run-null",
        status="completed",
        decision="inconclusive",
        started_at="2026-07-21T10:00:00Z",
        finished_at="2026-07-21T10:01:00Z",
        suite=ReportIdentity("read/suite/v1", "5" * 64),
        baseline=ReportIdentity("read/base", "1" * 64),
        challenger=ReportIdentity("read/next", "3" * 64),
        cases=(PairedCaseOutcome("case-1", side, challenger),),
        aggregates={"cost_usd": None},
    )
    path = write_report(tmp_path / "runs" / "run-null" / "report.json", report)
    with EvaluationStore(db) as store:
        store.index_report(path)
    return db, "run-null"


def test_report_json_and_table_preserve_unknown_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, run_id = _indexed_unknown_report(tmp_path)
    json_args = build_parser().parse_args(
        ["bench", "report", run_id, "--format", "json", "--db", str(db)]
    )
    json_args.func(json_args)
    rendered_json = json.loads(capsys.readouterr().out)
    assert rendered_json["aggregates"]["cost_usd"] is None
    assert rendered_json["cases"][0]["baseline"]["cost_usd"] is None

    table_args = build_parser().parse_args(
        ["bench", "report", run_id, "--format", "table", "--db", str(db)]
    )
    table_args.func(table_args)
    assert "unknown" in capsys.readouterr().out


def test_propose_calls_immutable_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = SimpleNamespace(station="read")
    challenger = SimpleNamespace(station="read")
    proposal = object()
    propose = Mock(return_value=proposal)
    save = Mock(return_value=tmp_path / "proposal.json")
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.candidate.load_candidate",
        Mock(side_effect=[baseline, challenger]),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.propose_recipe_change", propose
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.save_recipe_proposal", save
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.store.EvaluationStore", _StoreContext
    )
    monkeypatch.setattr(cli, "_indexed_report", Mock(return_value={"run_id": "run-7"}))
    args = build_parser().parse_args(
        [
            "bench",
            "propose",
            "run-7",
            "--recipe",
            "latin",
            "--recipe-root",
            str(tmp_path / "recipes"),
            "--baseline",
            "base.yaml",
            "--challenger",
            "next.yaml",
            "--output",
            str(tmp_path / "proposal.json"),
        ]
    )

    args.func(args)

    save.assert_called_once_with(tmp_path / "proposal.json", proposal)


def test_promote_uses_explicit_canary_without_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    proposal = object()
    canary = object()
    record = object()
    artifact = tmp_path / "history" / "record.json"
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.load_recipe_proposal",
        Mock(return_value=proposal),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.load_canary_evidence",
        Mock(return_value=canary),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.create_promotion_record",
        Mock(return_value=record),
    )
    commit = Mock(side_effect=lambda *a, **k: events.append("commit") or artifact)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.commit_recipe_decision", commit
    )
    monkeypatch.setattr(
        cli, "_index_promotion", Mock(side_effect=lambda *a: events.append("index"))
    )
    paid = Mock(side_effect=AssertionError("promotion must not execute model work"))
    monkeypatch.setattr("palimpsest.factory.gateway.generate", paid)
    args = build_parser().parse_args(
        [
            "bench",
            "promote",
            "proposal.json",
            "--recipe-root",
            str(tmp_path / "recipes"),
            "--canary-evidence",
            "canary.json",
            "--approved-by",
            "Operator",
            "--history-root",
            str(tmp_path / "history"),
        ]
    )

    args.func(args)

    assert events == ["commit", "index"]
    commit.assert_called_once_with(
        proposal,
        record,
        recipe_root=tmp_path / "recipes",
        history_root=tmp_path / "history",
    )
    paid.assert_not_called()


def test_live_canary_missing_retention_paths_fails_before_loading_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = Mock()
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.load_recipe_proposal", load
    )
    args = build_parser().parse_args(
        [
            "bench",
            "promote",
            "proposal.json",
            "--recipe-root",
            str(tmp_path / "recipes"),
            "--canary",
            "doc-1",
            "--approved-by",
            "Operator",
            "--history-root",
            str(tmp_path / "history"),
        ]
    )

    with pytest.raises(ValueError, match="canary-root"):
        args.func(args)
    load.assert_not_called()


def test_rollback_creates_exact_inverse_then_applies_persists_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    promotion = object()
    current = object()
    previous = object()
    proposal = object()
    record = object()
    artifact = tmp_path / "history" / "rollback.json"
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.load_promotion_record",
        Mock(return_value=promotion),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.candidate.load_candidate",
        Mock(side_effect=[current, previous]),
    )
    create_proposal = Mock(return_value=proposal)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.create_rollback_proposal",
        create_proposal,
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.create_rollback_record",
        Mock(return_value=record),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.save_recipe_proposal",
        Mock(side_effect=lambda *a: events.append("save-proposal")),
    )
    commit = Mock(side_effect=lambda *a, **k: events.append("commit") or artifact)
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.commit_recipe_decision", commit
    )
    monkeypatch.setattr(
        cli, "_index_promotion", Mock(side_effect=lambda *a: events.append("index"))
    )
    args = build_parser().parse_args(
        [
            "bench",
            "rollback",
            "promotion.json",
            "--recipe-root",
            str(tmp_path / "recipes"),
            "--current",
            "next.yaml",
            "--previous",
            "base.yaml",
            "--approved-by",
            "Operator",
            "--history-root",
            str(tmp_path / "history"),
            "--proposal-output",
            str(tmp_path / "rollback-proposal.json"),
        ]
    )

    args.func(args)

    create_proposal.assert_called_once_with(
        promotion,
        recipe_root=tmp_path / "recipes",
        current_candidate=current,
        previous_candidate=previous,
    )
    assert events == ["save-proposal", "commit", "index"]
    commit.assert_called_once_with(
        proposal,
        record,
        recipe_root=tmp_path / "recipes",
        history_root=tmp_path / "history",
    )


def test_verify_source_objects_never_fetches_and_preserves_hash_contract(
    tmp_path: Path,
) -> None:
    from palimpsest.factory.evaluation.suite import CaseAsset

    digest = "a" * 64
    case = SimpleNamespace(
        inputs={
            "image": CaseAsset(sha256=digest, source="https://example.invalid/image")
        },
        references={},
    )

    with pytest.raises(ValueError, match="bench fetch"):
        cli._verify_source_objects((case,), tmp_path / "objects")


def test_live_canary_is_isolated_saved_then_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = object()
    canary = object()
    record = object()
    artifact = tmp_path / "history" / "record.json"
    run_canary = Mock(return_value=canary)
    save_canary = Mock()
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.load_recipe_proposal",
        Mock(return_value=proposal),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.canary.run_proposal_canary", run_canary
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.save_canary_evidence", save_canary
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.create_promotion_record",
        Mock(return_value=record),
    )
    monkeypatch.setattr(
        "palimpsest.factory.evaluation.promotion.commit_recipe_decision",
        Mock(return_value=artifact),
    )
    monkeypatch.setattr(cli, "_index_promotion", Mock())
    args = build_parser().parse_args(
        [
            "bench",
            "promote",
            "proposal.json",
            "--recipe-root",
            str(tmp_path / "recipes"),
            "--canary",
            "doc-1",
            "--canary-root",
            str(tmp_path / "isolated-canary"),
            "--canary-evidence-output",
            str(tmp_path / "canary.json"),
            "--library-root",
            str(tmp_path / "library"),
            "--approved-by",
            "Operator",
            "--history-root",
            str(tmp_path / "history"),
            "--db",
            str(tmp_path / "factory.db"),
            "--executor",
            "inline",
            "--workers",
            "2",
        ]
    )

    args.func(args)

    run_canary.assert_called_once_with(
        proposal=proposal,
        doc_id="doc-1",
        library_root=tmp_path / "library",
        canary_root=tmp_path / "isolated-canary",
        db_path=tmp_path / "factory.db",
        recipe_root=tmp_path / "recipes",
        executor="inline",
        workers=2,
    )
    save_canary.assert_called_once_with(tmp_path / "canary.json", canary)
