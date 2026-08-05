"""Core factory tests: ledger lifecycle, prompt store, workspace I/O, gateway.

The stage_runs assertions query the SQLite file directly — the schema is the
public contract (FACTORY.md §2.5), so tests exercise it as such.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from palimpsest.factory import prompt_store
from palimpsest.factory.core import conductor as conductor_module
from palimpsest.factory.core.cell import CellOutcome
from palimpsest.factory.core.conductor import CellReport, Conductor, RunReport
from palimpsest.factory.core.ledger import Ledger, fingerprint
from palimpsest.factory.core.recipe import Recipe, StationSpec
from palimpsest.factory.core.station import Station
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
    assert "work_runs" in tables


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


def test_work_claim_excludes_other_processes(tmp_path):
    db_path = tmp_path / "factory.db"
    with Ledger(db_path) as first, Ledger(db_path) as second:
        first.adopt("claimed_doc", recipe="latin_manuscript")
        first_claim = first.claim_work("claimed_doc", owner="first")

        with pytest.raises(RuntimeError, match="already running under first"):
            second.claim_work("claimed_doc", owner="second")

        first.finish_work(first_claim, status="done")
        second_claim = second.claim_work("claimed_doc", owner="second")
        second.finish_work(second_claim, status="done")


def test_stale_claim_reconciles_unfinished_cells(ledger, tmp_path):
    doc_id = _adopt_test_item(ledger)
    claim = ledger.claim_work(doc_id, owner="crashed")
    stage = ledger.begin_run(
        doc_id,
        "read",
        page_id="0042",
        station_fingerprint="read-implementation",
        config_fingerprint="cfg",
        input_fingerprint="in",
    )

    assert ledger.reconcile_abandoned(doc_id, stale_before="9999-01-01") == 1

    with sqlite3.connect(tmp_path / "factory.db") as database:
        work_status = database.execute(
            "SELECT status FROM work_runs WHERE work_run_id = ?", (claim,)
        ).fetchone()[0]
        stage_status = database.execute(
            "SELECT status FROM stage_runs WHERE run_id = ?", (stage,)
        ).fetchone()[0]
    assert work_status == "abandoned"
    assert stage_status == "failed:abandoned"


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


def test_run_report_preserves_unknown_cost_without_counting_skips():
    report = RunReport(
        doc_id="doc",
        recipe="recipe",
        cells=[
            CellReport("read", "1", "fresh"),
            CellReport("read", "2", "ran", cost_usd=0.25),
            CellReport("read", "3", "ran", cost_usd=None),
        ],
    )

    assert report.cost_usd is None
    report.cells.pop()
    assert report.cost_usd == 0.25


def test_page_batch_uses_station_barriers_and_model_worker_limit(
    ledger, tmp_path, monkeypatch
):
    class Prepare(Station):
        name = "prepare_test"
        grain = "page"
        consumes = ()
        produces = "page_image_clean"

    class Model(Station):
        name = "model_test"
        grain = "page"
        consumes = ("page_image_clean",)
        produces = "page_transcription"
        uses_model = True

    batch = (
        StationSpec(Prepare(), None, None, {}, {}),
        StationSpec(Model(), "test-model", None, {}, {}),
    )
    pages = tuple({"page_id": f"p{index}", "order": index} for index in range(6))
    events = []
    active_models = 0
    maximum_models = 0
    lock = threading.Lock()
    conductor = Conductor(
        ledger,
        library_root=tmp_path,
        workers=4,
        model_workers=2,
    )

    def fake_run_cell(
        _doc_id,
        spec,
        _all_pages,
        *,
        page,
        prompts,
        previous_runs,
    ):
        nonlocal active_models, maximum_models
        assert prompts == {}
        assert previous_runs == {}
        if spec.station.uses_model:
            with lock:
                active_models += 1
                maximum_models = max(maximum_models, active_models)
                events.append(("model-start", page["page_id"]))
            time.sleep(0.02)
            with lock:
                active_models -= 1
                events.append(("model-finish", page["page_id"]))
        else:
            with lock:
                events.append(("prepare", page["page_id"]))
        return CellReport(spec.station.name, page["page_id"], "ran")

    monkeypatch.setattr(conductor, "_run_cell", fake_run_cell)
    cells = conductor._run_page_batch("doc", batch, pages, pages, {}, {})

    first_model = next(
        index for index, event in enumerate(events) if event[0] == "model-start"
    )
    assert {event for event in events[:first_model]} == {
        ("prepare", page["page_id"]) for page in pages
    }
    assert maximum_models == 2
    assert len(cells) == 12


@pytest.mark.parametrize(
    ("argument", "value"),
    [("workers", 0), ("model_workers", 0), ("workers", True)],
)
def test_conductor_rejects_invalid_worker_limits(ledger, argument, value):
    with pytest.raises(ValueError, match="must be a positive integer"):
        Conductor(ledger, **{argument: value})


def test_conductor_verifies_output_and_refreshes_prompt_snapshot(
    ledger, tmp_path, monkeypatch
):
    class OutputStation(Station):
        name = "test_output"
        grain = "page"
        consumes = ()
        produces = "page_translation"

    station = OutputStation()
    prompt_digest = {"value": "a" * 64}
    monkeypatch.setattr(
        conductor_module.prompt_store,
        "load",
        lambda name: prompt_store.Prompt(
            name=name,
            text="test prompt",
            sha256=prompt_digest["value"],
        ),
    )
    recipe = Recipe(
        name="test_recipe",
        language="",
        steps=(
            StationSpec(
                station=station,
                model=None,
                prompt_name="test/prompt",
                params={},
                options={},
            ),
        ),
    )
    loaded_recipes = []

    def injected_recipe_loader(name):
        loaded_recipes.append(name)
        return recipe

    doc_id = "integrity_doc"
    page_id = "f001r"
    ledger.adopt(doc_id, recipe=recipe.name)
    ws_io.atomic_write_json(
        layout.page_list_path(doc_id, tmp_path),
        {
            "doc_id": doc_id,
            "pages": [
                {"page_id": page_id, "url": "https://example.test/page.jpg", "order": 1}
            ],
        },
    )
    output_path = layout.artifact_path(
        doc_id, station.produces, page_id, library_root=tmp_path
    )

    class RecordingExecutor:
        calls = 0
        reported_path = output_path

        def execute(self, cell):
            self.calls += 1
            ws_io.atomic_write_json(
                output_path,
                {
                    "doc_id": doc_id,
                    "page_id": page_id,
                    "translation": f"generated-{self.calls}",
                    "flags": {},
                    "provenance": {
                        "station": cell.station,
                        "station_fingerprint": station.implementation_fingerprint,
                        "config_fingerprint": cell.config_fingerprint,
                        "input_fingerprint": cell.input_fingerprint,
                    },
                },
            )
            return CellOutcome(output_path=str(self.reported_path))

    executor = RecordingExecutor()
    conductor = Conductor(
        ledger,
        library_root=tmp_path,
        workers=1,
        recipe_loader=injected_recipe_loader,
    )
    conductor._executor = executor

    assert conductor.run(doc_id).count("ran") == 1
    assert loaded_recipes == [recipe.name]
    tampered = ws_io.read_json(output_path)
    tampered["translation"] = "manually changed"
    ws_io.atomic_write_json(output_path, tampered)
    rerun = conductor.run(doc_id)
    assert rerun.count("ran") == 1
    assert rerun.count("fresh") == 0
    assert executor.calls == 2
    forged = ws_io.read_json(output_path)
    forged["provenance"]["input_fingerprint"] = "forged"
    ws_io.atomic_write_json(output_path, forged)
    assert conductor.run(doc_id).count("ran") == 1
    assert executor.calls == 3

    prompt_digest["value"] = "b" * 64
    outdated = conductor.run(doc_id)
    assert outdated.count("outdated") == 1
    assert executor.calls == 3

    output_path.unlink()
    executor.reported_path = tmp_path / "unrelated.json"
    failed = conductor.run(doc_id)
    assert failed.count("failed") == 1
    assert "expected" in failed.cells[0].error

    class BilledFailureExecutor:
        def execute(self, cell):
            raise GatewayError(
                "malformed paid response",
                tokens_in=120,
                tokens_out=45,
                cost_usd=0.12,
            )

    conductor._executor = BilledFailureExecutor()
    billed_failure = conductor.run(doc_id)
    assert billed_failure.cost_usd == 0.12
    with sqlite3.connect(tmp_path / "factory.db") as database:
        tokens_in, tokens_out, cost = database.execute(
            """
            SELECT tokens_in, tokens_out, cost_usd
            FROM stage_runs ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
    assert (tokens_in, tokens_out, cost) == (120, 45, 0.12)


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
    for model in ("gemini-flash-latest", "gemini-flash-lite-latest"):
        cost = estimate_cost(model, 1000, 500)
        assert cost is not None and cost > 0
    assert estimate_cost("gemini-flash-latest", 1_000_000, 1_000_000) == pytest.approx(
        9.0
    )
    assert estimate_cost("gemini-flash-lite-latest", 1000, 500) == estimate_cost(
        "gemini-3.5-flash-lite", 1000, 500
    )
    assert estimate_cost("no-such-model", 1, 1) is None
