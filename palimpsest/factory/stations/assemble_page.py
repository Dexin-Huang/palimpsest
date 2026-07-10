"""assemble_page: join transcription + translation into the small loop's
finished part. Pure assembly — no model call.

Under ``trim_seam_overlap`` (overlapping scroll segments), the original is
trimmed against the previous page with the same pure function translate used,
so the assembled original and translation cover the same columns; the dropped
duplicate stays auditable in ``original.seam``."""

from __future__ import annotations

import hashlib
from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.seams import prev_page_id, trim_overlap
from palimpsest.factory.workspace.io import read_json


def _sha16(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class AssemblePage(Station):
    name = "assemble_page"
    version = "assemble_page/v1"
    grain = "page"
    consumes = ("page_transcription", "page_translation")
    produces = "page_assembled"

    def input_paths(self, job: Job) -> list[Path]:
        paths = [job.path_of(kind) for kind in self.consumes]
        previous = self._seam_neighbor(job)
        if previous:
            paths.append(job.path_of("page_transcription", previous))
        return paths

    def run(self, job: Job) -> StationResult:
        transcription_path = job.path_of("page_transcription")
        translation_path = job.path_of("page_translation")
        transcription = read_json(transcription_path)
        translation = read_json(translation_path)

        text, seam = transcription["text"], None
        previous = self._seam_neighbor(job)
        if previous:
            prev_text = read_json(
                job.path_of("page_transcription", previous))["text"]
            text, seam = trim_overlap(prev_text, text)

        return StationResult(payload={
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": transcription.get("page_seq", 0),
            "canvas_id": transcription.get("canvas_id", ""),
            "original": {
                "text": text,
                "regions": transcription.get("regions", []),
                "seam": seam,
            },
            "translation": {
                "text": translation["translation"],
                "notes": translation.get("notes", ""),
                "flags": translation.get("flags", {}),
            },
            "alignment": [],
            "inputs": {
                "page_transcription": _sha16(transcription_path),
                "page_translation": _sha16(translation_path),
            },
        })

    def _seam_neighbor(self, job: Job) -> str | None:
        if not job.config.options.get("trim_seam_overlap"):
            return None
        return prev_page_id(job.pages, job.page_id)


register(AssemblePage())
