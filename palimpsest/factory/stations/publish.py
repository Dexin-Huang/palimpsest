"""publish: assemble the book model — pure content, zero presentation.

Merges the immutable evidence layers with the final edition prose and catalog
metadata, then builds the colophon by reading provenance stamps off the
artifacts themselves (disk is the archive; no ledger dependency), so every
book states what transcribed it, what translated it, from which shelfmark, and
at what cost.
"""

from __future__ import annotations

from itertools import chain

from palimpsest.factory.core.artifact import (
    content_fingerprint,
    provenance_fingerprint,
    provenance_matches,
    read_provenance,
)
from palimpsest.factory.core.contracts import (
    BOOK_PROFILE,
    BOOK_SCHEMA_VERSION,
    transcription_audit,
)
from palimpsest.factory.core.artifact import fingerprint
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
        "page_image",
        "page_image_clean",
        "page_translation",
        "reference",
        "emendations",
        "edition",
    )
    optional_consumes = ("page_alignment",)
    produces = "book"
    option_keys = frozenset({"original_language"})
    production_dependencies = (
                "factory/usage.py",
    )

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
                "edition",
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
        edition = read_json(job.path_of("edition"))

        folios, aligned_pages = self._folios(job)
        sections, section_ids_by_heading = self._sections(
            job, manuscript, emendations, edition
        )
        apparatus, apparatus_ids_by_section = self._apparatus(
            emendations["apparatus"], section_ids_by_heading
        )
        for section in sections:
            section["apparatus_ids"] = apparatus_ids_by_section.get(section["id"], [])

        return StationResult(
            payload={
                "schema_version": BOOK_SCHEMA_VERSION,
                "profile": BOOK_PROFILE,
                "doc_id": job.doc_id,
                "catalog_record_id": metadata["catalog_record_id"],
                "identity": {
                    "title": catalog.get("title") or catalog.get("label") or job.doc_id,
                    "author": catalog.get("author"),
                    "archive": catalog.get("archive") or job.doc_id.split("_", 1)[0],
                    "shelfmark": catalog.get("shelfmark"),
                    "date": catalog.get("date"),
                },
                "languages": {
                    "original": job.config.options.get("original_language", "la"),
                    "translation": "en",
                },
                "readers_note": edition["readers_note"],
                "folios": folios,
                "sections": sections,
                "apparatus": apparatus,
                "colophon": self._colophon(job, aligned_pages),
            }
        )

    def _folios(self, job: Job) -> tuple[list[dict], set[str]]:
        folios = []
        aligned_pages = set()
        for source_page in job.pages:
            page_id = source_page["page_id"]
            transcription_path = job.path_of("page_transcription", page_id)
            transcription = read_json(transcription_path)
            if transcription.get("adjudication_status") == "failed":
                error = transcription.get("adjudication_error")
                detail = f": {error}" if error else ""
                raise ValueError(
                    f"Cannot publish page {page_id}: "
                    f"transcription adjudication failed{detail}"
                )
            translation = read_json(job.path_of("page_translation", page_id))
            evidence = {
                "diplomatic": {
                    "text": _published_text(
                        transcription["text"],
                        is_blank=transcription.get("route") == "blank",
                    ),
                    "audit": transcription_audit(transcription),
                    "source": _source_ref(job, "page_transcription", "/text", page_id),
                },
                "translation": {
                    "text": _published_text(
                        translation["translation"],
                        is_blank=transcription.get("route") == "blank",
                    ),
                    "notes": translation.get("notes", ""),
                    "flags": translation["flags"],
                    "seam": translation.get("seam"),
                    "source": _source_ref(
                        job, "page_translation", "/translation", page_id
                    ),
                },
            }
            alignment_path = job.path_of("page_alignment", page_id)
            if self._alignment_is_current(
                job, alignment_path, transcription_path, page_id
            ):
                alignment = read_json(alignment_path)
                evidence["alignment"] = {
                    "columns": alignment["columns"],
                    "stats": alignment["stats"],
                    "source": _source_ref(job, "page_alignment", "/columns", page_id),
                }
                aligned_pages.add(page_id)
            folios.append(
                {
                    "page_id": page_id,
                    "order": source_page["order"],
                    "images": {
                        "original": {
                            "kind": "page_image",
                            "page_id": page_id,
                            "fingerprint": content_fingerprint(
                                job.path_of("page_image", page_id)
                            ),
                            "source_url": source_page["url"],
                        },
                        "enhanced": {
                            "kind": "page_image_clean",
                            "page_id": page_id,
                            "fingerprint": content_fingerprint(
                                job.path_of("page_image_clean", page_id)
                            ),
                        },
                    },
                    "evidence": evidence,
                }
            )
        return folios, aligned_pages

    def _sections(
        self,
        job: Job,
        manuscript: dict,
        emendations: dict,
        edition: dict,
    ) -> tuple[list[dict], dict[str, str]]:
        manuscript_sections = manuscript.get("sections")
        emended_sections = emendations.get("sections")
        edition_sections = edition.get("sections")
        if not all(
            isinstance(sections, list)
            for sections in (
                manuscript_sections,
                emended_sections,
                edition_sections,
            )
        ):
            raise ValueError("Cannot publish: editorial sections must be JSON arrays")
        counts = {
            "manuscript": len(manuscript_sections),
            "emendations": len(emended_sections),
            "edition": len(edition_sections),
        }
        if len(set(counts.values())) != 1 or not manuscript_sections:
            raise ValueError(
                f"Cannot publish: editorial section counts do not match {counts}"
            )

        fingerprints = {
            kind: content_fingerprint(job.path_of(kind))
            for kind in ("manuscript", "emendations", "edition")
        }
        sections = []
        section_ids_by_heading: dict[str, str] = {}
        for index in range(len(manuscript_sections)):
            manuscript_section = manuscript_sections[index]
            emended_section = emended_sections[index]
            edition_section = edition_sections[index]
            if edition_section.get("section_index") != index:
                raise ValueError(
                    "Cannot publish: edition section_index values must match "
                    "manuscript section order"
                )
            section_id = f"section-{index + 1:04d}"
            folio_ids = _section_pages(job.pages, manuscript_section["pages"])
            section_is_blank = all(
                read_json(job.path_of("page_transcription", page_id)).get("route")
                == "blank"
                for page_id in folio_ids
            )
            for heading in (
                manuscript_section.get("heading"),
                edition_section.get("heading"),
            ):
                existing_section_id = section_ids_by_heading.get(heading)
                if (
                    existing_section_id is not None
                    and existing_section_id != section_id
                ):
                    raise ValueError(
                        f"Cannot publish: duplicate section heading {heading!r}"
                    )
                section_ids_by_heading[heading] = section_id
            sections.append(
                {
                    "id": section_id,
                    "order": index + 1,
                    "heading": edition_section["heading"],
                    "folio_ids": folio_ids,
                    "content": {
                        "translation": {
                            "text": _published_text(
                                edition_section["translation"],
                                is_blank=section_is_blank,
                            ),
                            "source": _source_descriptor(
                                "edition",
                                f"/sections/{index}/translation",
                                fingerprints["edition"],
                            ),
                        },
                        "emended_reading": {
                            "text": _published_text(
                                emended_section["reading"],
                                is_blank=section_is_blank,
                            ),
                            "source": _source_descriptor(
                                "emendations",
                                f"/sections/{index}/reading",
                                fingerprints["emendations"],
                            ),
                        },
                        "diplomatic_transcription": {
                            "text": _published_text(
                                manuscript_section["original"],
                                is_blank=section_is_blank,
                            ),
                            "source": _source_descriptor(
                                "manuscript",
                                f"/sections/{index}/original",
                                fingerprints["manuscript"],
                            ),
                        },
                    },
                    "apparatus_ids": [],
                }
            )
        return sections, section_ids_by_heading

    @staticmethod
    def _apparatus(
        source_entries: list[dict],
        section_ids_by_heading: dict[str, str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        entries = []
        ids_by_section: dict[str, list[str]] = {}
        for index, source in enumerate(source_entries, start=1):
            heading = source.get("section")
            try:
                section_id = section_ids_by_heading[heading]
            except KeyError:
                raise ValueError(
                    f"Cannot publish: apparatus cites unknown section {heading!r}"
                ) from None
            apparatus_id = f"apparatus-{index:04d}"
            entries.append(
                {
                    "id": apparatus_id,
                    "section_id": section_id,
                    "original": source["original"],
                    "emended": source["emended"],
                    "reason": source["reason"],
                    "evidence": source.get("evidence", ""),
                }
            )
            ids_by_section.setdefault(section_id, []).append(apparatus_id)
        return entries, ids_by_section

    @staticmethod
    def _alignment_is_current(
        job: Job, alignment_path, transcription_path, page_id: str
    ) -> bool:
        if not alignment_path.is_file():
            return False
        stamp = read_provenance(alignment_path)
        if stamp is None:
            return False
        station_name = stamp.get("station", "align")
        variant = stamp.get("station_variant")
        station = (
            get(station_name, variant) if variant else get(station_name)
        )
        output_fingerprint = content_fingerprint(alignment_path)
        input_fingerprint = fingerprint(
            content_fingerprint(job.path_of("page_image_clean", page_id)),
            content_fingerprint(transcription_path),
        )
        return provenance_matches(
            alignment_path,
            {
                "station": station_name,
                "station_fingerprint": station.implementation_fingerprint,
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
                    "edition",
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
            "finalized_by": _station_model(stations, "finalize_edition"),
            "pipeline": [
                {"station": name, **details} for name, details in stations.items()
            ],
            "cost_usd_total": round(known_cost, 4) if cost_complete else None,
            "cost_usd_known": round(known_cost or 0.0, 4),
            "cost_complete": cost_complete,
            "pages": len(job.pages),
        }


def _published_text(text: str, *, is_blank: bool) -> str:
    if text.strip():
        return text
    return "[Blank page]" if is_blank else "[No transcribed text]"


def _source_ref(
    job: Job,
    kind: str,
    pointer: str,
    page_id: str | None = None,
) -> dict[str, str]:
    return _source_descriptor(
        kind,
        pointer,
        content_fingerprint(job.path_of(kind, page_id)),
    )


def _source_descriptor(
    kind: str, pointer: str, fingerprint_value: str
) -> dict[str, str]:
    return {
        "kind": kind,
        "pointer": pointer,
        "fingerprint": fingerprint_value,
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
