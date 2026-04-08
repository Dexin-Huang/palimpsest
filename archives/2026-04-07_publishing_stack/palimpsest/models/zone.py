"""Zone and text layer models for Palimpsest."""

from typing import Literal, Optional, List, Tuple, Annotated
from pydantic import BaseModel, Field, field_validator

ZoneType = Literal[
    "text_block",     # multi-line main text block
    "line",           # regular text line
    "rubric",         # red header/title text
    "initial",        # decorated capital letter
    "marginalia",     # marginal notes
    "interlinear",    # gloss or insertion between lines
    "illustration",   # drawings/diagrams
    "diagram_label",  # text labeling a diagram or figure
    "caption",        # caption-like explanatory text
    "colophon",       # scribal signatures
    "catchword",      # page-turn guides
    "column_break",   # marks column boundaries
    "header",         # page header
    "footer",         # page footer
    "page_number",    # folio number or pagination
    "stamp",          # archival stamps
    "table_cell",     # table cell content
    "unknown",        # unclassified
]


class TextLayers(BaseModel):
    """Multi-layer text transcription and translation."""

    # Generic source-language layers for the manuscript's original text.
    source_diplomatic: Optional[str] = Field(
        default=None,
        description="Exact transcription in the source language, preserving original spelling and abbreviations"
    )
    source_normalized: Optional[str] = Field(
        default=None,
        description="Normalized or expanded transcription in the source language"
    )

    # Latin layers (for medieval manuscripts)
    la_diplomatic: Optional[str] = Field(
        default=None,
        description="Exact transcription with original abbreviations and spelling"
    )
    la_normalized: Optional[str] = Field(
        default=None,
        description="Expanded abbreviations, standard Latin spelling"
    )

    # Spanish layers (for colonial archives)
    es_diplomatic: Optional[str] = Field(
        default=None,
        description="Exact transcription preserving original Spanish spelling"
    )
    es_normalized: Optional[str] = Field(
        default=None,
        description="Modernized Spanish spelling"
    )

    # English layers
    en_literal: Optional[str] = Field(
        default=None,
        description="Direct English translation"
    )
    en_interpreted: Optional[str] = Field(
        default=None,
        description="Contextual English interpretation"
    )

    def get_layer(self, layer: str) -> Optional[str]:
        """Get text from a specific layer by name."""
        return getattr(self, layer, None)

    def primary_text(self) -> Optional[str]:
        """Return first available text layer (diplomatic preferred)."""
        for layer in [
            "source_diplomatic",
            "la_diplomatic",
            "es_diplomatic",
            "source_normalized",
            "la_normalized",
            "es_normalized",
            "en_literal",
            "en_interpreted",
        ]:
            text = getattr(self, layer, None)
            if text:
                return text
        return None


class Confidence(BaseModel):
    """Confidence scores for different processing stages."""

    layout: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in zone geometry and layout classification"
    )
    transcription: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in OCR/transcription accuracy"
    )
    normalization: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in abbreviation expansion and normalization"
    )
    translation: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in translation accuracy"
    )
    reading: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in genre/content interpretation"
    )
    extraction: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence in structured extraction derived from this zone"
    )


