"""assemble_page: join transcription + translation into the small loop's
finished part. Pure assembly — no model call."""

from __future__ import annotations

import hashlib

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json


def _sha16(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class AssemblePage(Station):
    name = "assemble_page"
    version = "assemble_page/v1"
    grain = "page"
    consumes = ("page_transcription", "page_translation")
    produces = "page_assembled"

    def run(self, job: Job) -> StationResult:
        transcription_path = job.path_of("page_transcription")
        translation_path = job.path_of("page_translation")
        transcription = read_json(transcription_path)
        translation = read_json(translation_path)
        return StationResult(payload={
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": transcription.get("page_seq", 0),
            "canvas_id": transcription.get("canvas_id", ""),
            "original": {
                "text": transcription["text"],
                "regions": transcription.get("regions", []),
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


register(AssemblePage())
