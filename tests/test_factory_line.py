"""Recipe validation and conductor behavior on a deterministic fake document.

The gateway is monkeypatched (no network, no API key); acquire is fed by a
local HTTP-free fetch stub. Everything else — stations, conductor, ledger,
fingerprints, staleness — runs for real.
"""

from __future__ import annotations

import json

import pytest

from palimpsest.cli import build_parser
import palimpsest.factory.stations.acquire as acquire_module
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import ModelResponse
from palimpsest.factory.stations.assemble_page import AssemblePage
from palimpsest.factory.stations.translate import Translate
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
    atomic_write_json(
        doc_dir / "metadata.json",
        {
            "doc_id": DOC,
            "source_catalog": {"title": "Test", "archive": "Test Archive"},
        },
    )
    return tmp_path / "library"


@pytest.fixture
def ledger(library):
    with Ledger(library / "factory.db") as ledger:
        ledger.adopt(DOC, recipe="latin_manuscript")
        yield ledger


RECONSTRUCT_PLAN = {
    "sections": [{"heading": "Remedies", "from_page": "f001r", "to_page": "f001v"}],
    "joins": [
        {
            "from_page": "f001r",
            "to_page": "f001v",
            "kind": "sentence_continuation",
            "rationale": "flags say so",
        }
    ],
    "readers_note": "A small test codex of remedies.",
}

TEXT_STATIONS = ("translate",)  # call generate()
JSON_STATIONS = ("read", "survey", "reconstruct")  # call generate_json()


class ScriptedGateway:
    """Deterministic fake: reply depends on which prompt/station is calling."""

    def __init__(self):
        self.calls = []
        self.read_texts = {
            "f001r": "Experimenta ad morbos",
            "f001v": "Ad febres tertianas",
        }

    def __call__(self, request):
        self.calls.append(request)
        if request.images:  # read station
            page_id = request.images[0].stem
            text = json.dumps({"transcription": self.read_texts[page_id]})
        elif request.json_output and "reconstructing the structure" in request.prompt:
            text = json.dumps(RECONSTRUCT_PLAN)
        elif request.json_output:  # survey station
            text = json.dumps(
                {
                    "terms": [{"term": "febris", "translation": "fever", "note": ""}],
                    "sections": [],
                    "abbreviations": [],
                    "entities": [],
                    "flags": [],
                    "style_notes": ["Medieval Latin"],
                }
            )
        else:  # translate station
            text = (
                "Translated body\n---FLAGS---\n"
                '{"starts_mid_sentence": false, "ends_mid_sentence": false, "new_terms": []}'
                "\n---END FLAGS---"
            )
        return ModelResponse(
            text=text,
            model=request.model,
            prompt_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
        )


