"""Focused tests for factory run concurrency options."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from palimpsest.cli import build_parser
from palimpsest.factory import cli as factory_cli
from palimpsest.factory.core import conductor as conductor_module


@pytest.fixture
def conductor_calls(monkeypatch):
    calls = []

    class FakeLedger:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class RecordingConductor:
        def __init__(self, _ledger, **options):
            calls.append(options)

        def run(self, doc_id):
            return SimpleNamespace(
                doc_id=doc_id,
                recipe="test_recipe",
                cost_usd=0.0,
                partial=False,
                cells=(),
                count=lambda _action: 0,
            )

    monkeypatch.setattr(factory_cli, "Ledger", FakeLedger)
    monkeypatch.setattr(conductor_module, "Conductor", RecordingConductor)
    return calls


def _run_factory(tmp_path, *worker_options):
    args = build_parser().parse_args(
        [
            "run",
            "--db",
            str(tmp_path / "factory.db"),
            "--library-root",
            str(tmp_path),
            "--doc-id",
            "test_document",
            *worker_options,
        ]
    )
    args.func(args)


@pytest.mark.parametrize(
    ("workers", "provider_workers", "expected_model_workers"),
    [(20, 3, 3), (20, 20, 20), (20, 24, 20)],
)
def test_default_model_workers_are_bounded_by_workers_and_provider_capacity(
    tmp_path,
    monkeypatch,
    conductor_calls,
    workers,
    provider_workers,
    expected_model_workers,
):
    # cli.py imports configuration at module load, so patch the imported value
    # rather than mutating process environment after collection.
    monkeypatch.setattr(factory_cli, "MODEL_PROVIDER_WORKERS", provider_workers)

    _run_factory(tmp_path, "--workers", str(workers))

    assert conductor_calls[0]["workers"] == workers
    assert conductor_calls[0]["model_workers"] == expected_model_workers


def test_explicit_model_worker_override_wins_unchanged(
    tmp_path, monkeypatch, conductor_calls
):
    monkeypatch.setattr(factory_cli, "MODEL_PROVIDER_WORKERS", 3)

    _run_factory(tmp_path, "--workers", "20", "--model-workers", "11")

    assert conductor_calls[0]["model_workers"] == 11


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_model_worker_override_rejects_non_positive_integers(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "run",
                "--doc-id",
                "test_document",
                "--model-workers",
                value,
            ]
        )


def test_model_worker_help_describes_derived_default(capsys):
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["run", "--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "min(--workers, PALIMPSEST_MODEL_PROVIDER_WORKERS)" in output
