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
from palimpsest.factory.gateway import ModelRequest, generate
from palimpsest.factory.seams import prev_page_id, trim_overlap
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

    grain = "page"
    consumes = ("page_transcription", "translation_brief")
    produces = "page_translation"
    uses_model = True
    param_keys = frozenset({"temperature", "max_output_tokens"})
    option_keys = frozenset({"overlap", "trim_seam_overlap"})
    production_dependencies = (
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/gemini.py",
        "factory/gateway/omp.py",
        "factory/gateway/pricing.py",
        "factory/gateway/protocol.py",
        "factory/seams.py",
        "factory/usage.py",
    )

    def input_paths(self, job: Job) -> list[Path]:
        window = self._context_window(job)
        paths = [job.path_of("page_transcription", page["page_id"]) for page in window]
        previous = self._seam_neighbor(job)
        if previous and all(page["page_id"] != previous for page in window):
            paths.append(job.path_of("page_transcription", previous))
        paths.append(job.path_of("translation_brief"))
        return paths

    def run(self, job: Job) -> StationResult:
        window = self._context_window(job)
        texts = {
            page["page_id"]: read_json(
                job.path_of("page_transcription", page["page_id"])
            )
            for page in window
        }
        own_index = next(
            i for i, page in enumerate(window) if page["page_id"] == job.page_id
        )
        left = self._format_context(window[:own_index], texts)
        right = self._format_context(window[own_index + 1 :], texts)

        page_text = texts[job.page_id]["text"]
        seam = None
        previous = self._seam_neighbor(job)
        if previous:
            prev_text = (
                texts[previous]
                if previous in texts
                else read_json(job.path_of("page_transcription", previous))
            )["text"]
            page_text, seam = trim_overlap(prev_text, page_text)

        brief = read_json(job.path_of("translation_brief"))
        brief.pop("provenance", None)  # guidance for the model, not bookkeeping

        prompt = (
            job.config.prompt.text.replace(
                "{BRIEF}", json.dumps(brief, ensure_ascii=False, indent=1)
            )
            .replace("{PAGE_TEXT}", page_text)
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

    def _seam_neighbor(self, job: Job) -> str | None:
        """Previous page id when this recipe trims re-photographed seam
        columns (overlapping scroll segments) before translating."""
        if not job.config.options.get("trim_seam_overlap"):
            return None
        return prev_page_id(job.pages, job.page_id)

    def _context_window(self, job: Job) -> tuple[dict, ...]:
        overlap = int(job.config.options.get("overlap", DEFAULT_OVERLAP))
        index = next(
            i for i, page in enumerate(job.pages) if page["page_id"] == job.page_id
        )
        return job.pages[max(0, index - overlap) : index + 1 + overlap]

    @staticmethod
    def _format_context(pages: tuple[dict, ...], texts: dict[str, dict]) -> str:
        return (
            "\n\n".join(
                f"[{page['page_id']}]\n{texts[page['page_id']]['text']}"
                for page in pages
            )
            or "(none)"
        )


register(Translate())
