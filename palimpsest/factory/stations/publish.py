"""publish: assemble the book model — pure content, zero presentation.

Merges the reconstruction with catalog metadata and builds the colophon by
reading provenance stamps off the artifacts themselves (disk is the archive;
no ledger dependency), so every book states what transcribed it, what
translated it, from which shelfmark, at what cost.
"""

from __future__ import annotations

from pathlib import Path

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import metadata_path


class Publish(Station):
    name = "publish"
    version = "publish/v1"
    grain = "manuscript"
    consumes = ("manuscript",)
    produces = "book"

    def input_paths(self, job: Job) -> list[Path]:
        # The colophon reads provenance stamps off the page artifacts and the
        # brief — hermetic rule: everything read is declared.
        return [
            job.path_of("manuscript"),
            job.path_of("translation_brief"),
            metadata_path(job.doc_id, job.library_root),
            *(job.path_of(kind, page["page_id"])
              for kind in ("page_transcription", "page_translation")
              for page in job.pages),
        ]

    def run(self, job: Job) -> StationResult:
        manuscript = read_json(job.path_of("manuscript"))
        catalog = read_json(
            metadata_path(job.doc_id, job.library_root)
        ).get("source_catalog", {})

        return StationResult(payload={
            "doc_id": job.doc_id,
            "title": catalog.get("title") or catalog.get("label") or job.doc_id,
            "author": catalog.get("author"),
            "source": {
                "archive": _archive_of(job.doc_id),
                "shelfmark": catalog.get("shelfmark"),
                "date": catalog.get("date"),
            },
            "language": {
                "original": job.config.options.get("original_language", "la"),
                "translation": "en",
            },
            "readers_note": manuscript.get("readers_note", ""),
            "chapters": [
                {
                    "id": f"ch{index:02d}",
                    "heading": section["heading"],
                    "translation": section["translation"],
                    "original": section["original"],
                    "pages": section["pages"],
                }
                for index, section in enumerate(manuscript["sections"], start=1)
            ],
            "colophon": self._colophon(job),
        })

    def _colophon(self, job: Job) -> dict:
        stations: dict[str, dict] = {}
        cost = 0.0
        stamps = [
            read_json(job.path_of(kind, page["page_id"])).get("provenance", {})
            for kind in ("page_transcription", "page_translation")
            for page in job.pages
        ] + [
            read_json(job.path_of(kind)).get("provenance", {})
            for kind in ("translation_brief", "manuscript")
        ]
        for stamp in stamps:
            cost += stamp.get("cost_usd") or 0.0
            key = stamp.get("station")
            if key and key not in stations:
                stations[key] = {
                    "station_version": stamp.get("station_version"),
                    "model": stamp.get("model"),
                    "prompt_sha256": stamp.get("prompt_sha256"),
                }
        return {
            "transcribed_by": stations.get("read", {}).get("model"),
            "translated_by": stations.get("translate", {}).get("model"),
            "pipeline": [
                {"station": name, **details} for name, details in stations.items()
            ],
            "cost_usd_total": round(cost, 4),
            "pages": len(job.pages),
        }


def _archive_of(doc_id: str) -> str:
    return doc_id.split("_", 1)[0]


register(Publish())
