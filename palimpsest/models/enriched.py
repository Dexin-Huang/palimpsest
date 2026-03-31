"""Data models for the enrichment pipeline (transcription -> translation)."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class TranscriptionRecord(BaseModel):
    """A single page from transcriptions.jsonl."""

    source: str = ""
    book_title: str = ""
    page_id: str
    text: str
    model: str = ""
    timestamp: str = ""


class EnrichedRecord(BaseModel):
    """A single enriched page written to enriched.jsonl.

    Carries the original transcription fields plus translation output.
    """

    source: str = ""
    book_title: str = ""
    page_id: str
    text: str = Field(description="Original transcribed text")
    translation: str = Field(description="English translation of the text")
    translation_model: str = Field(description="Model used for translation")
    translation_timestamp: str = ""
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
