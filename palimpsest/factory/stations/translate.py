"""translate: one page, guided by the survey jig and neighbor context
(lifted from enrich.py).

Neighbor transcriptions are declared inputs: refreshing a neighbor's read
correctly makes THIS page's translation stale, because its continuity
context changed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway.client import ModelRequest, generate
from palimpsest.factory.workspace.io import read_json

DEFAULT_OVERLAP = 1
_FLAGS_RE = re.compile(r"---FLAGS---\s*(\{.*?\})\s*---END FLAGS---", re.DOTALL)


def _parse_flags(text: str) -> tuple[str, dict]:
    match = _FLAGS_RE.search(text)
    if not match:
        return text.strip(), {}
    translation = text[: match.start()].strip()
    try:
        flags = json.loads(match.group(1))
    except json.JSONDecodeError:
        flags = {}
    return translation, flags


class Translate(Station):
    name = "translate"
    version = "translate/v1"
    grain = "page"
    consumes = ("page_transcription", "translation_brief")
    produces = "page_translation"
    uses_model = True

    def input_paths(self, job: Job) -> list[Path]:
        own_and_neighbors = [
            job.path_of("page_transcription", page["page_id"])
            for page in self._context_window(job)
        ]
        return own_and_neighbors + [job.path_of("translation_brief")]

    def run(self, job: Job) -> StationResult:
        window = self._context_window(job)
        texts = {
            page["page_id"]: read_json(job.path_of("page_transcription", page["page_id"]))
            for page in window
        }
        own_index = next(
            i for i, page in enumerate(window) if page["page_id"] == job.page_id
        )
        left = self._format_context(window[:own_index], texts)
        right = self._format_context(window[own_index + 1:], texts)

        brief = read_json(job.path_of("translation_brief"))
        brief.pop("provenance", None)  # guidance for the model, not bookkeeping

        prompt = (
            job.config.prompt.text
            .replace("{BRIEF}", json.dumps(brief, ensure_ascii=False, indent=1))
            .replace("{PAGE_TEXT}", texts[job.page_id]["text"])
            .replace("{LEFT_CONTEXT}", left)
            .replace("{RIGHT_CONTEXT}", right)
        )
        response = generate(ModelRequest(
            model=job.config.model,
            prompt=prompt,
            temperature=job.config.params.get("temperature", 0.1),
            max_output_tokens=job.config.params.get("max_output_tokens", 32768),
        ))
        translation, flags = _parse_flags(response.text)
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "translation": translation,
                "notes": "",
                "flags": flags,
            },
            tokens_in=response.prompt_tokens,
            tokens_out=response.output_tokens,
            cost_usd=response.cost_usd,
        )

    def _context_window(self, job: Job) -> list[dict]:
        overlap = int(job.config.options.get("overlap", DEFAULT_OVERLAP))
        index = next(
            i for i, page in enumerate(job.pages) if page["page_id"] == job.page_id
        )
        return list(job.pages[max(0, index - overlap): index + 1 + overlap])

    @staticmethod
    def _format_context(pages: list[dict], texts: dict[str, dict]) -> str:
        blocks = [
            f"[{page['page_id']}]\n{texts[page['page_id']]['text']}" for page in pages
        ]
        return "\n\n".join(blocks) or "(none)"


register(Translate())
