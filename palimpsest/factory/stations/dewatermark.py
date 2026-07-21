"""dewatermark: remove digital overlay marks (watermarks, stamps, banners).

Second station of the preprocessing line. Discriminates overlays from real
faint annotations by letterform height AND intensity uniformity — a rendered
overlay has near-constant gray; pencil pressure varies. Both thresholds are
recipe options, tunable per digitization campaign.
"""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import encode_jpeg, remove_overlay_marks
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import atomic_write_bytes


class Dewatermark(Station):
    name = "dewatermark"

    grain = "page"
    consumes = ("page_image_framed",)
    produces = "page_image_unmarked"
    option_keys = frozenset({"height_fraction", "max_std"})
    production_dependencies = (
        "factory/imaging.py",
        "factory/stations/image_input.py",
    )

    def run(self, job: Job) -> StationResult:
        image = load_image(job, "page_image_framed")
        cleaned = remove_overlay_marks(
            image,
            height_fraction=float(job.config.options.get("height_fraction", 0.01)),
            max_std=float(job.config.options.get("max_std", 12.0)),
        )
        atomic_write_bytes(self.output_path(job), encode_jpeg(cleaned))
        return StationResult()


register(Dewatermark())
