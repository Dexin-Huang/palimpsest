from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from palimpsest.factory import site
from palimpsest.factory.core import registry as station_registry
from palimpsest.factory.core.contracts import contract, validate_payload
from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.metrics import MetricDirection, MetricRegistry
from palimpsest.factory.evaluation.station_metrics.deterministic import (
    collect_site_conformance,
    register_deterministic_metrics,
)
from palimpsest.factory.evaluation.suite import (
    CaseAsset,
    load_suite,
    validate_candidate_suite,
)
from palimpsest.factory.stations.assemble_page import AssemblePage
from palimpsest.factory.stations.publish import Publish
from palimpsest.factory.stations.render_epub import RenderEpub
from palimpsest.factory.workspace.io import atomic_write_json
from palimpsest.factory.workspace.layout import artifact_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = PROJECT_ROOT / "palimpsest" / "factory" / "evaluation"
CANDIDATE_ROOT = PROJECT_ROOT / "palimpsest" / "factory" / "candidates"


def _read_json(relative: str) -> dict[str, object]:
    value = json.loads((EVALUATION_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _copy_input(
    source_relative: str, library_root: Path, kind: str, page_id: str | None = None
) -> None:
    source = EVALUATION_ROOT / source_relative
    destination = artifact_path("fixture_ms", kind, page_id, library_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _assert_asset_hash(asset: CaseAsset | Mapping[str, CaseAsset]) -> None:
    if isinstance(asset, Mapping):
        for page_asset in asset.values():
            _assert_asset_hash(page_asset)
        return
    assert asset.path is not None
    assert (
        hashlib.sha256((EVALUATION_ROOT / asset.path).read_bytes()).hexdigest()
        == asset.sha256
    )


def test_deterministic_station_and_library_conformance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"network": 0, "model": 0}

    def reject_network(*_args: object, **_kwargs: object) -> None:
        calls["network"] += 1
        raise AssertionError("deterministic evaluation must not call the network")

    def reject_model(*_args: object, **_kwargs: object) -> None:
        calls["model"] += 1
        raise AssertionError("deterministic evaluation must not call a model")

    monkeypatch.setattr("requests.sessions.Session.request", reject_network)
    monkeypatch.setattr("palimpsest.factory.gateway.client.generate", reject_model)
    monkeypatch.setattr("palimpsest.factory.gateway.client.generate_json", reject_model)

    metrics = MetricRegistry()
    register_deterministic_metrics(metrics)
    assert len(metrics.all()) == 22

    suites = {}
    candidates = {}
    resources = {
        "acquire": "retrieval-conformance-v1.yaml",
        "assemble_page": "assembly-conformance-v1.yaml",
        "publish": "book-conformance-v2.yaml",
        "render_epub": "portable-conformance-v2.yaml",
    }
    for station, suite_name in resources.items():
        suite_record = load_suite(
            EVALUATION_ROOT / "suites" / station / suite_name,
            metric_resolver=metrics,
            probe_resolver={},
            judge_resolver={},
            verify_local=True,
        )
        candidate = load_candidate(
            CANDIDATE_ROOT / station / "current-default-v1.yaml",
            registry=station_registry,
        )
        validate_candidate_suite(candidate, suite_record)
        assert candidate.station == suite_record.station == station
        assert candidate.variant == "default"
        assert candidate.model is None and candidate.prompt_name is None
        assert candidate.produces == station_registry.get(station, "default").produces
        assert (
            suite_record.promotion.minimum_completed_cases
            == len(suite_record.cases)
            == 1
        )
        assert suite_record.qualification_eligible is False
        for metric in (*suite_record.primary_metrics, *suite_record.hard_limits):
            definition = metrics.get(metric.name)
            if hasattr(metric, "direction"):
                assert metric.direction == definition.direction.value
        for case in suite_record.cases:
            for asset in (*case.inputs.values(), *case.references.values()):
                _assert_asset_hash(asset)
            for kind in case.inputs:
                assert contract(kind).kind == kind
        suites[station] = suite_record
        candidates[station] = candidate

    assert candidates["acquire"].consumes == ("page_list",)
    assert candidates["assemble_page"].consumes == (
        "page_transcription",
        "page_translation",
    )
    assert candidates["render_epub"].consumes == ("book",)
    assert set(candidates["publish"].consumes) == {
        "metadata",
        "manuscript",
        "translation_brief",
        "page_transcription",
        "page_image",
        "page_image_clean",
        "page_translation",
        "reference",
        "emendations",
        "edition",
    }

    acquire_gold = _read_json("gold/acquire/expected-retrieval.json")
    expected_image = (EVALUATION_ROOT / "gold/acquire/expected-page.jpg").read_bytes()
    assert hashlib.sha256(expected_image).hexdigest() == acquire_gold["content_sha256"]
    acquire_good = {
        "content_sha256": hashlib.sha256(expected_image).hexdigest(),
        "requested_url": acquire_gold["source_url"],
        "delivered_url": acquire_gold["source_url"],
        "http_status": 200,
        "media_type": "image/jpeg; charset=binary",
        "attempts": [
            {"status": 503, "published": False},
            {"status": 200, "published": True},
        ],
        "partial_published": False,
    }
    acquire_broken = {
        **acquire_good,
        "content_sha256": "0" * 64,
        "delivered_url": "https://example.invalid/wrong-page.jpg",
        "http_status": 206,
        "attempts": [
            {"status": 503, "published": True},
            {"status": 200, "published": True},
        ],
        "partial_published": True,
    }
    for name in (
        "acquire_byte_identity",
        "acquire_source_identity",
        "acquire_retry_conformance",
    ):
        assert metrics.observe(name, acquire_good, acquire_gold) == 1.0
        assert metrics.observe(name, acquire_broken, acquire_gold) == 0.0

    assembly_root = tmp_path / "assembly-library"
    _copy_input(
        "gold/assemble_page/page-transcription.json",
        assembly_root,
        "page_transcription",
        "p001",
    )
    _copy_input(
        "gold/assemble_page/page-translation.json",
        assembly_root,
        "page_translation",
        "p001",
    )
    page = dict(suites["assemble_page"].cases[0].pages[0])
    assembled_good = (
        AssemblePage()
        .run(Job("fixture_ms", (page,), page, assembly_root, StationConfig()))
        .payload
    )
    assert assembled_good is not None
    validate_payload("page_assembled", assembled_good)
    assembled_gold = _read_json("gold/assemble_page/expected-page-assembled.json")
    assert assembled_good == assembled_gold
    assembled_broken = copy.deepcopy(assembled_good)
    assembled_broken["original"]["text"] = "gamma beta beta"
    assembled_broken["original"]["seam"] = {"dropped_text": "unsupported"}
    assembled_broken["translation"]["text"] = "Gamma. Alpha Alpha"
    for name in (
        "assembled_source_identity",
        "assembled_translation_identity",
        "assembled_seam_correctness",
        "assembled_order_integrity",
    ):
        assert metrics.observe(name, assembled_good, assembled_gold) == 1.0
        assert metrics.observe(name, assembled_broken, assembled_gold) == 0.0
    for name in ("assembled_duplication_rate", "assembled_omission_rate"):
        assert metrics.get(name).direction is MetricDirection.MINIMIZE
        assert metrics.observe(name, assembled_good, assembled_gold) == 0.0
        assert metrics.observe(name, assembled_broken, assembled_gold) > 0.0

    publish_root = tmp_path / "publish-library"
    publish_assets = {
        "metadata": ("metadata.json", None),
        "translation_brief": ("translation-brief.json", None),
        "manuscript": ("manuscript.json", None),
        "page_transcription": ("page-transcription.json", "p001"),
        "page_image": ("page-image-clean.jpg", "p001"),
        "page_image_clean": ("page-image-clean.jpg", "p001"),
        "page_translation": ("page-translation.json", "p001"),
        "reference": ("reference.json", None),
        "emendations": ("emendations.json", None),
        "edition": ("edition.json", None),
    }
    for kind, (filename, page_id) in publish_assets.items():
        _copy_input(f"gold/publish/{filename}", publish_root, kind, page_id)
    book_good = (
        Publish()
        .run(
            Job(
                "fixture_ms",
                (page,),
                None,
                publish_root,
                StationConfig(options={"original_language": "la"}),
            )
        )
        .payload
    )
    assert book_good is not None
    validate_payload("book", book_good)
    expected_book = _read_json("gold/publish/expected-book-v2.json")
    assert book_good == expected_book
    book_gold = {
        **expected_book,
        "required_source_pages": ["p001"],
        "required_stations": [
            "read",
            "translate",
            "survey",
            "reconstruct",
            "reference",
            "emend",
            "finalize_edition",
        ],
    }
    book_broken = copy.deepcopy(book_good)
    del book_broken["schema_version"]
    book_broken["sections"][0]["content"]["translation"]["text"] = (
        "unsupported replacement"
    )
    book_broken["folios"] = []
    book_broken["colophon"]["pipeline"] = []
    del book_broken["colophon"]["cost_complete"]
    for name in (
        "book_schema_validity",
        "book_content_identity",
        "book_evidence_coverage",
        "book_provenance_completeness",
        "book_colophon_completeness",
    ):
        assert metrics.observe(name, book_good, book_gold) == 1.0
        assert metrics.observe(name, book_broken, book_gold) == 0.0

    epub_root = tmp_path / "epub-library"
    book_path = artifact_path("fixture_ms", "book", None, epub_root)
    atomic_write_json(book_path, book_good)
    RenderEpub().run(Job("fixture_ms", (page,), None, epub_root, StationConfig()))
    epub_path = artifact_path("fixture_ms", "book_epub", None, epub_root)
    epub_good = {"epub_bytes": epub_path.read_bytes()}
    tracked_epub = EVALUATION_ROOT / "gold/render_epub/expected-book-v2.epub"
    assert hashlib.sha256(tracked_epub.read_bytes()).hexdigest() == (
        suites["render_epub"].cases[0].references["expected_epub"].sha256
    )
    epub_gold = {
        "navigation_labels": ["A Note to the Reader", "First Leaf", "Colophon"],
        "content_strings": [
            "Archive Scribe",
            "A one-leaf conformance manuscript.",
            "Alpha beta. Gamma.",
        ],
    }
    epub_broken = {"epub_bytes": b"not an EPUB container"}
    for name in (
        "epub_container_conformance",
        "epub_navigation_correctness",
        "epub_content_equivalence",
    ):
        assert metrics.observe(name, epub_good, epub_gold) == 1.0
        assert metrics.observe(name, epub_broken, epub_gold) == 0.0

    site_record = _read_json("gold/site/library-level-conformance-v1.json")
    assert site_record["record_kind"] == "library-level-site-conformance"
    assert "rights" in site_record and "adjudication" in site_record
    assert not (EVALUATION_ROOT / "suites/site").exists()
    assert not (CANDIDATE_ROOT / "site").exists()

    site_library = tmp_path / "site-library"
    source_book = artifact_path("fixture_ms", "book", None, site_library)
    atomic_write_json(source_book, book_good)
    for kind in ("page_image", "page_image_clean"):
        source_image = artifact_path("fixture_ms", kind, "p001", site_library)
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(
            (EVALUATION_ROOT / "gold/publish/page-image-clean.jpg").read_bytes()
        )
    site_root = tmp_path / "built-site"
    assert site.build(site_library, site_root) == ["fixture_ms"]
    assert not (site_root / "fixture_ms/fixture_ms.epub").exists()
    site_good = collect_site_conformance(site_root)
    site_gold = {
        "source_image_sha256": dict(site_good["source_image_sha256"]),
        "book_json_sha256": {
            "fixture_ms": hashlib.sha256(
                (site_root / "fixture_ms/book.json").read_bytes()
            ).hexdigest()
        },
        "minimum_keyboard_controls": site_record["requirements"][
            "minimum_keyboard_controls"
        ],
    }
    for name in (
        "site_link_integrity",
        "site_source_image_conformance",
        "site_keyboard_conformance",
        "site_responsive_conformance",
        "site_book_equality",
    ):
        assert metrics.observe(name, site_good, site_gold) == 1.0

    (site_root / "fixture_ms/evidence/p001.jpg").unlink()
    (site_root / "style.css").unlink()
    (site_root / "fixture_ms/book.json").write_text("{}", encoding="utf-8")
    reader = site_root / "fixture_ms/index.html"
    reader.write_text(
        reader.read_text(encoding="utf-8").replace(
            "<button class='toggle' onclick='tgl()'>Show editorial layers</button>",
            "<a href='missing.html'>broken reader target</a>",
        ),
        encoding="utf-8",
    )
    site_broken = collect_site_conformance(site_root)
    for name in (
        "site_link_integrity",
        "site_source_image_conformance",
        "site_keyboard_conformance",
        "site_responsive_conformance",
        "site_book_equality",
    ):
        assert metrics.observe(name, site_broken, site_gold) == 0.0

    assert calls == {"network": 0, "model": 0}
