"""prepare: image cleaning by named profile.

v1 ships the ``passthrough`` profile (byte copy) — which matches the current
golden path, where transcription reads raw downloads. Real cleaning profiles
(deskew, contrast) slot in as new entries in ``PROFILES`` without touching
the station contract.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult


def _passthrough(source: Path, dest: Path) -> None:
    shutil.copyfile(source, dest)


PROFILES = {"passthrough": _passthrough}


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
        profile(job.path_of("page_image"), tmp_path)
        os.replace(tmp_path, out_path)
        return StationResult()


register(Prepare())
