"""Page-level models for Palimpsest."""

from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime

from .zone import Zone


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


class ImageInfo(BaseModel):
    """Metadata about the page image."""

    path: str = Field(description="Relative path to image file")
    width_px: int = Field(gt=0, description="Image width in pixels")
    height_px: int = Field(gt=0, description="Image height in pixels")
    sha256: Optional[str] = Field(default=None, description="SHA256 hash for integrity")
    iiif_url: Optional[str] = Field(default=None, description="Original IIIF image URL")


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


class Page(BaseModel):
    """Complete page document model (rescript.page.v1 schema)."""

    schema_version: Literal["rescript.page.v1"] = Field(
        default="rescript.page.v1",
        description="Schema version identifier"
    )
    created_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 creation timestamp"
    )

    page_id: str = Field(description="Unique page identifier")
    doc_id: str = Field(description="Parent document identifier")

    source: Optional[SourceInfo] = Field(default=None)
    image: ImageInfo
    reading_direction: Literal["ltr", "rtl"] = Field(default="ltr")
    coordinate_space: Literal["norm01"] = Field(default="norm01")

    layout: Optional[Layout] = Field(default=None)
    zones: List[Zone] = Field(default_factory=list)
    claims: Optional[List[Claim]] = Field(default=None)

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
