"""Core factory tests: ledger lifecycle, prompt store, workspace I/O, gateway.

The stage_runs assertions query the SQLite file directly — the schema is the
public contract (FACTORY.md §2.5), so tests exercise it as such.
"""

from __future__ import annotations

import sqlite3

import pytest

from palimpsest.factory import prompt_store
from palimpsest.factory.core.ledger import Ledger, fingerprint
from palimpsest.factory.gateway import GatewayError, ModelRequest, generate
from palimpsest.factory.gateway.pricing import estimate_cost
from palimpsest.factory.workspace import io as ws_io
from palimpsest.factory.workspace import layout


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path / "factory.db") as ledger:
        yield ledger


def test_ledger_has_canonical_work_order_schema(ledger, tmp_path):
    with sqlite3.connect(tmp_path / "factory.db") as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(items)")}
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert columns == {"doc_id", "recipe", "created_at", "status"}
    assert "prospects" not in tables


def _adopt_test_item(ledger: Ledger) -> str:
    doc_id = "vatican_pal_lat_1267"
    ledger.adopt(doc_id, recipe="latin_manuscript")
    return doc_id


def test_adopt_creates_work_order(ledger):
    doc_id = _adopt_test_item(ledger)
    (item,) = ledger.list_items()
    assert item["doc_id"] == doc_id
    assert item["status"] == "active"
    assert item["recipe"] == "latin_manuscript"


def test_refresh_appends_history_and_state_shows_latest(ledger, tmp_path):
    doc_id = _adopt_test_item(ledger)
    common = dict(
        page_id="0042",
        station_fingerprint="read-implementation",
        input_fingerprint=fingerprint("img-abc"),
    )

    old_run = ledger.begin_run(
        doc_id,
        "read",
        config_fingerprint=fingerprint("read-implementation", "old-model"),
        model="old-model",
        **common,
    )
    ledger.complete_run(
        old_run,
        output_fingerprint="old-output",
        tokens_in=1000,
        tokens_out=500,
        cost_usd=0.00625,
    )

    new_run = ledger.begin_run(
        doc_id,
        "read",
        config_fingerprint=fingerprint("read-implementation", "new-model"),
        model="new-model",
        **common,
    )
    ledger.complete_run(new_run, output_fingerprint="new-output")

    (state,) = ledger.state(doc_id)
    assert state["run_id"] == new_run
    assert state["output_fingerprint"] == "new-output"

    db = sqlite3.connect(tmp_path / "factory.db")
    assert db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0] == 2


def test_failed_run_is_logged_but_not_current_state(ledger, tmp_path):
    doc_id = _adopt_test_item(ledger)
    run_id = ledger.begin_run(
        doc_id,
        "translate",
        page_id="0042",
        station_fingerprint="translate-implementation",
        config_fingerprint="cfg",
        input_fingerprint="in",
    )
    ledger.fail_run(run_id, kind="rate_limit", detail="429 quota exceeded")

    assert ledger.state(doc_id) == []
    db = sqlite3.connect(tmp_path / "factory.db")
    status, error = db.execute(
        "SELECT status, error FROM stage_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert status == "failed:rate_limit"
    assert error == "429 quota exceeded"


def test_fingerprint_is_deterministic_and_order_sensitive():
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert fingerprint("a", "b") != fingerprint("b", "a")
    assert fingerprint("a", None) == fingerprint("a", "")


def test_prompt_store_loads_and_hashes(tmp_path):
    (tmp_path / "read" / "la").mkdir(parents=True)
    (tmp_path / "read" / "la" / "diplomatic.txt").write_text(
        "Transcribe.", encoding="utf-8"
    )
    prompt = prompt_store.load("read/la/diplomatic", root=tmp_path)
    assert prompt.text == "Transcribe."
    assert len(prompt.sha256) == 64


def test_prompt_store_blocks_path_escape(tmp_path):
    with pytest.raises(ValueError):
        prompt_store.load("../escape", root=tmp_path)


def test_workspace_jsonl_roundtrip(tmp_path):
    path = tmp_path / "records.jsonl"
    ws_io.atomic_write_jsonl(path, [{"page": 1}, {"page": "café"}])
    assert [r["page"] for r in ws_io.read_jsonl(path)] == [1, "café"]


def test_layout_contract(tmp_path):
    assert layout.artifact_path(
        "doc1", "page_image", "f001r", library_root=tmp_path
    ) == (tmp_path / "doc1" / "page_image" / "f001r.jpg")
    assert layout.artifact_path(
        "doc1", "translation_brief", None, library_root=tmp_path
    ) == (tmp_path / "doc1" / "translation_brief.json")
    assert layout.metadata_path("doc1", library_root=tmp_path).name == "metadata.json"


def test_gateway_rejects_unknown_provider():
    with pytest.raises(GatewayError):
        generate(ModelRequest(model="unknown-provider-model", prompt="hi"))


def test_pricing_known_and_unknown_models():
    # rates come from the genai-prices database and change over time —
    # assert resolution behavior, not specific numbers
    cost = estimate_cost("gemini-3.5-flash", 1000, 500)
    assert cost is not None and cost > 0
    assert estimate_cost("no-such-model", 1, 1) is None
