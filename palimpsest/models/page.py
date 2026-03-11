"""Page-level models for Palimpsest."""

from typing import Literal, Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field, field_validator
from datetime import datetime

from .zone import Zone

PageType = Literal[
    "text_page",
    "cover",
    "blank",
    "ownership",
    "binding",
    "illustration_only",
    "diagram",
    "map",
    "table",
    "index",
    "other",
]


class Margins(BaseModel):
    """Page margin measurements (normalized 0-1)."""

    top: float = Field(default=0.05, ge=0.0, le=0.5)
    bottom: float = Field(default=0.05, ge=0.0, le=0.5)
    inner: float = Field(default=0.08, ge=0.0, le=0.5)
    outer: float = Field(default=0.12, ge=0.0, le=0.5)


class Layout(BaseModel):
    """Page layout metadata."""

    columns: int = Field(default=1, ge=1, le=4, description="Number of text columns")
    column_gap_norm: float = Field(
        default=0.08, ge=0.0, le=0.3,
        description="Gap between columns as fraction of page width"
    )
    margins: Optional[Margins] = Field(
        default=None,
        description="Page margin measurements"
    )
    ruling: Optional[Literal["none", "dry_point", "lead", "ink"]] = Field(
        default=None,
        description="Type of ruling visible on the page"
    )
    writing_area_bbox_norm: Optional[Tuple[float, float, float, float]] = Field(
        default=None,
        description="Normalized writing area [x, y, w, h] as 0-1 fractions"
    )
    line_count_estimate: Optional[int] = Field(
        default=None,
        ge=0,
        description="Estimated number of main text lines on the page"
    )
    has_marginalia: Optional[bool] = Field(default=None)
    has_interlinear_glosses: Optional[bool] = Field(default=None)
    has_running_header: Optional[bool] = Field(default=None)


class ImageInfo(BaseModel):
    """Metadata about the page image."""

    path: str = Field(description="Relative path to image file")
    width_px: int = Field(gt=0, description="Image width in pixels")
    height_px: int = Field(gt=0, description="Image height in pixels")
    sha256: Optional[str] = Field(default=None, description="SHA256 hash for integrity")
    iiif_url: Optional[str] = Field(default=None, description="Original IIIF image URL")


class PreparedImage(BaseModel):
    """Derived image used for page preparation or rendering."""

    kind: Literal["cropped", "deskewed", "debleeded", "contrast_enhanced", "aligned", "thumbnail"]
    path: str = Field(description="Relative path to derived image")
    width_px: Optional[int] = Field(default=None, gt=0)
    height_px: Optional[int] = Field(default=None, gt=0)
    based_on: Optional[str] = Field(default=None, description="Source image path or derivative id")


class PreparationStep(BaseModel):
    """Single image preparation step applied before reading."""

    name: Literal["crop", "deskew", "debleed", "contrast", "align", "denoise", "other"]
    params: Optional[Dict[str, Any]] = Field(default=None)
    note: Optional[str] = Field(default=None)


class PreparationInfo(BaseModel):
    """Image preparation metadata."""

    prepared_images: Optional[List[PreparedImage]] = Field(default=None)
    steps: Optional[List[PreparationStep]] = Field(default=None)
    preferred_image_kind: Optional[str] = Field(
        default=None,
        description="Which image derivative was used for downstream reading"
    )


class SourceInfo(BaseModel):
    """Provenance and source information."""

    name: str = Field(description="Archive or repository name")
    collection: Optional[str] = Field(default=None)
    source_doc_ref: Optional[str] = Field(default=None, description="Catalog reference")
    provenance_note: Optional[str] = Field(default=None)
    iiif_manifest: Optional[str] = Field(default=None, description="IIIF manifest URL")


class Span(BaseModel):
    """Reference to a text span within a zone."""

    zone_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    layer: Optional[str] = Field(
        default=None,
        description="Which text layer this span refers to"
    )


class Claim(BaseModel):
    """Structured extraction from the page (person, place, date, etc.)."""

    claim_id: str
    type: str = Field(
        description="Claim type: person, place, date, substance, apparatus, process, etc."
    )
    value: str = Field(description="Extracted value")
    span: Optional[Span] = Field(
        default=None,
        description="Reference to source text"
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    attributes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional structured attributes"
    )


class PipelineInfo(BaseModel):
    """Processing pipeline metadata."""

    assumed_components: Optional[Dict[str, str]] = Field(
        default=None,
        description="Component sources: layout, transcription, etc."
    )
    model_versions: Optional[Dict[str, str]] = Field(
        default=None,
        description="Model versions used for each stage"
    )
    notes: Optional[str] = Field(default=None)


