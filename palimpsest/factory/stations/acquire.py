"""acquire: download one page image from its IIIF url (lifted from
library/download.py)."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult

REQUEST_HEADERS = {"User-Agent": "palimpsest manuscript recovery factory"}
TIMEOUT_SECONDS = 60.0


class Acquire(Station):
    name = "acquire"

    grain = "page"
    consumes = ("page_list",)
    produces = "page_image"

    def input_paths(self, job: Job) -> list[Path]:
        # The page's URL is the true input; hashing page_list.json wholesale
        # would invalidate every image whenever unrelated metadata changes.
        return []

    def signature_extras(self, job: Job) -> tuple[str, ...]:
        return (job.page["url"],)

    def run(self, job: Job) -> StationResult:
        out_path = self.output_path(job)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        with requests.get(
            job.page["url"],
            stream=True,
            timeout=TIMEOUT_SECONDS,
            headers=REQUEST_HEADERS,
        ) as response:
            response.raise_for_status()
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
        return StationResult()


register(Acquire())
