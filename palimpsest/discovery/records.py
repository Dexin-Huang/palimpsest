from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


def encode_json_field(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def decode_json_field(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def normalize_manuscript_updates(updates: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(updates)
    if "languages" in normalized and isinstance(normalized["languages"], list):
        normalized["languages"] = encode_json_field(normalized["languages"])
    if "subject_areas" in normalized and isinstance(normalized["subject_areas"], list):
        normalized["subject_areas"] = encode_json_field(normalized["subject_areas"])
    if "source_catalog" in normalized:
        normalized["source_catalog_json"] = encode_json_field(
            normalized.pop("source_catalog")
        )
    return normalized


@dataclass
class Manuscript:
    """Core discovery manuscript record."""

    id: str  # e.g., "vat_pal_lat_1267"
    shelfmark: str  # e.g., "Pal.lat.1267"
    repository: str  # e.g., "BAV"

    iiif_manifest_url: str | None = None
    canvas_count: int | None = None

    obscurity_score: int | None = None
    wtf_score: int | None = None
    interest_score: int | None = None

    status: str = "discovered"
    priority: int = 50

    collection: str | None = None
    title: str | None = None
    date_range: str | None = None
    languages: list[str] | None = None
    subject_areas: list[str] | None = None
    description: str | None = None
    source_catalog: dict[str, Any] | None = None

    discovered_at: str | None = None
    updated_at: str | None = None


@dataclass
class Opportunity:
    """Triage record for discovery opportunities."""

    manuscript_id: str
    initial_interest: bool | None = None
    initial_score: int | None = None
    interest_score: int | None = None
    interest_reason: str | None = None
    triage_method: str | None = None
    triage_model: str | None = None
    triage_at: str | None = None
    triage_json: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    status: str = "new"


@dataclass
class Enrichment:
    """Enrichment signals for manuscript discovery triage."""

    manuscript_id: str
    catalog_description_words: int | None = None
    manifest_metadata_fields: int | None = None
    has_bibliography: bool | None = None
    has_incipit: bool | None = None
    in_viaf: bool | None = None
    viaf_id: str | None = None
    in_europeana: bool | None = None
    in_mirabile: bool | None = None
    in_pinakes: bool | None = None
    google_scholar_hits: int | None = None
    google_search_hits: int | None = None
    transcription_exists: bool | None = None
    transcription_source: str | None = None
    common_text_detected: str | None = None
    original_content_signals: list[str] | None = None
    author_famous: bool | None = None
    studied_score: int | None = None
    enriched_at: str | None = None
    enrichment_notes: str | None = None


@dataclass
class AuditEntry:
    """Audit log entry."""

    id: int | None = None
    manuscript_id: str = ""
    timestamp: str = ""
    action: str = ""
    agent: str = ""
    details: str | None = None


def manuscript_from_row(row: sqlite3.Row) -> Manuscript:
    return Manuscript(
        id=row["id"],
        shelfmark=row["shelfmark"],
        repository=row["repository"],
        iiif_manifest_url=row["iiif_manifest_url"],
        canvas_count=row["canvas_count"],
        obscurity_score=row["obscurity_score"],
        wtf_score=row["wtf_score"],
        interest_score=row["interest_score"],
        status=row["status"],
        priority=row["priority"],
        collection=row["collection"],
        title=row["title"],
        date_range=row["date_range"],
        languages=decode_json_field(row["languages"]),
        subject_areas=decode_json_field(row["subject_areas"]),
        description=row["description"],
        source_catalog=decode_json_field(row["source_catalog_json"])
        if "source_catalog_json" in row.keys()
        else None,
        discovered_at=row["discovered_at"],
        updated_at=row["updated_at"],
    )


def opportunity_from_row(row: sqlite3.Row) -> Opportunity:
    return Opportunity(
        manuscript_id=row["manuscript_id"],
        initial_interest=bool(row["initial_interest"])
        if row["initial_interest"] is not None
        else None,
        initial_score=row["initial_score"],
        interest_score=row["interest_score"],
        interest_reason=row["interest_reason"],
        triage_method=row["triage_method"],
        triage_model=row["triage_model"],
        triage_at=row["triage_at"],
        triage_json=row["triage_json"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        status=row["status"] or "new",
    )


def enrichment_from_row(row: sqlite3.Row) -> Enrichment:
    return Enrichment(
        manuscript_id=row["manuscript_id"],
        catalog_description_words=row["catalog_description_words"],
        manifest_metadata_fields=row["manifest_metadata_fields"],
        has_bibliography=bool(row["has_bibliography"])
        if row["has_bibliography"] is not None
        else None,
        has_incipit=bool(row["has_incipit"])
        if row["has_incipit"] is not None
        else None,
        in_viaf=bool(row["in_viaf"]) if row["in_viaf"] is not None else None,
        viaf_id=row["viaf_id"],
        in_europeana=bool(row["in_europeana"])
        if row["in_europeana"] is not None
        else None,
        in_mirabile=bool(row["in_mirabile"])
        if row["in_mirabile"] is not None
        else None,
        in_pinakes=bool(row["in_pinakes"])
        if row["in_pinakes"] is not None
        else None,
        google_scholar_hits=row["google_scholar_hits"],
        google_search_hits=row["google_search_hits"],
        transcription_exists=bool(row["transcription_exists"])
        if row["transcription_exists"] is not None
        else None,
        transcription_source=row["transcription_source"],
        common_text_detected=row["common_text_detected"],
        original_content_signals=decode_json_field(row["original_content_signals"]),
        author_famous=bool(row["author_famous"])
        if row["author_famous"] is not None
        else None,
        studied_score=row["studied_score"],
        enriched_at=row["enriched_at"],
        enrichment_notes=row["enrichment_notes"],
    )


def audit_entry_from_row(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        manuscript_id=row["manuscript_id"],
        timestamp=row["timestamp"],
        action=row["action"],
        agent=row["agent"],
        details=row["details"],
    )


__all__ = [
    "AuditEntry",
    "Enrichment",
    "Manuscript",
    "Opportunity",
    "audit_entry_from_row",
    "decode_json_field",
    "encode_json_field",
    "enrichment_from_row",
    "manuscript_from_row",
    "normalize_manuscript_updates",
    "opportunity_from_row",
]
