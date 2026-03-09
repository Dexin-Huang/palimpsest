"""Structured render contract for one folio edition page."""

from typing import Literal, Optional, List

from pydantic import BaseModel, Field


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


class FolioRenderTextPanel(BaseModel):
    """One text face on the right side of the folio."""

    header_label: str
    header_title: str
    sections: List[FolioRenderSection] = Field(default_factory=list)


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
