"""assemble_page: join transcription + translation into the small loop's
finished part. Pure assembly — no model call.

The translation artifact records any overlap removed before translation.
Assembly applies that same seam report to the diplomatic transcription, so
the original and translation cover identical columns without independently
recomputing the boundary.
"""

from __future__ import annotations

import hashlib
import json

from palimpsest.factory.core.contracts import transcription_audit
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult


def _apply_seam(text: str, seam: dict | None) -> str:
    if seam is None:
        return text
    dropped = seam.get("dropped_text")
    if not isinstance(dropped, str) or not dropped or not text.startswith(dropped):
        raise ValueError("Translation seam does not match the page transcription")
    return text[len(dropped) :].strip("\n")


class AssemblePage(Station):
    name = "assemble_page"

    grain = "page"
    consumes = ("page_transcription", "page_translation")
    produces = "page_assembled"

    def run(self, job: Job) -> StationResult:
        transcription_path = job.path_of("page_transcription")
        translation_path = job.path_of("page_translation")
        transcription_bytes = transcription_path.read_bytes()
        translation_bytes = translation_path.read_bytes()
        transcription = json.loads(transcription_bytes)
        translation = json.loads(translation_bytes)

        if transcription.get("adjudication_status") == "failed":
            error = transcription.get("adjudication_error")
            detail = f": {error}" if error else ""
            raise ValueError(
                f"Cannot assemble page {job.page_id}: "
                f"transcription adjudication failed{detail}"
            )

        seam = translation.get("seam")
        text = _apply_seam(transcription["text"], seam)

        return StationResult(
            payload={
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
                "transcription_audit": transcription_audit(transcription),
                "inputs": {
                    "page_transcription": hashlib.sha256(
                        transcription_bytes
                    ).hexdigest()[:16],
                    "page_translation": hashlib.sha256(translation_bytes).hexdigest()[
                        :16
                    ],
                },
            }
        )


register(AssemblePage())