class PageClassification(BaseModel):
    """High-level page classification for routing and downstream logic."""

    page_type: PageType = Field(description="Overall page type")
    genre: Optional[str] = Field(
        default=None,
        description="Content genre such as recipe, commentary, itinerary, table, glossary"
    )
    domain_tags: Optional[List[str]] = Field(
        default=None,
        description="High-level topic tags such as alchemy, astronomy, pharmacology, geography"
    )
    languages: Optional[List[str]] = Field(default=None)
    scripts: Optional[List[str]] = Field(default=None)
    has_illustration: Optional[bool] = Field(default=None)
    has_diagram: Optional[bool] = Field(default=None)
    has_table: Optional[bool] = Field(default=None)
    has_marginalia: Optional[bool] = Field(default=None)
    has_interlinear_glosses: Optional[bool] = Field(default=None)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PageReading(BaseModel):
    """Interpretive but evidence-bound understanding of the page."""

    summary: Optional[str] = Field(
        default=None,
        description="Short evidence-bound summary of what the page is doing"
    )
    genre: Optional[str] = Field(default=None)
    domain_tags: Optional[List[str]] = Field(default=None)
    notable_features: Optional[List[str]] = Field(
        default=None,
        description="Signals such as recipe sequence, first-person travel account, site list, table headings"
    )
    first_person_voice: Optional[bool] = Field(default=None)
    procedural_text: Optional[bool] = Field(default=None)
    questions: Optional[List[str]] = Field(
        default=None,
        description="Open questions or ambiguities worth later review"
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PageRestorationHints(BaseModel):
    """Hints for derived facsimile reconstruction and scholarly typesetting."""

    preserve_columns: bool = Field(
        default=True,
        description="Keep original column structure in reconstruction outputs"
    )
    preserve_line_breaks: bool = Field(
        default=True,
        description="Preserve line breaks for diplomatic or facsimile-style outputs"
    )
    preserve_marginalia_positions: bool = Field(
        default=True,
        description="Keep marginalia spatially anchored where possible"
    )
    preserve_interlinear_insertions: bool = Field(
        default=True,
        description="Represent interlinear insertions in situ when possible"
    )
    preserve_rubrication: bool = Field(
        default=True,
        description="Carry rubric color/styling into derived outputs"
    )
    preserve_initials: bool = Field(
        default=True,
        description="Keep decorated initials distinct in derived outputs"
    )
    preferred_text_layer: Optional[str] = Field(
        default=None,
        description="Suggested base layer for rendering, e.g. la_diplomatic or la_normalized"
    )
    output_modes: Optional[List[Literal[
        "diplomatic_edition",
        "normalized_edition",
        "pseudo_facsimile",
        "overlay",
        "tei",
    ]]] = Field(
        default=None,
        description="Recommended render/output modes supported by this page evidence"
    )
    notes: Optional[str] = Field(default=None)


class PageQuality(BaseModel):
    """Assessment of page/image quality for routing and QA."""

    legibility: Optional[Literal["poor", "fair", "good", "excellent"]] = Field(default=None)
    bleed_through: Optional[Literal["none", "light", "moderate", "heavy"]] = Field(default=None)
    skew: Optional[Literal["none", "light", "moderate", "heavy"]] = Field(default=None)
    crop_quality: Optional[Literal["poor", "fair", "good"]] = Field(default=None)
    notes: Optional[str] = Field(default=None)


class Page(BaseModel):
    """Complete page document model."""

    schema_version: Literal["canonical.page"] = Field(
        default="canonical.page",
        description="Canonical schema identifier"
    )
    created_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 creation timestamp"
    )

    page_id: str = Field(description="Unique page identifier")
    doc_id: str = Field(description="Parent document identifier")

    source: Optional[SourceInfo] = Field(default=None)
    image: ImageInfo
    preparation: Optional[PreparationInfo] = Field(default=None)
    reading_direction: Literal["ltr", "rtl", "ttb", "btt"] = Field(default="ltr")
    coordinate_space: Literal["norm01"] = Field(default="norm01")

    classification: Optional[PageClassification] = Field(default=None)
    layout: Optional[Layout] = Field(default=None)
    zones: List[Zone] = Field(default_factory=list)
    claims: Optional[List[Claim]] = Field(default=None)
    reading: Optional[PageReading] = Field(default=None)
    restoration: Optional[PageRestorationHints] = Field(default=None)
    quality: Optional[PageQuality] = Field(default=None)

    pipeline: Optional[PipelineInfo] = Field(default=None)

    @field_validator("zones", mode="after")
    @classmethod
    def validate_zones_not_empty(cls, v):
        if len(v) == 0:
            raise ValueError("zones must not be empty")
        return v

    def zones_by_order(self) -> List[Zone]:
        """Return zones sorted by reading order."""
        return sorted(self.zones, key=lambda z: z.order)

    def zones_by_type(self, zone_type: str) -> List[Zone]:
        """Filter zones by type."""
        return [z for z in self.zones if z.type == zone_type]

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Find zone by ID."""
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        return None

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(exclude_none=True, indent=2, **kwargs)

    @classmethod
    def from_file(cls, path: str) -> "Page":
        """Load page from JSON file."""
        from pathlib import Path
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str) -> None:
        """Save page to JSON file."""
        from pathlib import Path
        Path(path).write_text(self.to_json(), encoding="utf-8")
