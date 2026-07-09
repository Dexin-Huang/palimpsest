"""prepare: image cleaning by named profile.

``passthrough`` copies bytes (the pre-segmentation golden path).
``vatican_scan`` targets what actually poisons Vatican digitizations:
the black page-edge frame and gutter bleed (fractional crop) and the giant
light-gray "ALL RIGHTS RESERVED" watermark ring (large-light-component
removal, which preserves faint pencil annotations). New profiles are new
entries in ``PROFILES``; the recipe picks by name.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import cv2

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.imaging import parchment_frame, remove_large_light_marks, to_gray


def _passthrough(source: Path, dest: Path, options: dict) -> None:
    shutil.copyfile(source, dest)


def _vatican_scan(source: Path, dest: Path, options: dict) -> None:
    image = cv2.imread(str(source))
    if image is None:
        raise ValueError(f"Unreadable image: {source}")
    x0, y0, x1, y1 = parchment_frame(
        to_gray(image), margin_fraction=float(options.get("frame_margin", 0.02)))
    cleaned = remove_large_light_marks(image[y0:y1, x0:x1])
    ok, buffer = cv2.imencode(".jpg", cleaned, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError(f"JPEG encoding failed for {source}")
    dest.write_bytes(buffer.tobytes())


PROFILES = {"passthrough": _passthrough, "vatican_scan": _vatican_scan}


class Prepare(Station):
    name = "prepare"
    version = "prepare/v1"
    grain = "page"
    consumes = ("page_image",)
    produces = "page_image_clean"

    def run(self, job: Job) -> StationResult:
        profile_name = job.config.options.get("profile", "passthrough")
        try:
            profile = PROFILES[profile_name]
        except KeyError:
            raise ValueError(
                f"Unknown prepare profile {profile_name!r}; have {sorted(PROFILES)}"
            ) from None

        out_path = self.output_path(job)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        profile(job.path_of("page_image"), tmp_path, dict(job.config.options))
        os.replace(tmp_path, out_path)
        return StationResult()


register(Prepare())
