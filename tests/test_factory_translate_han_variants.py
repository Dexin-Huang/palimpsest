from __future__ import annotations

import hashlib

from palimpsest.factory.core.station import Job, StationConfig
from palimpsest.factory.gateway import ModelResponse
from palimpsest.factory.prompt_store import Prompt
from palimpsest.factory.stations.translate import Translate
from palimpsest.factory.stations.translate_han_variants import (
    HanVariantAuxiliaryTranslate,
    _source_views,
)
from palimpsest.factory.workspace.io import atomic_write_json
from palimpsest.factory.workspace.layout import artifact_path


def test_source_views_preserve_diplomatic_text_and_add_only_safe_variants() -> None:
    diplomatic = "佛陁眞丗衆經目録 日曰已巳"
    views = _source_views(diplomatic)

    assert f"[DIPLOMATIC SOURCE — AUTHORITATIVE]\n{diplomatic}" in views
    assert "佛陀真世眾經目錄 日曰已巳" in views
    assert "SEMANTIC VIEW — AUXILIARY" in views
    assert "Do not treat it as new source evidence" in views


def test_han_variant_translation_preserves_socket() -> None:
    baseline = Translate()
    challenger = HanVariantAuxiliaryTranslate()

    assert (
        challenger.grain,
        challenger.consumes,
        challenger.optional_consumes,
        challenger.produces,
    ) == (
        baseline.grain,
        baseline.consumes,
        baseline.optional_consumes,
        baseline.produces,
    )
    assert "factory/han_variants.py" in challenger.production_dependencies
    assert "factory/stations/translate.py" in challenger.production_dependencies


def test_han_variant_translation_prompt_contains_both_views(
    tmp_path, monkeypatch
) -> None:
    doc_id = "han_translation_test"
    page = {"page_id": "p1", "order": 1, "url": "https://archive.test/p1"}
    transcription_path = artifact_path(doc_id, "page_transcription", "p1", tmp_path)
    transcription_path.parent.mkdir(parents=True)
    atomic_write_json(
        transcription_path,
        {"doc_id": doc_id, "page_id": "p1", "text": "佛陁眞丗衆經目録"},
    )
    brief_path = artifact_path(doc_id, "translation_brief", None, tmp_path)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(brief_path, {"document": {"doc_id": doc_id}})
    prompt_text = "{BRIEF}\n{PAGE_TEXT}\n{LEFT_CONTEXT}\n{RIGHT_CONTEXT}"
    prompt = Prompt(
        name="translate/zh/test",
        text=prompt_text,
        sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
    )
    job = Job(
        doc_id=doc_id,
        pages=(page,),
        page=page,
        library_root=tmp_path,
        config=StationConfig(
            model="test-model",
            prompt=prompt,
            params={"temperature": 0.1, "max_output_tokens": 256},
            options={"overlap": 1, "trim_seam_overlap": False},
        ),
    )
    captured: list[str] = []

    def fake_generate(request):
        captured.append(request.prompt)
        return ModelResponse(
            text="The Buddha taught.\n---FLAGS---\n{}\n---END FLAGS---",
            model="test-model",
        )

    monkeypatch.setattr(
        "palimpsest.factory.stations.translate_han_variants.generate", fake_generate
    )
    result = HanVariantAuxiliaryTranslate().run(job)

    assert result.payload["translation"] == "The Buddha taught."
    assert len(captured) == 1
    assert "[DIPLOMATIC SOURCE — AUTHORITATIVE]\n佛陁眞丗衆經目録" in captured[0]
    assert (
        "[HAN VARIANT TABLE V1 SEMANTIC VIEW — AUXILIARY]\n佛陀真世眾經目錄"
        in captured[0]
    )
