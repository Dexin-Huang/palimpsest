"""Operational contracts for health, snapshots, selection, queues, and publication."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.sync import sync_source
from palimpsest.cli import build_parser
from palimpsest.factory import cli as factory_cli
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.gateway.protocol import ModelResponse
from palimpsest.factory import publication_store as publication_store_module
from palimpsest.factory.health import inspect_factory
from palimpsest.factory.snapshot import (
    SnapshotError,
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)
from palimpsest import selection as selection_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _initialize_authoritative_databases(root: Path) -> tuple[Path, Path]:
    factory_db = root / "factory.db"
    catalog_db = root / "catalog.db"
    with Ledger(factory_db):
        pass
    with CatalogDB(catalog_db):
        pass
    return factory_db, catalog_db


def test_doctor_validates_recipe_owned_production_configuration(tmp_path):
    factory_db, catalog_db = _initialize_authoritative_databases(tmp_path)

    report = inspect_factory(
        factory_db=factory_db,
        catalog_db=catalog_db,
        library_root=tmp_path,
        recipes_root=PROJECT_ROOT / "palimpsest" / "factory" / "recipes",
    )

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["factory.database"]["status"] == "pass"
    assert checks["catalog.database"]["status"] == "pass"
    assert checks["recipes"] == {
        "name": "recipes",
        "status": "pass",
        "detail": "validated 2 recipe(s): chinese_scroll_rig, latin_manuscript",
    }
    assert checks["production.configuration"]["status"] == "pass"


def test_doctor_fails_closed_when_authoritative_database_is_missing(tmp_path):
    _factory_db, catalog_db = _initialize_authoritative_databases(tmp_path)
    missing = tmp_path / "missing-factory.db"

    report = inspect_factory(
        factory_db=missing,
        catalog_db=catalog_db,
        library_root=tmp_path,
        recipes_root=PROJECT_ROOT / "palimpsest" / "factory" / "recipes",
    )

    assert report["status"] == "fail"
    assert report["checks"][0]["detail"] == f"missing: {missing}"


def test_snapshot_round_trip_preserves_payloads_and_excludes_transient_state(tmp_path):
    library_root = tmp_path / "library"
    factory_db, catalog_db = _initialize_authoritative_databases(library_root)
    (library_root / "document").mkdir()
    (library_root / "document" / "book.epub").write_bytes(b"epub")
    (library_root / ".gateway-locks").mkdir()
    (library_root / ".gateway-locks" / "provider.0.lock").write_text("locked")
    archive = tmp_path / "snapshot.zip"

    created = create_snapshot(
        library_root,
        archive,
        database_paths=(factory_db, catalog_db),
    )
    verified = verify_snapshot(archive)
    restored = tmp_path / "restored"
    restore_snapshot(archive, restored)

    assert created["files"] == verified["files"]
    assert (restored / "document" / "book.epub").read_bytes() == b"epub"
    assert not (restored / ".gateway-locks").exists()
    with sqlite3.connect(restored / "factory.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_snapshot_verification_rejects_a_replaced_payload(tmp_path):
    library_root = tmp_path / "library"
    factory_db, catalog_db = _initialize_authoritative_databases(library_root)
    (library_root / "evidence.txt").write_text("original", encoding="utf-8")
    archive = tmp_path / "snapshot.zip"
    create_snapshot(
        library_root,
        archive,
        database_paths=(factory_db, catalog_db),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("evidence.txt", "changed")

    with pytest.raises(SnapshotError, match="duplicate|Digest mismatch"):
        verify_snapshot(archive)


def _catalog_line(source_key: str) -> str:
    return json.dumps(
        {
            "source_key": source_key,
            "source_url": f"https://archive.test/{source_key}",
            "record": {
                "record_type": "manuscript",
                "manifest_url": f"https://archive.test/{source_key}/manifest.json",
                "titles": [f"Manuscript {source_key}"],
                "repository": "Test Repository",
                "access": "open",
            },
            "raw": {"title": f"Manuscript {source_key}"},
        }
    )


def test_selection_samples_pages_with_qwen_and_writes_auditable_decisions(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "records.jsonl"
    source_path.write_text(_catalog_line("MS-1") + "\n", encoding="utf-8")
    database_path = tmp_path / "catalog.db"
    with CatalogDB(database_path) as database:
        database.add_source("archive-a", "normalized-jsonl", {"path": str(source_path)})
        sync_source(database, "archive-a")

    monkeypatch.setattr(
        selection_module,
        "_sample_record",
        lambda _record, _count: (
            [
                {
                    "page_id": "page_0003",
                    "order": 3,
                    "label": "3",
                    "url": "https://images.test/3.jpg",
                }
            ],
            (selection_module.ImageContent(b"jpeg", mime="image/jpeg"),),
        ),
    )
    requests = []

    def fake_generate_json(request):
        requests.append(request)
        return (
            {
                "verdict": "prioritize",
                "score": 91,
                "summary": "Unpublished colophon with readable text.",
                "significance": "A dated witness.",
                "language_or_script": "Chinese",
                "transcription_feasibility": "high",
                "evidence": ["Sample page 3 contains a dated colophon."],
                "risks": ["Outer margin is damaged."],
            },
            ModelResponse(
                text="{}",
                model=request.model,
                prompt_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cost_usd=0.02,
            ),
        )

    monkeypatch.setattr(selection_module, "generate_json", fake_generate_json)
    output = tmp_path / "selection.json"

    manifest = selection_module.select_catalog(
        source_id="archive-a",
        catalog_db=database_path,
        library_root=tmp_path,
        record_limit=1,
        page_samples=3,
        recommendation_limit=1,
        after=None,
        max_cost_usd=1.0,
        output=output,
    )

    assert requests[0].model == "token-plan/qwen3.8-max"
    assert len(requests[0].images) == 1
    assert manifest["evaluations"][0]["sampled_pages"][0]["order"] == 3
    assert manifest["evaluations"][0]["usage"]["cost_usd"] == 0.02
    assert json.loads(output.read_text(encoding="utf-8")) == manifest


def test_active_queue_is_bounded_by_count_and_observed_cost(
    tmp_path, monkeypatch, capsys
):
    class FakeLedger:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_items(self):
            return [
                {"doc_id": "first", "status": "active"},
                {"doc_id": "second", "status": "active"},
                {"doc_id": "third", "status": "active"},
            ]

    driven = []

    def drive(_args, doc_id):
        driven.append(doc_id)
        return SimpleNamespace(
            doc_id=doc_id,
            recipe="test",
            cost_usd=0.6,
            partial=False,
            cells=(),
            count=lambda _action: 0,
        )

    monkeypatch.setattr(factory_cli, "Ledger", FakeLedger)
    monkeypatch.setattr(factory_cli, "_drive_work_order", drive)
    args = build_parser().parse_args(
        [
            "run",
            "--db",
            str(tmp_path / "factory.db"),
            "--library-root",
            str(tmp_path),
            "--active",
            "--limit",
            "3",
            "--max-total-cost",
            "0.5",
        ]
    )

    args.func(args)

    assert driven == ["first"]
    assert "queue stopped at observed cost $0.6000" in capsys.readouterr().out


def test_publication_upload_uses_low_level_s3_api_and_verifies_inventory(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle"
    (bundle / "books").mkdir(parents=True)
    (bundle / "library.json").write_text("{}\n", encoding="utf-8")
    (bundle / "books" / "book.epub").write_bytes(b"epub")
    commands = []
    monkeypatch.setattr(publication_store_module.shutil, "which", lambda _name: "aws")

    def run(command, **_options):
        commands.append(command)
        if "list-objects-v2" in command:
            prefix = "releases/sha256:test/"
            payload = {
                "Contents": [
                    {
                        "Key": prefix + "books/book.epub",
                        "Size": 4,
                    },
                    {
                        "Key": prefix + "library.json",
                        "Size": (bundle / "library.json").stat().st_size,
                    },
                ]
            }
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(payload), stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(publication_store_module.subprocess, "run", run)

    release = publication_store_module.publish_bundle(
        bundle,
        bundle_id="sha256:test",
        bucket="publication",
        profile="r2",
        endpoint_url="https://r2.test",
        public_base_url="https://releases.test",
    )

    uploads = [command for command in commands if "put-object" in command]
    assert len(uploads) == 2
    assert all(command[1:3] == ["s3api", "put-object"] for command in uploads)
    assert release.public_url == "https://releases.test/releases/sha256:test/"
