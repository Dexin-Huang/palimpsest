"""Phase 2 tests: recipe validation + conductor end-to-end on a fake document.

The gateway is monkeypatched (no network, no API key); acquire is fed by a
local HTTP-free fetch stub. Everything else — stations, conductor, ledger,
fingerprints, staleness — runs for real.
"""

from __future__ import annotations

import json

import pytest

import palimpsest.factory.stations.acquire as acquire_module
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.gateway.client import ModelResponse
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import artifact_path

DOC = "test_codex"
PAGES = [
    {"page_id": "f001r", "url": "https://archive.test/f001r.jpg", "order": 1},
    {"page_id": "f001v", "url": "https://archive.test/f001v.jpg", "order": 2},
]


@pytest.fixture
def library(tmp_path):
    doc_dir = tmp_path / "library" / DOC
    doc_dir.mkdir(parents=True)
    atomic_write_json(doc_dir / "page_list.json", {"doc_id": DOC, "pages": PAGES})
    atomic_write_json(doc_dir / "metadata.json", {"source_catalog": {"title": "Test"}})
    return tmp_path / "library"


@pytest.fixture
def ledger(library):
    with Ledger(library / "factory.db") as ledger:
        ledger.adopt(DOC, recipe="latin_manuscript")
        yield ledger


RECONSTRUCT_PLAN = {
    "sections": [{"heading": "Remedies", "from_page": "f001r", "to_page": "f001v"}],
    "joins": [{
        "from_page": "f001r", "to_page": "f001v",
        "kind": "sentence_continuation", "rationale": "flags say so",
    }],
    "readers_note": "A small test codex of remedies.",
}

TEXT_STATIONS = ("translate",)                                # call generate()
JSON_STATIONS = ("read", "survey", "reconstruct")             # call generate_json()


class ScriptedGateway:
    """Deterministic fake: reply depends on which prompt/station is calling."""

    def __init__(self):
        self.calls = []
        self.read_texts = {"f001r": "Experimenta ad morbos", "f001v": "Ad febres tertianas"}

    def __call__(self, request):
        self.calls.append(request)
        if request.images:  # read station
            page_id = request.images[0].stem
            text = json.dumps({"transcription": self.read_texts[page_id]})
        elif request.json_output and "reconstructing the structure" in request.prompt:
            text = json.dumps(RECONSTRUCT_PLAN)
        elif request.json_output:  # survey station
            text = json.dumps({
                "terms": [{"term": "febris", "translation": "fever", "note": ""}],
                "sections": [], "abbreviations": [], "entities": [],
                "flags": [], "style_notes": ["Medieval Latin"],
            })
        else:  # translate station
            text = ("Translated body\n---FLAGS---\n"
                    '{"starts_mid_sentence": false, "ends_mid_sentence": false, "new_terms": []}'
                    "\n---END FLAGS---")
        return ModelResponse(text=text, model=request.model,
                             prompt_tokens=100, output_tokens=50, cost_usd=0.001)


@pytest.fixture
def gateway(monkeypatch):
    from palimpsest.factory import agent_cell
    from palimpsest.factory.gateway.client import parse_json_response

    fake = ScriptedGateway()

    def fake_json(request, **kwargs):
        response = fake(request)
        return parse_json_response(response.text), response

    def fake_agent_run(workspace, task, model, timeout_s=0):
        # scripted stand-ins for the two editorial agents: reference emits an
        # empty dossier; emend applies one covered, anchored emendation
        out = workspace / "out"
        if "out/emendations.json" not in task:
            (out / "reference.json").write_text(json.dumps({
                "identification": {"work": "Test codex", "tradition": "test"},
                "reference_points": [], "editorial_notes": [],
            }), encoding="utf-8")
        else:
            evidence = json.loads((workspace / "evidence" / "manuscript.json")
                                  .read_text(encoding="utf-8"))
            (out / "emendations.json").write_text(json.dumps({
                "sections": [
                    {"heading": s["heading"],
                     "reading": s["original"].replace("morbos", "morbos EMENDED")}
                    for s in evidence["sections"]],
                "apparatus": [{"section": s["heading"], "original": "morbos",
                               "emended": "morbos EMENDED",
                               "reason": "test emendation", "evidence": "structure"}
                              for s in evidence["sections"]
                              if "morbos" in s["original"]],
            }, ensure_ascii=False), encoding="utf-8")
        return agent_cell.AgentRun(
            session_id="00000000-0000-0000-0000-000000000000",
            tokens=100, log_path=out / "agent_run.log")

    def fail_resume(*args, **kwargs):
        raise AssertionError("verifier rejected the scripted emendation")

    for module in TEXT_STATIONS:
        monkeypatch.setattr(f"palimpsest.factory.stations.{module}.generate", fake)
    for module in JSON_STATIONS:
        monkeypatch.setattr(f"palimpsest.factory.stations.{module}.generate_json", fake_json)
    monkeypatch.setattr("palimpsest.factory.agent_cell.run", fake_agent_run)
    monkeypatch.setattr("palimpsest.factory.agent_cell.resume", fail_resume)
    return fake


def _synthetic_page_jpeg() -> bytes:
    """A light page: white 800×600 with thin text-like strokes — thin, because
    the adaptive ink mask only marks stroke-scale features (solid bars read
    as outlines) — with enough ink to clear the full-page routing floor."""
    import cv2
    import numpy as np

    page = np.full((800, 600, 3), 235, np.uint8)
    for row in range(10):
        cv2.rectangle(page, (120, 180 + row * 20), (480, 188 + row * 20), (30, 30, 30), -1)
    ok, buffer = cv2.imencode(".jpg", page)
    assert ok
    return buffer.tobytes()


