"""deframe: crop the delivered image to the detected parchment frame.

First station of the preprocessing line. Kills the black backdrop, binding
edge, gutter bleed, and footer banners — the distractors that poison both
the background statistics of every later CV step and the model's attention.
"""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import (
    encode_jpeg,
    parchment_frame,
    to_gray,
    trim_gutter,
)
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import atomic_write_bytes


class Deframe(Station):
    name = "deframe"

    grain = "page"
    consumes = ("page_image",)
    produces = "page_image_framed"
    option_keys = frozenset({"frame_margin"})

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image")
        gray = to_gray(image)
        x0, y0, x1, y1 = parchment_frame(
            gray,
            margin_fraction=float(job.config.options.get("frame_margin", 0.02)),
        )
        framed = image[y0:y1, x0:x1]
        gx0, gx1 = trim_gutter(gray[y0:y1, x0:x1])
        atomic_write_bytes(self.output_path(job), encode_jpeg(framed[:, gx0:gx1]))
        return StationResult()


register(Deframe())
