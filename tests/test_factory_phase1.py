"""Phase 1 factory tests: ledger lifecycle, prompt store, workspace I/O, gateway.

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


def _promote_test_item(ledger: Ledger) -> str:
    ledger.add_prospect(
        "vatican:pal.lat.1267", head="vatican", archive_ref="Pal.lat.1267",
        title="Test codex", language="la",
    )
    ledger.record_triage("vatican:pal.lat.1267", score=87, triage_json="{}")
    ledger.promote("vatican:pal.lat.1267", doc_id="vatican_pal_lat_1267",
                   recipe="latin_manuscript", mode="source")
    return "vatican_pal_lat_1267"


def test_promote_joins_scout_provenance(ledger):
    _promote_test_item(ledger)
    (item,) = ledger.list_items()
    assert item["head"] == "vatican"
    assert item["triage_score"] == 87
    assert item["recipe"] == "latin_manuscript"


def test_refresh_appends_history_and_state_shows_latest(ledger, tmp_path):
    doc_id = _promote_test_item(ledger)
    common = dict(page_id="0042", station_version="read/v1",
                  input_fingerprint=fingerprint("img-abc"))

    run_v1 = ledger.begin_run(
        doc_id, "read", config_fingerprint=fingerprint("read/v1", "old-model"),
        model="old-model", **common,
    )
    ledger.complete_run(run_v1, output_fingerprint="out-v1",
                        tokens_in=1000, tokens_out=500, cost_usd=0.00625)

    run_v2 = ledger.begin_run(
        doc_id, "read", config_fingerprint=fingerprint("read/v1", "new-model"),
        model="new-model", **common,
    )
    ledger.complete_run(run_v2, output_fingerprint="out-v2")

    (state,) = ledger.state(doc_id)
    assert state["run_id"] == run_v2
    assert state["output_fingerprint"] == "out-v2"

    db = sqlite3.connect(tmp_path / "factory.db")
    assert db.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0] == 2


def test_failed_run_is_logged_but_not_current_state(ledger, tmp_path):
    doc_id = _promote_test_item(ledger)
    run_id = ledger.begin_run(
        doc_id, "translate", page_id="0042", station_version="translate/v1",
        config_fingerprint="cfg", input_fingerprint="in",
    )
    ledger.fail_run(run_id, kind="rate_limit", detail="429 quota exceeded")

    assert ledger.state(doc_id) == []
    db = sqlite3.connect(tmp_path / "factory.db")
    status, error = db.execute(
        "SELECT status, error FROM stage_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert status == "failed:rate_limit"
    assert error == "429 quota exceeded"


def test_triage_of_unknown_prospect_raises(ledger):
    with pytest.raises(KeyError):
        ledger.record_triage("nonexistent", score=1, triage_json="{}")


def test_fingerprint_is_deterministic_and_order_sensitive():
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert fingerprint("a", "b") != fingerprint("b", "a")
    assert fingerprint("a", None) == fingerprint("a", "")


def test_prompt_store_loads_and_hashes(tmp_path):
    (tmp_path / "read" / "la").mkdir(parents=True)
    (tmp_path / "read" / "la" / "diplomatic.txt").write_text("Transcribe.", encoding="utf-8")
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
    assert layout.artifact_dir("doc1", "page_image", library_root=tmp_path) == (
        tmp_path / "doc1" / "page_image"
    )
    assert layout.metadata_path("doc1", library_root=tmp_path).name == "metadata.json"


def test_gateway_rejects_unknown_provider():
    with pytest.raises(GatewayError):
        generate(ModelRequest(model="unknown-provider-model", prompt="hi"))


def test_pricing_known_and_unknown_models():
    assert estimate_cost("gemini-3.1-pro-preview", 1000, 500) == pytest.approx(0.00625)
    assert estimate_cost("no-such-model", 1, 1) is None
