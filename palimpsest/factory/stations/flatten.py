"""flatten: illumination flattening + optional soft attenuation — the last
preprocessing station, producing the image the readers consume.

Modes (recipe option ``mode``):
- ``none``       — byte-faithful copy (corpora that need no enhancement)
- ``flatten``    — divide out the low-frequency background field
- ``attenuate``  — flatten, then push light residue (show-through, stains)
                   toward white proportionally; ink untouched, nothing
                   thresholded away
"""

from __future__ import annotations

import cv2

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import (
    attenuate_light_marks,
    encode_jpeg,
    flatten_illumination,
)
from palimpsest.factory.workspace.io import atomic_write_bytes

MODES = ("none", "flatten", "attenuate")


class Flatten(Station):
    name = "flatten"

    grain = "page"
    consumes = ("page_image_unmarked",)
    produces = "page_image_clean"
    option_keys = frozenset({"mode", "factor"})

    def run(self, job: Job) -> StationResult:
        mode = job.config.options.get("mode", "flatten")
        if mode not in MODES:
            raise ValueError(f"Unknown flatten mode {mode!r}; have {MODES}")
        source = job.path_of("page_image_unmarked")

        if mode == "none":
            atomic_write_bytes(self.output_path(job), source.read_bytes())
            return StationResult()

        image = cv2.imread(str(source))
        if image is None:
            raise ValueError(f"Unreadable image: {source}")
        result = flatten_illumination(image)
        if mode == "attenuate":
            result = attenuate_light_marks(
                result, factor=float(job.config.options.get("factor", 0.45))
            )
        atomic_write_bytes(self.output_path(job), encode_jpeg(result))
        return StationResult()


register(Flatten())
