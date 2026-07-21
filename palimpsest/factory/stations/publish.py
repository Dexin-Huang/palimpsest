"""publish: assemble the book model — pure content, zero presentation.

Merges the reconstruction with catalog metadata and builds the colophon by
reading provenance stamps off the artifacts themselves (disk is the archive;
no ledger dependency), so every book states what transcribed it, what
translated it, from which shelfmark, at what cost.
"""

from __future__ import annotations

from itertools import chain

from palimpsest.factory.core.artifact import (
    content_fingerprint,
    provenance_fingerprint,
    provenance_matches,
)
from palimpsest.factory.core.ledger import fingerprint
from palimpsest.factory.core.registry import get, register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.usage import combine_count
from palimpsest.factory.workspace.io import read_json

_COLOPHON_PROVENANCE_FIELDS = (
    "station",
    "station_fingerprint",
    "config_fingerprint",
    "model",
    "prompt_name",
    "prompt_sha256",
    "params",
    "tokens_in",
    "tokens_out",
    "cost_usd",
)


class Publish(Station):
    name = "publish"

    grain = "manuscript"
    consumes = (
        "metadata",
        "manuscript",
        "translation_brief",
        "page_transcription",
        "page_image_clean",
        "page_translation",
        "reference",
        "emendations",
    )
    optional_consumes = ("page_alignment",)
    produces = "book"
    option_keys = frozenset({"original_language"})

    def signature_extras(self, job: Job) -> tuple[str, ...]:
        page_paths = (
            job.path_of(kind, page["page_id"])
            for kind in (
                "page_transcription",
                "page_alignment",
                "page_translation",
            )
            for page in job.pages
            if job.path_of(kind, page["page_id"]).is_file()
        )
        manuscript_paths = (
            job.path_of(kind)
            for kind in (
                "translation_brief",
                "manuscript",
                "reference",
                "emendations",
            )
        )
        return tuple(
            provenance_fingerprint(path, _COLOPHON_PROVENANCE_FIELDS) or ""
            for path in chain(page_paths, manuscript_paths)
        )

    def run(self, job: Job) -> StationResult:
        manuscript = read_json(job.path_of("manuscript"))
        metadata = read_json(job.path_of("metadata"))
        catalog = metadata.get("source_catalog") or metadata
        emendations = read_json(job.path_of("emendations"))

        evidence, aligned_pages = self._evidence(job)
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "title": catalog.get("title") or catalog.get("label") or job.doc_id,
                "author": catalog.get("author"),
                "source": {
                    "archive": job.doc_id.split("_", 1)[0],
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
                        # the emended READING; the verbatim original stays beside it
                        "reading": emendation["reading"],
                        "pages": section["pages"],
                        "source_pages": _section_pages(job.pages, section["pages"]),
                    }
                    for index, (section, emendation) in enumerate(
                        zip(manuscript["sections"], emendations["sections"]), start=1
                    )
                ],
                "evidence": {"pages": evidence},
                "apparatus": emendations["apparatus"],
                "colophon": self._colophon(job, aligned_pages),
            }
        )

    def _evidence(self, job: Job) -> tuple[list[dict], set[str]]:
        pages = []
        aligned_pages = set()
        for source_page in job.pages:
            page_id = source_page["page_id"]
            transcription_path = job.path_of("page_transcription", page_id)
            transcription = read_json(transcription_path)
            evidence = {
                "page_id": page_id,
                "order": source_page["order"],
                "source_image_url": source_page["url"],
                "aligned_image_kind": "page_image_clean",
                "diplomatic": transcription["text"],
            }
            alignment_path = job.path_of("page_alignment", page_id)
            if self._alignment_is_current(
                job, alignment_path, transcription_path, page_id
            ):
                alignment = read_json(alignment_path)
                evidence["alignment"] = {
                    "columns": alignment["columns"],
                    "stats": alignment["stats"],
                    "provenance": alignment.get("provenance"),
                }
                aligned_pages.add(page_id)
            pages.append(evidence)
        return pages, aligned_pages

    @staticmethod
    def _alignment_is_current(
        job: Job, alignment_path, transcription_path, page_id: str
    ) -> bool:
        if not alignment_path.is_file():
            return False
        output_fingerprint = content_fingerprint(alignment_path)
        input_fingerprint = fingerprint(
            content_fingerprint(job.path_of("page_image_clean", page_id)),
            content_fingerprint(transcription_path),
        )
        return provenance_matches(
            alignment_path,
            {
                "station": "align",
                "station_fingerprint": get("align").implementation_fingerprint,
                "input_fingerprint": input_fingerprint,
                "output_fingerprint": output_fingerprint,
            },
        )

    def _colophon(self, job: Job, aligned_pages: set[str]) -> dict:
        stations: dict[str, dict] = {}
        known_cost = 0.0
        cost_complete = True
        stamps = chain(
            (
                read_json(job.path_of(kind, page["page_id"])).get("provenance", {})
                for kind in (
                    "page_transcription",
                    "page_alignment",
                    "page_translation",
                )
                for page in job.pages
                if job.path_of(kind, page["page_id"]).is_file()
                and (kind != "page_alignment" or page["page_id"] in aligned_pages)
            ),
            (
                read_json(job.path_of(kind)).get("provenance", {})
                for kind in (
                    "translation_brief",
                    "manuscript",
                    "reference",
                    "emendations",
                )
            ),
        )
        for stamp in stamps:
            name = stamp.get("station")
            if not name:
                continue
            station = stations.setdefault(
                name,
                {
                    "runs": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                    "cost_complete": True,
                    "cost_usd_known": 0.0,
                    "configurations": [],
                },
            )
            station["runs"] += 1
            station["tokens_in"] = combine_count(
                station["tokens_in"], stamp.get("tokens_in")
            )
            station["tokens_out"] = combine_count(
                station["tokens_out"], stamp.get("tokens_out")
            )
            configuration = {
                key: stamp.get(key)
                for key in (
                    "station_fingerprint",
                    "config_fingerprint",
                    "model",
                    "prompt_name",
                    "prompt_sha256",
                    "params",
                )
                if stamp.get(key) is not None
            }
            if configuration not in station["configurations"]:
                station["configurations"].append(configuration)
            if stamp.get("model"):
                billed = stamp.get("cost_usd")
                if billed is None:
                    cost_complete = False
                    station["cost_complete"] = False
                    station["cost_usd"] = None
                else:
                    known_cost += billed
                    station["cost_usd_known"] += billed
                    if station["cost_complete"]:
                        station["cost_usd"] += billed

        return {
            "transcribed_by": _station_model(stations, "read"),
            "translated_by": _station_model(stations, "translate"),
            "referenced_by": _station_model(stations, "reference"),
            "emended_by": _station_model(stations, "emend"),
            "pipeline": [
                {"station": name, **details} for name, details in stations.items()
            ],
            "cost_usd_total": round(known_cost, 4) if cost_complete else None,
            "cost_usd_known": round(known_cost or 0.0, 4),
            "cost_complete": cost_complete,
            "pages": len(job.pages),
        }


def _section_pages(pages: tuple[dict, ...], span: dict) -> list[str]:
    order = [page["page_id"] for page in pages]
    start = order.index(span["from"])
    end = order.index(span["to"])
    return order[start : end + 1]


def _station_model(stations: dict[str, dict], name: str):
    configurations = stations.get(name, {}).get("configurations", [])
    models = [
        configuration["model"]
        for configuration in configurations
        if configuration.get("model")
    ]
    return models[0] if len(models) == 1 else models or None


register(Publish())
