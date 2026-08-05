"""Production-canary isolation, freshness, evidence, and validation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from palimpsest.factory.core import registry
from palimpsest.factory.core.conductor import Conductor
from palimpsest.factory.core.ledger import Ledger
from palimpsest.factory.core.contracts import transcription_audit
from palimpsest.factory.core.recipe import load as load_recipe
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.evaluation import canary as canary_module
from palimpsest.factory.evaluation.candidate import content_fingerprint
from palimpsest.factory.evaluation.canary import CanaryError, run_proposal_canary
from palimpsest.factory.evaluation.promotion import RecipeProposal
from palimpsest.factory.workspace.io import atomic_write_json, read_json
from palimpsest.factory.workspace.layout import (
    artifact_path,
    metadata_path,
    page_list_path,
)

DOC_ID = "canary_doc"
RECIPE = "canary_recipe"
PAGE_ID = "f001r"


class CanaryUpstream(Station):
    name = "canary_upstream"
    grain = "manuscript"
    consumes = ("metadata",)
    produces = "translation_brief"

    def run(self, job: Job) -> StationResult:
        return StationResult(
            payload={"document": job.doc_id, "glossary": [], "outline": []},
            cost_usd=0.0,
        )


class CanaryChanged(Station):
    name = "canary_changed"
    grain = "manuscript"
    consumes = ("translation_brief",)
    produces = "manuscript"
    option_keys = frozenset({"marker"})

    def run(self, job: Job) -> StationResult:
        marker = job.config.options["marker"]
        if marker == "fail":
            raise RuntimeError("proposed cell failed")
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "sections": [{"marker": marker}],
                "joins": [],
                "readers_note": "Canary reader note.",
            },
            cost_usd=None if marker == "unknown" else 0.25,
        )


class CanaryBook(Station):
    name = "canary_book"
    grain = "manuscript"
    consumes = ("manuscript", "metadata", "page_list")
    produces = "book"

    def run(self, job: Job) -> StationResult:
        fingerprint = "0" * 16
        return StationResult(
            payload={
                "schema_version": 1,
                "profile": "facsimile-spread/v1",
                "doc_id": job.doc_id,
                "identity": {
                    "title": "Canary Book",
                    "author": "Test Author",
                    "archive": "Test Archive",
                    "shelfmark": None,
                    "date": None,
                },
                "languages": {"translation": "en", "original": "la"},
                "readers_note": "Canary reader note.",
                "folios": [
                    {
                        "page_id": PAGE_ID,
                        "order": 1,
                        "images": {
                            "original": {
                                "kind": "page_image",
                                "page_id": PAGE_ID,
                                "source_url": "https://example.test/f001r.jpg",
                                "fingerprint": fingerprint,
                            }
                        },
                        "evidence": {
                            "diplomatic": {
                                "text": "Canarium.",
                                "audit": transcription_audit(
                                    {"candidate_readings": []}
                                ),
                                "source": {
                                    "kind": "page_transcription",
                                    "pointer": "/text",
                                    "fingerprint": fingerprint,
                                },
                            },
                            "translation": {
                                "text": "A translated canary.",
                                "notes": "",
                                "flags": {},
                                "seam": None,
                                "source": {
                                    "kind": "page_translation",
                                    "pointer": "/translation",
                                    "fingerprint": fingerprint,
                                },
                            },
                        },
                    }
                ],
                "sections": [
                    {
                        "id": "section-0001",
                        "order": 1,
                        "heading": "Canary Chapter",
                        "folio_ids": [PAGE_ID],
                        "content": {
                            "translation": {
                                "text": "A translated canary.",
                                "source": {
                                    "kind": "edition",
                                    "pointer": "/sections/0/translation",
                                    "fingerprint": fingerprint,
                                },
                            },
                            "emended_reading": {
                                "text": "Canarium.",
                                "source": {
                                    "kind": "emendations",
                                    "pointer": "/sections/0/emended",
                                    "fingerprint": fingerprint,
                                },
                            },
                            "diplomatic_transcription": {
                                "text": "Canarium.",
                                "source": {
                                    "kind": "manuscript",
                                    "pointer": "/sections/0/reading",
                                    "fingerprint": fingerprint,
                                },
                            },
                        },
                        "apparatus_ids": [],
                    }
                ],
                "apparatus": [],
                "colophon": {
                    "pipeline": [
                        {
                            "station": "canary_changed",
                            "runs": 1,
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "cost_usd": 0.25,
                            "cost_complete": True,
                            "cost_usd_known": 0.25,
                            "configurations": [],
                        }
                    ],
                    "cost_usd_total": 0.25,
                    "cost_usd_known": 0.25,
                    "cost_complete": True,
                    "pages": 1,
                },
            },
            cost_usd=0.0,
        )


def _source(marker: str, *, render_epub: bool = True) -> str:
    render = "  - station: render_epub\n" if render_epub else ""
    return (
        f"name: {RECIPE}\n"
        "language: la\n"
        "line:\n"
        "  - station: canary_upstream\n"
        "  - station: canary_changed\n"
        f"    marker: {marker}\n"
        "  - station: canary_book\n"
        f"{render}"
    )


def _proposal(current_source: str, proposed_source: str) -> RecipeProposal:
    payload = {
        "schema_version": 1,
        "action": "promote",
        "recipe": RECIPE,
        "station": "canary_changed",
        "current_recipe_hash": hashlib.sha256(current_source.encode()).hexdigest(),
        "proposed_recipe_hash": hashlib.sha256(proposed_source.encode()).hexdigest(),
        "previous_candidate": "canary_changed/baseline",
        "previous_candidate_fingerprint": "1" * 64,
        "next_candidate": "canary_changed/proposed",
        "next_candidate_fingerprint": "2" * 64,
        "evaluation_run": "evaluation-run",
        "report_fingerprint": "3" * 64,
        "source_promotion_id": None,
        "waiver_fingerprint": None,
        "proposed_source": proposed_source,
    }
    return RecipeProposal(
        proposal_id=content_fingerprint(payload),
        **payload,  # type: ignore[arg-type]
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, marker: str = "proposed"
) -> tuple[RecipeProposal, Path, Path, Path]:
    registry.get("render_epub")
    monkeypatch.setitem(
        registry._STATIONS, "canary_upstream", {"default": CanaryUpstream()}
    )
    monkeypatch.setitem(
        registry._STATIONS, "canary_changed", {"default": CanaryChanged()}
    )
    monkeypatch.setitem(registry._STATIONS, "canary_book", {"default": CanaryBook()})

    library_root = tmp_path / "production-library"
    recipe_root = tmp_path / "production-recipes"
    db_path = tmp_path / "production.db"
    recipe_root.mkdir()
    current_source = _source("baseline")
    (recipe_root / f"{RECIPE}.yaml").write_bytes(current_source.encode("utf-8"))

    atomic_write_json(
        metadata_path(DOC_ID, library_root),
        {"doc_id": DOC_ID, "title": "Canary Book"},
    )
    atomic_write_json(
        page_list_path(DOC_ID, library_root),
        {
            "doc_id": DOC_ID,
            "pages": [
                {
                    "page_id": PAGE_ID,
                    "url": "https://example.test/f001r.jpg",
                    "order": 1,
                }
            ],
        },
    )
    for kind in ("page_image", "page_image_clean"):
        image = artifact_path(DOC_ID, kind, PAGE_ID, library_root)
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"isolated canary image")

    with Ledger(db_path) as ledger:
        ledger.adopt(DOC_ID, recipe=RECIPE)
        report = Conductor(
            ledger,
            library_root=library_root,
            workers=1,
            recipe_loader=lambda name: load_recipe(name, recipes_dir=recipe_root),
        ).run(DOC_ID)
    assert report.count("failed") == 0
    return (
        _proposal(current_source, _source(marker)),
        library_root,
        db_path,
        recipe_root,
    )


def test_canary_runs_exact_proposal_with_isolated_freshness_and_validates_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal, library_root, db_path, recipe_root = _canonical(tmp_path, monkeypatch)
    production_workspace = _tree_bytes(library_root)
    production_db = db_path.read_bytes()
    production_recipe = (recipe_root / f"{RECIPE}.yaml").read_bytes()
    canary_root = tmp_path / "canary"
    conductor_options: dict[str, object] = {}

    def capture_conductor(*args: object, **kwargs: object) -> Conductor:
        conductor_options.update(kwargs)
        return Conductor(*args, **kwargs)

    monkeypatch.setattr(canary_module, "Conductor", capture_conductor)

    evidence = run_proposal_canary(
        proposal,
        doc_id=DOC_ID,
        canary_root=canary_root,
        library_root=library_root,
        db_path=db_path,
        recipe_root=recipe_root,
        workers=1,
    )
    assert conductor_options["workers"] == 1
    assert conductor_options["model_workers"] == 1

    assert evidence.status == "passed"
    assert evidence.recipe_hash == proposal.proposed_recipe_hash
    assert evidence.refreshed_station == proposal.station
    assert evidence.known_cost_usd == pytest.approx(0.25)
    assert not evidence.unknown_cost
    assert (evidence.book_valid, evidence.epub_valid, evidence.site_valid) == (
        True,
        True,
        True,
    )
    assert all(outcome.status == "passed" for outcome in evidence.downstream_outcomes)
    manuscript = read_json(canary_root / "library" / DOC_ID / "manuscript.json")
    assert manuscript["sections"] == [{"marker": "proposed"}]
    run_report = read_json(canary_root / "run-report.json")
    actions = {cell["station"]: cell["action"] for cell in run_report["cells"]}
    assert actions == {
        "canary_upstream": "recovered",
        "canary_changed": "ran",
        "canary_book": "ran",
        "render_epub": "recovered",
    }
    assert (canary_root / "recipes" / f"{RECIPE}.yaml").read_text(
        encoding="utf-8"
    ) == proposal.proposed_source
    assert _tree_bytes(library_root) == production_workspace
    assert db_path.read_bytes() == production_db
    assert (recipe_root / f"{RECIPE}.yaml").read_bytes() == production_recipe


@pytest.mark.parametrize(
    ("marker", "expected_status", "unknown_cost"),
    [("fail", "failed", True), ("unknown", "unknown", True)],
)
def test_canary_keeps_failed_and_unknown_execution_evidence_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    expected_status: str,
    unknown_cost: bool,
) -> None:
    proposal, library_root, db_path, recipe_root = _canonical(
        tmp_path, monkeypatch, marker=marker
    )

    evidence = run_proposal_canary(
        proposal,
        doc_id=DOC_ID,
        canary_root=tmp_path / "canary",
        library_root=library_root,
        db_path=db_path,
        recipe_root=recipe_root,
        workers=1,
    )

    assert evidence.status == expected_status
    assert evidence.unknown_cost is unknown_cost
    changed = next(
        outcome
        for outcome in evidence.downstream_outcomes
        if outcome.name == "canary_changed"
    )
    assert changed.status == ("failed" if marker == "fail" else "passed")
    if marker == "fail":
        assert any(
            outcome.status == "failed" for outcome in evidence.downstream_outcomes
        )
    assert (tmp_path / "canary" / "run-report.json").is_file()


def test_unavailable_required_terminal_validation_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal, library_root, db_path, recipe_root = _canonical(tmp_path, monkeypatch)
    monkeypatch.setattr(canary_module, "_validate_epub", lambda *args: None)

    evidence = run_proposal_canary(
        proposal,
        doc_id=DOC_ID,
        canary_root=tmp_path / "canary",
        library_root=library_root,
        db_path=db_path,
        recipe_root=recipe_root,
        workers=1,
    )

    assert evidence.epub_valid is None
    assert evidence.status == "unknown"


@pytest.mark.parametrize("case", ["hash", "document"])
def test_invalid_identity_or_document_refuses_before_canary_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    proposal, library_root, db_path, recipe_root = _canonical(tmp_path, monkeypatch)
    if case == "hash":
        proposal = replace(proposal, proposed_recipe_hash="f" * 64)
        doc_id = DOC_ID
    else:
        doc_id = "wrong_doc"
    canary_root = tmp_path / "canary"
    before_workspace = _tree_bytes(library_root)
    before_db = db_path.read_bytes()
    before_recipe = (recipe_root / f"{RECIPE}.yaml").read_bytes()

    with pytest.raises((CanaryError, ValueError)):
        run_proposal_canary(
            proposal,
            doc_id=doc_id,
            canary_root=canary_root,
            library_root=library_root,
            db_path=db_path,
            recipe_root=recipe_root,
            workers=1,
        )

    assert not canary_root.exists()
    assert _tree_bytes(library_root) == before_workspace
    assert db_path.read_bytes() == before_db
    assert (recipe_root / f"{RECIPE}.yaml").read_bytes() == before_recipe