@pytest.fixture
def gateway(monkeypatch):
    from palimpsest.factory import agent_cell

    from palimpsest.factory.gateway.client import parse_json_response

    fake = ScriptedGateway()

    def fake_json(request, **kwargs):
        response = fake(request)
        return parse_json_response(response.text), response

    def fake_agent_run(workspace, task, model, timeout_s=0, executor="codex"):
        # scripted stand-ins for the three editorial agents: reference emits
        # an empty dossier, emend applies one covered correction, and the
        # final editor reconciles the reader-facing translation
        out = workspace / "out"
        if "out/reference.json" in task:
            (out / "reference.json").write_text(
                json.dumps(
                    {
                        "identification": {"work": "Test codex", "tradition": "test"},
                        "reference_points": [],
                        "editorial_notes": [],
                    }
                ),
                encoding="utf-8",
            )
        elif "out/edition.json" in task:
            evidence = json.loads(
                (workspace / "evidence" / "emendations.json").read_text(
                    encoding="utf-8"
                )
            )
            (out / "edition.json").write_text(
                json.dumps(
                    {
                        "readers_note": "Final reader's note.",
                        "sections": [
                            {
                                "section_index": index,
                                "heading": section["heading"],
                                "translation": f"Final translation of {section['reading']}",
                            }
                            for index, section in enumerate(evidence["sections"])
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        else:
            evidence = json.loads(
                (workspace / "evidence" / "manuscript.json").read_text(encoding="utf-8")
            )
            (out / "emendations.json").write_text(
                json.dumps(
                    {
                        "sections": [
                            {
                                "heading": s["heading"],
                                "reading": s["original"].replace(
                                    "morbos", "morbos EMENDED"
                                ),
                            }
                            for s in evidence["sections"]
                        ],
                        "apparatus": [
                            {
                                "section": s["heading"],
                                "original": "morbos",
                                "emended": "morbos EMENDED",
                                "reason": "test emendation",
                                "evidence": "structure",
                            }
                            for s in evidence["sections"]
                            if "morbos" in s["original"]
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return agent_cell.AgentRun(
            session_id="00000000-0000-0000-0000-000000000000",
            tokens=100,
            log_path=out / "agent_run.log",
        )

    def fail_resume(*args, **kwargs):
        raise AssertionError("verifier rejected the scripted emendation")

    for module in TEXT_STATIONS:
        monkeypatch.setattr(f"palimpsest.factory.stations.{module}.generate", fake)
    for module in JSON_STATIONS:
        monkeypatch.setattr(
            f"palimpsest.factory.stations.{module}.generate_json", fake_json
        )
    monkeypatch.setattr("palimpsest.factory.agent_cell.run", fake_agent_run)
    monkeypatch.setattr("palimpsest.factory.agent_cell.resume", fail_resume)
    return fake


def test_translate_signature_distinguishes_target_pages(library):
    pages = tuple(PAGES)
    station = Translate()
    first = Job(
        doc_id=DOC,
        pages=pages,
        page=pages[0],
        library_root=library,
        config=StationConfig(options={"overlap": 1}),
    )
    second = Job(
        doc_id=DOC,
        pages=pages,
        page=pages[1],
        library_root=library,
        config=StationConfig(options={"overlap": 1}),
    )

    assert station.input_paths(first) == station.input_paths(second)
    assert station.signature_extras(first) != station.signature_extras(second)


def _synthetic_page_jpeg() -> bytes:
    """A light page: white 800×600 with thin text-like strokes — thin, because
    the adaptive ink mask only marks stroke-scale features (solid bars read
    as outlines) — with enough ink to clear the full-page routing floor."""
    import cv2
    import numpy as np

    page = np.full((800, 600, 3), 235, np.uint8)
    for row in range(10):
        cv2.rectangle(
            page, (120, 180 + row * 20), (480, 188 + row * 20), (30, 30, 30), -1
        )
    ok, buffer = cv2.imencode(".jpg", page)
    assert ok
    return buffer.tobytes()


@pytest.fixture
def fetch(monkeypatch):
    jpeg = _synthetic_page_jpeg()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield jpeg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        acquire_module.requests, "get", lambda url, **kw: FakeResponse()
    )


def run_line(ledger, library, **kw):
    return Conductor(ledger, library_root=library, workers=2, **kw).run(DOC)


@pytest.fixture
def dual_read_models(monkeypatch):
    monkeypatch.setenv("PALIMPSEST_MODEL_READING", "openai-codex/gpt-5.6-sol")
    monkeypatch.setenv("PALIMPSEST_MODEL_READING_SECONDARY", "google/gemini-3.6-flash")
    monkeypatch.setenv("PALIMPSEST_MODEL_EDITORIAL", "anthropic/claude-fable-5")
    monkeypatch.setenv("PALIMPSEST_MODEL_ADJUDICATOR", "anthropic/claude-fable-5")


def test_recipe_loads_and_validates(dual_read_models):
    recipe = load_recipe("latin_manuscript")
    assert [spec.station.name for spec in recipe.steps] == [
        "acquire",
        "deframe",
        "dewatermark",
        "flatten",
        "segment",
        "read",
        "survey",
        "translate",
        "assemble_page",
        "reconstruct",
        "reference",
        "emend",
        "finalize_edition",
        "publish",
        "render_epub",
    ]
    read = recipe.steps[5]
    assert read.model == "openai-codex/gpt-5.6-sol"
    assert read.params == {
        "temperature": 0.7,
        "media_resolution": "low",
        "max_output_tokens": 32768,
        "thinking_level": "low",
        "secondary_model": "google/gemini-3.6-flash",
        "secondary_thinking_level": None,
        "adjudicator_model": "anthropic/claude-fable-5",
        "adjudicator_thinking_level": "high",
    }
    assert recipe.steps[6].model == "anthropic/claude-fable-5"
    assert recipe.steps[7].model == "anthropic/claude-fable-5"
    assert recipe.steps[9].model == "anthropic/claude-fable-5"


def test_recipe_rejects_duplicate_artifact_producers(tmp_path):
    (tmp_path / "duplicate.yaml").write_text(
        """
name: duplicate
line:
  - station: acquire
  - station: acquire
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="produces 'page_image' twice"):
        load_recipe("duplicate", recipes_dir=tmp_path)


def test_recipe_rejects_consumers_before_their_producers(tmp_path):
    (tmp_path / "out_of_order.yaml").write_text(
        """
name: out_of_order
line:
  - station: survey
    model: test-model
    prompt: survey/generic/brief
  - station: read
    model: test-model
    prompt: read/la/diplomatic
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="before any earlier station produces"):
        load_recipe("out_of_order", recipes_dir=tmp_path)


def test_chinese_recipe_loads_and_validates(dual_read_models):
    recipe = load_recipe("chinese_scroll")
    assert [spec.station.name for spec in recipe.steps] == [
        "acquire",
        "deframe",
        "dewatermark",
        "flatten",
        "segment",
        "read",
        "align",
        "survey",
        "translate",
        "assemble_page",
        "reconstruct",
        "reference",
        "emend",
        "finalize_edition",
        "publish",
        "render_epub",
    ]
    read = recipe.steps[5]
    assert read.model == "openai-codex/gpt-5.6-sol"
    assert read.params == {
        "temperature": 0.1,
        "media_resolution": "high",
        "max_output_tokens": 32768,
        "thinking_level": "low",
        "secondary_model": "google/gemini-3.6-flash",
        "secondary_thinking_level": None,
        "adjudicator_model": "anthropic/claude-fable-5",
        "adjudicator_thinking_level": "high",
    }
    assert recipe.steps[8].options == {
        "overlap": 1,
        "trim_seam_overlap": True,
    }


def test_chinese_printed_book_recipe_disables_scroll_seam_trimming(
    dual_read_models,
):
    recipe = load_recipe("chinese_printed_book")
    assert [spec.station.name for spec in recipe.steps] == [
        "acquire",
        "deframe",
        "dewatermark",
        "flatten",
        "segment",
        "read",
        "align",
        "survey",
        "translate",
        "assemble_page",
        "reconstruct",
        "reference",
        "emend",
        "finalize_edition",
        "publish",
        "render_epub",
    ]
    read = recipe.steps[5]
    assert read.model == "openai-codex/gpt-5.6-sol"
    assert read.params["secondary_model"] == "google/gemini-3.6-flash"
    assert recipe.steps[7].model == "anthropic/claude-fable-5"
    translate = recipe.steps[8]
    assert translate.options == {"overlap": 1}


def test_assemble_page_applies_the_translation_seam(tmp_path):
    library_root = tmp_path / "library"
    transcription_path = artifact_path(DOC, "page_transcription", "p2", library_root)
    translation_path = artifact_path(DOC, "page_translation", "p2", library_root)
    atomic_write_json(
        transcription_path,
        {
            "text": "duplicate one\nduplicate two\nkept text",
            "page_seq": 2,
            "regions": [],
        },
    )
    atomic_write_json(
        translation_path,
        {
            "translation": "kept translation",
            "seam": {
                "lines": 2,
                "similarity": 0.9,
                "dropped_text": "duplicate one\nduplicate two",
            },
        },
    )
    pages = (
        {"page_id": "p1", "order": 1},
        {"page_id": "p2", "order": 2},
    )
    job = Job(
        doc_id=DOC,
        pages=pages,
        page=pages[1],
        library_root=library_root,
        config=StationConfig(),
    )

    station = AssemblePage()
    result = station.run(job)

    assert result.payload["original"]["text"] == "kept text"
    assert result.payload["original"]["seam"]["lines"] == 2
    assert station.input_paths(job) == [transcription_path, translation_path]

    atomic_write_json(
        translation_path,
        {
            "translation": "invalid translation",
            "seam": {"lines": 1, "dropped_text": "not the transcription"},
        },
    )
    with pytest.raises(ValueError, match="seam does not match"):
        station.run(job)


def test_end_to_end_line(ledger, library, gateway, fetch):
    report = run_line(ledger, library)

    assert report.count("failed") == 0
    # 8 page stations × 2 pages + 7 manuscript stations
    assert report.count("ran") == 23
    assert ledger.item(DOC)["status"] == "complete"

    assembled = read_json(artifact_path(DOC, "page_assembled", "f001r", library))
    assert assembled["original"]["text"] == "Experimenta ad morbos"
    assert assembled["translation"]["text"] == "Translated body"
    assert assembled["translation"]["flags"]["starts_mid_sentence"] is False
    assert assembled["provenance"]["station"] == "assemble_page"

    brief = read_json(artifact_path(DOC, "translation_brief", None, library))
    assert brief["glossary"][0]["term"] == "febris"
    assert brief["provenance"]["prompt_sha256"]
    edition = read_json(artifact_path(DOC, "edition", None, library))
    assert edition["sections"][0]["translation"].startswith("Final translation of")
    book = read_json(artifact_path(DOC, "book", None, library))
    content = book["sections"][0]["content"]
    assert content["translation"]["text"] == edition["sections"][0]["translation"]
    assert "morbos EMENDED" in content["emended_reading"]["text"]

    # binary artifacts got provenance sidecars
    image = artifact_path(DOC, "page_image", "f001r", library)
    assert image.exists()
    assert (image.parent / (image.name + ".provenance.json")).exists()

    # translate saw the brief and neighbor context in its prompt
    translate_calls = [c for c in gateway.calls if "TRANSLATION BRIEF" in c.prompt]
    assert len(translate_calls) == 2
    # thread order is nondeterministic — find f001r's call by its page text
    f001r_call = next(
        c
        for c in translate_calls
        if "Experimenta ad morbos" in c.prompt.split("--- PAGE TO TRANSLATE ---")[1]
    )
    assert "febris" in f001r_call.prompt
    assert "[f001v]" in f001r_call.prompt  # neighbor context present


def test_cli_partial_run_reports_unknown_cost(ledger, library, fetch, capsys):
    args = build_parser().parse_args(
        [
            "run",
            "--db",
            str(library / "factory.db"),
            "--library-root",
            str(library),
            "--doc-id",
            DOC,
            "--page",
            "f001r",
            "--through",
            "acquire",
            "--workers",
            "1",
        ]
    )

    args.func(args)

    output = capsys.readouterr().out
    assert "scope=partial" in output
    assert "ran=1" in output
    assert "cost=unknown" in output


def test_page_selected_run_stops_inclusively_and_resumes_full_line(
    ledger, library, gateway, fetch
):
    partial = run_line(
        ledger,
        library,
        page_ids=("f001v",),
        through="read",
    )

    assert partial.partial is True
    assert [
        (cell.station, cell.page_id) for cell in partial.cells if cell.action == "ran"
    ] == [
        ("acquire", "f001v"),
        ("deframe", "f001v"),
        ("dewatermark", "f001v"),
        ("flatten", "f001v"),
        ("segment", "f001v"),
        ("read", "f001v"),
    ]
    assert ledger.item(DOC)["status"] == "active"

    completed = run_line(ledger, library)

    assert completed.partial is False
    assert completed.count("fresh") == 6
    assert completed.count("ran") == 17
    assert ledger.item(DOC)["status"] == "complete"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"through": "missing"}, "Unknown --through station"),
        ({"page_ids": ("missing",), "through": "read"}, "Unknown --page ids"),
        (
            {"page_ids": ("f001r",), "through": "translate"},
            "cannot cross manuscript station",
        ),
    ],
)
def test_invalid_partial_scope_does_not_claim_or_fail_work_order(
    ledger, library, options, message
):
    with pytest.raises(ValueError, match=message):
        run_line(ledger, library, **options)

    assert ledger.item(DOC)["status"] == "active"


def test_second_run_is_all_fresh(ledger, library, gateway, fetch):
    run_line(ledger, library)
    calls_before = len(gateway.calls)
    report = run_line(ledger, library)
    assert report.count("ran") == 0
    assert report.count("fresh") == 23
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


def test_config_drift_is_outdated_not_rerun(
    ledger, library, gateway, fetch, tmp_path, monkeypatch
):
    run_line(ledger, library)
    # simulate a prompt change: point the read slot at a different prompt text
    from palimpsest.factory import config as factory_config

    prompts = tmp_path / "prompts"
    (prompts / "read" / "la").mkdir(parents=True)
    (prompts / "read" / "la" / "diplomatic.txt").write_text(
        "NEW PROMPT", encoding="utf-8"
    )
    for name in (
        "survey/generic/brief",
        "translate/la/with_brief",
        "reconstruct/generic/structure",
        "reference/generic/identify",
        "emend/generic/agent",
        "finalize/generic/edition",
    ):
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
        (c.station, c.page_id) for c in report.cells if c.action == "ran"
    }


def test_failed_page_stops_at_batch_barrier(
    ledger, library, gateway, fetch, monkeypatch
):
    scripted = ScriptedGateway()

    def flaky(request, **kwargs):
        if request.images and getattr(request.images[0], "stem", "") == "f001r":
            raise RuntimeError("boom")
        response = scripted(request)
        return json.loads(response.text), response

    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", flaky)
    report = run_line(ledger, library)

    failed = [(c.station, c.page_id) for c in report.cells if c.action == "failed"]
    assert failed == [("read", "f001r")]
    ran = {(c.station, c.page_id) for c in report.cells if c.action == "ran"}
    assert ("read", "f001v") in ran
    assert not {"survey", "translate", "assemble_page"} & {
        cell.station for cell in report.cells
    }
    assert ledger.item(DOC)["status"] == "failed"


def test_failed_refresh_cannot_feed_old_artifacts_downstream(
    ledger, library, gateway, fetch, monkeypatch
):
    run_line(ledger, library)
    scripted = ScriptedGateway()

    def flaky(request, **kwargs):
        if request.images and getattr(request.images[0], "stem", "") == "f001r":
            raise RuntimeError("boom")
        response = scripted(request)
        return json.loads(response.text), response

    monkeypatch.setattr("palimpsest.factory.stations.read.generate_json", flaky)
    report = run_line(ledger, library, refresh=frozenset({"read"}))

    assert ("read", "f001r") in {
        (cell.station, cell.page_id) for cell in report.cells if cell.action == "failed"
    }
    assert not {"survey", "translate", "assemble_page"} & {
        cell.station for cell in report.cells
    }
