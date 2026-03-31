"""Schema for transcription and enriched JSONL records."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TranscriptionRecord(BaseModel):
    """One line from transcriptions.jsonl."""

    source: str = ""
    book_title: str = ""
    page_id: str
    text: str
    model: str = ""
    timestamp: str = ""


class EnrichedRecord(TranscriptionRecord):
    """One line from enriched.jsonl — transcription plus translation."""

    translation: str = ""
    translation_model: str = ""
    translation_timestamp: str = ""
    notes: str = ""
    enrichment_version: int = 1
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
