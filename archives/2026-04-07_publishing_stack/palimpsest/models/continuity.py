"""Continuity artifacts for front-to-back manuscript reading."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ContinuityStatus = Literal["proposed", "confirmed", "revised", "rejected"]


class ContinuityItem(BaseModel):
    """One entity or term carried forward through the manuscript."""

    key: str
    label: str
    status: ContinuityStatus = Field(default="proposed")
    note: Optional[str] = Field(default=None)


class PageLink(BaseModel):
    """One continuity claim linking the page to another page or section."""

    link_type: str
    target_page_id: str
    status: ContinuityStatus = Field(default="proposed")
    note: Optional[str] = Field(default=None)


class PageHandoff(BaseModel):
    """Compact forward-facing state from one page packet to the next."""

    artifact_type: Literal["page.handoff"] = Field(default="page.handoff")
    created_at: str
    doc_id: str
    page_id: str
    next_page_id: Optional[str] = Field(default=None)
    source_packet_path: str
    continues_text: Optional[bool] = Field(default=None)
    summary: List[str] = Field(default_factory=list)
    active_entities: List[ContinuityItem] = Field(default_factory=list)
    active_terms: List[ContinuityItem] = Field(default_factory=list)
    verify_next: List[str] = Field(default_factory=list)
    local_questions: List[str] = Field(default_factory=list)
    proposed_links: List[PageLink] = Field(default_factory=list)


class WindowSynthesis(BaseModel):
    """Sliding-window state for a small run of adjacent pages."""

    artifact_type: Literal["window.synthesis"] = Field(default="window.synthesis")
    created_at: str
    doc_id: str
    page_ids: List[str] = Field(default_factory=list)
    center_page_id: str
    summary: List[str] = Field(default_factory=list)
    contiguous_threads: List[str] = Field(default_factory=list)
    stable_entities: List[ContinuityItem] = Field(default_factory=list)
    stable_terms: List[ContinuityItem] = Field(default_factory=list)
    revise_or_confirm: List[str] = Field(default_factory=list)
    open_loops: List[str] = Field(default_factory=list)
