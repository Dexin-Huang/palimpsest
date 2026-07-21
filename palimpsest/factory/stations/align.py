"""align: forced alignment of the transcription to page ink.

Pure geometry, no model call. Binds each transcribed character to a blob
bounding box via column projection + DTW, yielding coordinates for the
reader, deterministic count-mismatch stats for evaluation, and the
substrate for the exemplar library. Characters it cannot bind are marked,
never forced — interlinear glosses and damaged spans stay auditable holes.
"""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.glyphs import align_page
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import read_json


class Align(Station):
    name = "align"

    grain = "page"
    consumes = ("page_image_clean", "page_transcription")
    produces = "page_alignment"

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image_clean")
        transcription = read_json(job.path_of("page_transcription"))
        aligned = align_page(image, transcription["text"].splitlines())
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                **aligned,
            }
        )


register(Align())
