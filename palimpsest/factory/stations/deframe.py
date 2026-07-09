"""deframe: crop the delivered image to the detected parchment frame.

First station of the preprocessing line. Kills the black backdrop, binding
edge, gutter bleed, and footer banners — the distractors that poison both
the background statistics of every later CV step and the model's attention.
"""

from __future__ import annotations

import cv2

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import encode_jpeg, parchment_frame, to_gray
from palimpsest.factory.workspace.io import atomic_write_bytes


class Deframe(Station):
    name = "deframe"
    version = "deframe/v1"
    grain = "page"
    consumes = ("page_image",)
    produces = "page_image_framed"

    def run(self, job: Job) -> StationResult:
        image = cv2.imread(str(job.path_of("page_image")))
        if image is None:
            raise ValueError(f"Unreadable image: {job.path_of('page_image')}")
        x0, y0, x1, y1 = parchment_frame(
            to_gray(image),
            margin_fraction=float(job.config.options.get("frame_margin", 0.02)),
        )
        atomic_write_bytes(self.output_path(job), encode_jpeg(image[y0:y1, x0:x1]))
        return StationResult()


register(Deframe())
