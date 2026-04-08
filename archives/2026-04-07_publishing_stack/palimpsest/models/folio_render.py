"""Structured render contract for one folio edition page."""

from typing import Literal, Optional, List, Tuple

from pydantic import BaseModel, Field


# ═══════════════════════════════════════
# Template section block for panel rendering
# ═══════════════════════════════════════

class FolioRenderSection(BaseModel):
    """Reusable section block for a folio panel."""

    kind: Literal[
        "witness",
        "translation",
        "interpretation",
        "notes",
        "terms",
        "questions",
        "other",
    ]
    title: str
    body_html: str
    wide: bool = Field(default=False)


# ═══════════════════════════════════════
# Structured content blocks
# ═══════════════════════════════════════

class SentencePair(BaseModel):
    """One source/translation pair."""

    source: str
    translation: str
    unit_id: Optional[str] = Field(default=None)
    region_id: Optional[str] = Field(default=None)
    bbox_norm: Optional[Tuple[float, float, float, float]] = Field(default=None)


class ColumnWitness(BaseModel):
    """One manuscript column with header and sentence pairs."""

    header_zh: str
    header_en: str
    unit_id: Optional[str] = Field(default=None)
    region_id: Optional[str] = Field(default=None)
    role: Optional[str] = Field(default=None)
    bbox_norm: Optional[Tuple[float, float, float, float]] = Field(default=None)
    page_side: Optional[Literal["left", "right", "center", "full"]] = Field(default=None)
    pairs: List[SentencePair] = Field(default_factory=list)


class MarginaliaEntry(BaseModel):
    """One marginalia annotation."""

    script: str
    position: str
    text: str
    note: Optional[str] = None


class WitnessContent(BaseModel):
    """Structured content for the witness/translation face."""

    columns: List[ColumnWitness] = Field(default_factory=list)
    marginalia: List[MarginaliaEntry] = Field(default_factory=list)


class InterpretationBlock(BaseModel):
    """A titled interpretation paragraph group."""

    title: str
    paragraphs: List[str] = Field(default_factory=list)


class TermEntry(BaseModel):
    """One structured term/name."""

    zh: str
    romanization: Optional[str] = None
    gloss: str
    category: Optional[str] = None


class QuestionEntry(BaseModel):
    """One open question."""

    text: str
    category: Optional[str] = None


class NoteBlock(BaseModel):
    """A titled notes section."""

    title: str
    items: List[str] = Field(default_factory=list)


class InterpretationContent(BaseModel):
    """Structured content for the interpretation/apparatus face."""

    interpretations: List[InterpretationBlock] = Field(default_factory=list)
    terms: List[TermEntry] = Field(default_factory=list)
    questions: List[QuestionEntry] = Field(default_factory=list)
    notes: List[NoteBlock] = Field(default_factory=list)


# ═══════════════════════════════════════
# Panel / spread / page models
# ═══════════════════════════════════════

class FolioRenderCover(BaseModel):
    """Cover page content for one folio."""

    label: str
    title: str
    subtitle: str
    nav_hint: Optional[str] = Field(default=None)


class FolioRenderImagePanel(BaseModel):
    """Left-side image panel."""

    folio_label: str
    source_label: str
    image_path: str
    caption: str
    regions: List["FolioRenderImageRegion"] = Field(default_factory=list)


class FolioRenderImageRegion(BaseModel):
    """One linked image region overlay."""

    region_id: str
    unit_id: Optional[str] = Field(default=None)
    label: str
    role: str
    bbox_norm: Tuple[float, float, float, float]
    page_side: Optional[Literal["left", "right", "center", "full"]] = Field(default=None)


class FolioRenderTextPanel(BaseModel):
    """One text face on the right side of the folio."""

    header_label: str
    header_title: str
    sections: List[FolioRenderSection] = Field(default_factory=list)
    witness_content: Optional[WitnessContent] = Field(default=None)
    interpretation_content: Optional[InterpretationContent] = Field(default=None)


class FolioRenderSpread(BaseModel):
    """The main folio spread."""

    image: FolioRenderImagePanel
    content: FolioRenderTextPanel
    interpretation: FolioRenderTextPanel


class FolioRenderNavigation(BaseModel):
    """Book-level links for moving between folios."""

    home_href: Optional[str] = Field(default=None)
    prev_href: Optional[str] = Field(default=None)
    next_href: Optional[str] = Field(default=None)


class FolioRender(BaseModel):
    """Canonical render payload for one generated folio page."""

    artifact_type: Literal["folio.render"] = Field(default="folio.render")
    created_at: str
    doc_id: str
    page_id: str
    page_label: str
    book_title: str
    page_unit: Literal["page", "spread"]
    source_image_path: str
    cover: FolioRenderCover
    spread: FolioRenderSpread
    navigation: FolioRenderNavigation = Field(default_factory=FolioRenderNavigation)
