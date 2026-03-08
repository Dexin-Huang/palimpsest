"""Restoration output models for Palimpsest."""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from .page import Span

BreakAfter = Literal["none", "line", "paragraph", "column", "page"]
SegmentRole = Literal[
    "main_text",
    "rubric",
    "initial",
    "marginalia",
    "interlinear",
    "header",
    "footer",
    "page_number",
    "catchword",
    "caption",
    "diagram_label",
    "table_cell",
    "other",
]
SegmentPlacement = Literal[
    "main_flow",
    "margin_outer",
    "margin_inner",
    "header",
    "footer",
    "interlinear",
    "floating",
]
SegmentCertainty = Literal["certain", "uncertain", "supplied", "illegible", "damaged"]
ReviewStatus = Literal[
    "unreviewed",
    "machine_only",
    "human_checked",
    "scholar_checked",
    "blocked",
]


class LayoutProjection(BaseModel):
    """Restoration-facing projection of page layout."""

    columns: Optional[int] = Field(default=None, ge=1, le=4)
    preserve_line_breaks: bool = Field(default=True)
    preserve_marginalia_positions: bool = Field(default=True)
    preserve_interlinear_insertions: bool = Field(default=True)
    preserve_rubrication: bool = Field(default=True)
    preserve_initials: bool = Field(default=True)


class SegmentAnchors(BaseModel):
    """Page anchors used by rendered views and review tooling."""

    bbox_norm: Optional[Tuple[float, float, float, float]] = Field(default=None)
    baseline_norm: Optional[List[float]] = Field(default=None)


class EditorialNote(BaseModel):
    """Explicit editorial note attached to a restored segment."""

    type: str
    note: Optional[str] = Field(default=None)
    from_text: Optional[str] = Field(default=None)
    to_text: Optional[str] = Field(default=None)


class ReviewInfo(BaseModel):
    """Review state for restoration artifacts."""

    status: ReviewStatus = Field(default="machine_only")
    reviewer: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)
    notes: Optional[List[str]] = Field(default=None)


class DiplomaticSegment(BaseModel):
    """Witness-near restored segment derived from a canonical page zone."""

    segment_id: str
    zone_id: str
    role: SegmentRole
    placement: SegmentPlacement
    sequence_index: int = Field(ge=0)
    column_index: Optional[int] = Field(default=None, ge=0)
    line_index: Optional[int] = Field(default=None, ge=0)
    text: str
    break_after: BreakAfter = Field(default="line")
    certainty: SegmentCertainty = Field(default="certain")
    anchors: Optional[SegmentAnchors] = Field(default=None)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence_spans: List[Span] = Field(default_factory=list)
    editorial: Optional[List[EditorialNote]] = Field(default=None)


class DiplomaticPage(BaseModel):
    """Authoritative restored page artifact derived from canonical.page."""

    artifact_type: Literal["diplomatic.page"] = Field(default="diplomatic.page")
    doc_id: str
    page_id: str
    created_at: str
    source_schema: Literal["canonical.page"] = Field(default="canonical.page")
    source_image_path: str
    basis_layer: str
    segments: List[DiplomaticSegment] = Field(default_factory=list)
    linear_text: str
    layout_projection: Optional[LayoutProjection] = Field(default=None)
    fidelity_flags: Optional[List[str]] = Field(default=None)
    open_questions: Optional[List[str]] = Field(default=None)
    review: Optional[ReviewInfo] = Field(default=None)
    render_hints: Optional[Dict[str, Any]] = Field(default=None)

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(exclude_none=True, indent=2, **kwargs)

    @classmethod
    def from_file(cls, path: str) -> "DiplomaticPage":
        """Load diplomatic page from JSON file."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str) -> None:
        """Save diplomatic page to JSON file."""
        Path(path).write_text(self.to_json(), encoding="utf-8")


class DiplomaticBookPageRef(BaseModel):
    """Book-level reference to a page restoration artifact."""

    page_id: str
    path: Optional[str] = Field(default=None)
    segment_count: int = Field(ge=0)
    line_count: int = Field(ge=0)


class DiplomaticBook(BaseModel):
    """Ordered assembly of restored pages for a document."""

    artifact_type: Literal["diplomatic.book"] = Field(default="diplomatic.book")
    doc_id: str
    created_at: str
    pages: List[DiplomaticBookPageRef] = Field(default_factory=list)
    book_text: str
    review: Optional[ReviewInfo] = Field(default=None)
    assembly_notes: Optional[List[str]] = Field(default=None)

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(exclude_none=True, indent=2, **kwargs)

    @classmethod
    def from_file(cls, path: str) -> "DiplomaticBook":
        """Load diplomatic book from JSON file."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str) -> None:
        """Save diplomatic book to JSON file."""
        Path(path).write_text(self.to_json(), encoding="utf-8")
