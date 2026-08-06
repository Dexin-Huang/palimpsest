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
    parchment_spread_frame,
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
    production_dependencies = (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    )

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


class SpreadSafeDeframe(Deframe):
    """Remove the scanner backdrop without treating an interior crease as a crop."""

    variant = "spread-safe/v1"

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image")
        gray = to_gray(image)
        x0, y0, x1, y1 = parchment_spread_frame(
            gray,
            margin_fraction=float(job.config.options.get("frame_margin", 0.02)),
        )
        atomic_write_bytes(self.output_path(job), encode_jpeg(image[y0:y1, x0:x1]))
        return StationResult()


class PassthroughDeframe(Deframe):
    """Copy the input bytes untouched so recipes can hand the instrumented rig
    raw archive bytes (its certified input domain) with the contract chain intact."""

    variant = "passthrough/v1"
    option_keys = frozenset()
    production_dependencies = ()

    def run(self, job: Job) -> StationResult:
        atomic_write_bytes(
            self.output_path(job), job.path_of("page_image").read_bytes()
        )
        return StationResult()


register(Deframe())
register(SpreadSafeDeframe())
register(PassthroughDeframe())