@pytest.fixture
def fetch(monkeypatch):
    jpeg = _synthetic_page_jpeg()

    class FakeResponse:
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield jpeg
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(acquire_module.requests, "get",
                        lambda url, **kw: FakeResponse())


def run_line(ledger, library, **kw):
    return Conductor(ledger, library_root=library, workers=2, **kw).run(DOC)


def test_recipe_loads_and_validates():
    recipe = load_recipe("latin_manuscript")
    assert [s.station.name for s in recipe.page_stations] == [
        "acquire", "deframe", "dewatermark", "flatten", "segment", "read",
        "translate", "assemble_page"]
    assert [s.station.name for s in recipe.manuscript_stations] == [
        "survey", "reconstruct", "reference", "emend", "publish", "render_epub"]
    assert recipe.page_stations[5].model  # ${VAR} interpolated


def test_end_to_end_line(ledger, library, gateway, fetch):
    report = run_line(ledger, library)

    assert report.count("failed") == 0
    # 8 page stations × 2 pages + 6 manuscript stations
    assert report.count("ran") == 22

    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", library))
    assert assembled["original"]["text"] == "Experimenta ad morbos"
    assert assembled["translation"]["text"] == "Translated body"
    assert assembled["translation"]["flags"]["starts_mid_sentence"] is False
    assert assembled["provenance"]["station"] == "assemble_page"

    brief = read_json(artifact_path(DOC, "translation_brief", None, library))
    assert brief["glossary"][0]["term"] == "febris"
    assert brief["provenance"]["prompt_sha256"]

    # binary artifacts got provenance sidecars
    image = artifact_path(DOC, "page_image", "f001r", library)
    assert image.exists()
    assert (image.parent / (image.name + ".provenance.json")).exists()

    # translate saw the brief and neighbor context in its prompt
    translate_calls = [c for c in gateway.calls if "TRANSLATION BRIEF" in c.prompt]
    assert len(translate_calls) == 2
    # thread order is nondeterministic — find f001r's call by its page text
    f001r_call = next(
        c for c in translate_calls
        if "Experimenta ad morbos" in c.prompt.split("--- PAGE TO TRANSLATE ---")[1]
    )
    assert "febris" in f001r_call.prompt
    assert "[f001v]" in f001r_call.prompt  # neighbor context present


def test_second_run_is_all_fresh(ledger, library, gateway, fetch):
    run_line(ledger, library)
    calls_before = len(gateway.calls)
    report = run_line(ledger, library)
    assert report.count("ran") == 0
    assert report.count("fresh") == 22
    assert len(gateway.calls) == calls_before  # not a single paid call


def test_refresh_read_cascades_staleness(ledger, library, gateway, fetch):
    run_line(ledger, library)
    gateway.read_texts["f001r"] = "Experimenta CORRECTED"

    report = run_line(ledger, library, refresh=frozenset({"read"}))
    ran = {(c.station, c.page_id) for c in report.cells if c.action == "ran"}
    # both reads re-ran; f001r read changed → survey (consumes all reads),
    # BOTH translations (neighbor context!), both assemblies go stale
    assert ("read", "f001r") in ran and ("read", "f001v") in ran
    assert ("survey", None) in ran
    assert ("translate", "f001r") in ran and ("translate", "f001v") in ran

    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", library))
    assert assembled["original"]["text"] == "Experimenta CORRECTED"


def test_byte_identical_refresh_does_not_cascade(ledger, library, gateway, fetch):
    run_line(ledger, library)
    report = run_line(ledger, library, refresh=frozenset({"read"}))
    ran = {(c.station, c.page_id) for c in report.cells if c.action == "ran"}
    # reads re-ran but produced identical bytes → nothing downstream moved
    assert ran == {("read", "f001r"), ("read", "f001v")}


def test_config_drift_is_outdated_not_rerun(ledger, library, gateway, fetch, tmp_path, monkeypatch):
    run_line(ledger, library)
    # simulate a prompt change: point the read slot at a different prompt text
    from palimpsest.factory import config as factory_config
    prompts = tmp_path / "prompts"
    (prompts / "read" / "la").mkdir(parents=True)
    (prompts / "read" / "la" / "diplomatic.txt").write_text("NEW PROMPT", encoding="utf-8")
    for name in ("survey/generic/brief", "translate/la/with_brief",
                 "reconstruct/generic/structure", "reference/generic/identify",
                 "emend/generic/agent"):
        src = factory_config.PROMPTS_DIR / f"{name}.txt"
        dest = prompts / f"{name}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr("palimpsest.factory.prompt_store.PROMPTS_DIR", prompts)

    report = run_line(ledger, library)
    outdated = {(c.station, c.page_id) for c in report.cells if c.action == "outdated"}
    assert outdated == {("read", "f001r"), ("read", "f001v")}
    assert report.count("ran") == 0  # paid work not silently redone

    report = run_line(ledger, library, refresh=frozenset({"read"}))
    assert ("read", "f001r") in {
        (c.station, c.page_id) for c in report.cells if c.action == "ran"}


def test_failed_page_does_not_stop_line(ledger, library, gateway, fetch, monkeypatch):
    scripted = ScriptedGateway()

    def flaky(request, **kwargs):
        if request.images and getattr(request.images[0], "stem", "") == "f001r":
            raise RuntimeError("boom")
        response = scripted(request)
        return json.loads(response.text), response

    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", flaky)
    report = run_line(ledger, library)

    failed = [(c.station, c.page_id) for c in report.cells if c.action == "failed"]
    # f001r's read failed → its chain stops (translate needs the brief which
    # needs ALL reads, so survey fails on missing input too) — but f001v's
    # read still ran
    assert ("read", "f001r") in failed
    ran = {(c.station, c.page_id) for c in report.cells if c.action == "ran"}
    assert ("read", "f001v") in ran
