"""Translation variant with a derived Han-form semantic view.

The diplomatic transcription remains authoritative and is always supplied
verbatim.  A conservative, position-preserving normalized view is appended for
semantic reasoning so historic glyph alternatives do not create false lexical
distinctions during translation.
"""

from __future__ import annotations

import json

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.gateway import ModelRequest, generate
from palimpsest.factory.han_variants import (
    HAN_VARIANT_TABLE_SHA256,
    HAN_VARIANT_TABLE_VERSION,
    normalize_han_variants_v1,
)
from palimpsest.factory.seams import trim_overlap
from palimpsest.factory.stations.translate import Translate, _parse_flags
from palimpsest.factory.workspace.io import read_json


def _source_views(diplomatic: str) -> str:
    normalized = normalize_han_variants_v1(diplomatic)
    return (
        "[DIPLOMATIC SOURCE — AUTHORITATIVE]\n"
        f"{diplomatic}\n\n"
        f"[HAN VARIANT TABLE V{HAN_VARIANT_TABLE_VERSION} SEMANTIC VIEW — AUXILIARY]\n"
        f"{normalized}\n"
        f"[TABLE SHA-256: {HAN_VARIANT_TABLE_SHA256}]\n"
        "Use the semantic view only to recognize equivalent written forms. "
        "Do not treat it as new source evidence, erase uncertainty, or silently "
        "repair names, titles, numbers, or damaged text."
    )


class HanVariantAuxiliaryTranslate(Translate):
    """Translate from diplomatic text plus a conservative normalized view."""

    variant = "han_variant_v1_auxiliary"
    production_dependencies = (
        *Translate.production_dependencies,
        "factory/han_variants.py",
        "factory/stations/translate.py",
    )

    def run(self, job: Job) -> StationResult:
        window = self._context_window(job)
        texts = {
            page["page_id"]: read_json(
                job.path_of("page_transcription", page["page_id"])
            )
            for page in window
        }
        own_index = next(
            index for index, page in enumerate(window) if page["page_id"] == job.page_id
        )
        left = self._format_context(window[:own_index], texts)
        right = self._format_context(window[own_index + 1 :], texts)

        page_text = texts[job.page_id]["text"]
        seam = None
        previous = self._seam_neighbor(job)
        if previous:
            previous_text = (
                texts[previous]
                if previous in texts
                else read_json(job.path_of("page_transcription", previous))
            )["text"]
            page_text, seam = trim_overlap(previous_text, page_text)

        brief = read_json(job.path_of("translation_brief"))
        brief.pop("provenance", None)
        prompt = (
            job.config.prompt.text.replace(
                "{BRIEF}", json.dumps(brief, ensure_ascii=False, indent=1)
            )
            .replace("{PAGE_TEXT}", _source_views(page_text))
            .replace("{LEFT_CONTEXT}", left)
            .replace("{RIGHT_CONTEXT}", right)
        )
        response = generate(
            ModelRequest(
                model=job.config.model,
                prompt=prompt,
                temperature=job.config.params.get("temperature", 0.1),
                max_output_tokens=job.config.params.get("max_output_tokens", 32768),
            )
        )
        translation, flags = _parse_flags(response.text)
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "translation": translation,
                "notes": "",
                "flags": flags,
                "seam": seam,
            },
            tokens_in=response.prompt_tokens,
            tokens_out=response.billable_output_tokens,
            cost_usd=response.cost_usd,
        )

    @staticmethod
    def _format_context(pages: tuple[dict, ...], texts: dict[str, dict]) -> str:
        return (
            "\n\n".join(
                f"[{page['page_id']}]\n{_source_views(texts[page['page_id']]['text'])}"
                for page in pages
            )
            or "(none)"
        )


register(HanVariantAuxiliaryTranslate())