class ScriptInfo(BaseModel):
    """Script/language metadata for a zone."""

    language: Optional[str] = Field(default=None, description="Language used in this zone")
    script: Optional[str] = Field(default=None, description="Script family, e.g. latin, greek, han")
    hand: Optional[str] = Field(default=None, description="Hand or scribal identifier when known")
    abbreviation_density: Optional[Literal["low", "medium", "high"]] = Field(
        default=None,
        description="Relative abbreviation density"
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ZoneStyle(BaseModel):
    """Visual styling metadata for a zone."""

    color: Optional[Literal["rubric", "initial_blue", "ink", "faded"]] = Field(
        default=None,
        description="Ink/pigment color: rubric (red), initial_blue, ink (black), faded"
    )
    font_size: Optional[Literal["small", "normal", "large", "initial"]] = Field(
        default=None,
        description="Relative text size"
    )
    decoration: Optional[Literal["flourish", "border", "pen_work", "gilded"]] = Field(
        default=None,
        description="Decorative elements"
    )


class ZoneStructure(BaseModel):
    """Structural placement metadata for reading and reconstruction."""

    region_role: Optional[Literal[
        "main_text",
        "margin_outer",
        "margin_inner",
        "marginalia",
        "header",
        "footer",
        "page_number",
        "interlinear",
        "paratext",
        "illustration_label",
        "table",
        "diagram",
    ]] = Field(default=None, description="Functional role of the zone on the page")
    column_index: Optional[int] = Field(default=None, ge=0, description="Zero-based column index")
    line_index: Optional[int] = Field(default=None, ge=0, description="Zero-based line index within its block")
    parent_zone_id: Optional[str] = Field(default=None, description="Parent block or region zone id")
    next_zone_id: Optional[str] = Field(default=None, description="Next zone in local reading order")
    section_label: Optional[str] = Field(default=None, description="Section marker or local header label")


class ZoneRestorationHints(BaseModel):
    """Hints for facsimile reconstruction and scholarly typesetting."""

    preserve_line_break_after: Optional[bool] = Field(
        default=None,
        description="Whether a render should preserve a break after this zone"
    )
    indent_level: Optional[int] = Field(default=None, ge=0, description="Relative indent level")
    alignment: Optional[Literal["left", "center", "right", "justify"]] = Field(
        default=None,
        description="Preferred text alignment when reconstructing the page"
    )
    render_class: Optional[str] = Field(
        default=None,
        description="Renderer hint such as main_text, rubric, marginal_note, initial"
    )


class Note(BaseModel):
    """Annotation or comment on a zone."""

    type: str = Field(description="Note type: abbreviation, content, correction, etc.")
    description: Optional[str] = Field(default=None)
    # For abbreviation notes
    from_text: Optional[str] = Field(default=None, alias="from")
    to_text: Optional[str] = Field(default=None, alias="to")

    class Config:
        populate_by_name = True


BboxNorm = Annotated[
    Tuple[float, float, float, float],
    Field(description="Normalized bounding box [x, y, width, height] as 0-1 fractions")
]


class Zone(BaseModel):
    """A text region on the page with transcription and metadata."""

    zone_id: str = Field(description="Unique identifier within the page")
    type: ZoneType = Field(description="Zone classification")
    order: int = Field(description="Reading order position")
    bbox_norm: BboxNorm = Field(
        description="Normalized bounding box [x, y, w, h] as 0-1 fractions"
    )
    baseline_norm: Optional[List[float]] = Field(
        default=None,
        description="Normalized baseline coordinates for text alignment"
    )
    text: TextLayers = Field(description="Multi-layer text content")
    script: Optional[ScriptInfo] = Field(
        default=None,
        description="Language/script metadata for the zone"
    )
    style: Optional[ZoneStyle] = Field(
        default=None,
        description="Visual styling metadata"
    )
    structure: Optional[ZoneStructure] = Field(
        default=None,
        description="Reading and page-structure metadata"
    )
    restoration: Optional[ZoneRestorationHints] = Field(
        default=None,
        description="Hints for facsimile reconstruction and typesetting"
    )
    confidence: Optional[Confidence] = Field(
        default=None,
        description="Processing confidence scores"
    )
    notes: Optional[List[Note]] = Field(
        default=None,
        description="Annotations and comments"
    )

    @field_validator("bbox_norm", mode="before")
    @classmethod
    def validate_bbox(cls, v):
        """Convert list to tuple and validate bounds."""
        if isinstance(v, list):
            v = tuple(v)
        if len(v) != 4:
            raise ValueError("bbox_norm must have exactly 4 values [x, y, w, h]")
        for i, val in enumerate(v):
            if not isinstance(val, (int, float)):
                raise ValueError(f"bbox_norm[{i}] must be numeric")
            if val < 0 or val > 1.5:  # Allow slight overflow for edge cases
                raise ValueError(f"bbox_norm[{i}]={val} out of range (expected 0-1)")
        return v

    @property
    def x(self) -> float:
        return self.bbox_norm[0]

    @property
    def y(self) -> float:
        return self.bbox_norm[1]

    @property
    def width(self) -> float:
        return self.bbox_norm[2]

    @property
    def height(self) -> float:
        return self.bbox_norm[3]
