"""Segmentation models for vision pipeline.

Defines data structures for page segmentation results from multi-pass
analysis using Gemini with Agentic Vision.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any


class RegionType(str, Enum):
    """Types of regions detected in manuscript pages."""

    TEXT_BLOCK = "text_block"
    TEXT_LINE = "text_line"
    INITIAL = "initial"  # Decorated/enlarged initial letter
    RUBRIC = "rubric"  # Red header/title
    MARGINALIA = "marginalia"
    ILLUSTRATION = "illustration"
    DIAGRAM = "diagram"
    TABLE = "table"
    DECORATION = "decoration"  # Non-textual decoration
    COLOPHON = "colophon"
    CATCHWORD = "catchword"
    SIGNATURE = "signature"  # Quire signature
    FOLIO_NUMBER = "folio_number"
    STAMP = "stamp"
    DAMAGE = "damage"  # Damaged/illegible area
    BLANK = "blank"
    UNKNOWN = "unknown"


@dataclass
class SegmentedRegion:
    """A segmented region from page analysis."""

    region_id: str
    region_type: RegionType
    bbox_norm: Tuple[float, float, float, float]  # [x, y, w, h] normalized 0-1
    confidence: float = 1.0

    # Optional hierarchical structure
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)

    # Analysis metadata
    reading_order: Optional[int] = None
    column: Optional[int] = None  # 1, 2, etc. for multi-column layouts

    # Content hints from initial analysis
    estimated_lines: Optional[int] = None
    has_decoration: bool = False
    ink_colors: List[str] = field(default_factory=list)  # ["black", "red", "blue"]
    script_style: Optional[str] = None  # "Gothic textualis", "Humanist", etc.

    # For illustrations
    subject_hint: Optional[str] = None  # Brief description
    contains_text: bool = False  # Labeled illustration

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "region_id": self.region_id,
            "region_type": self.region_type.value,
            "bbox_norm": list(self.bbox_norm),
            "confidence": self.confidence,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "reading_order": self.reading_order,
            "column": self.column,
            "estimated_lines": self.estimated_lines,
            "has_decoration": self.has_decoration,
            "ink_colors": self.ink_colors,
            "script_style": self.script_style,
            "subject_hint": self.subject_hint,
            "contains_text": self.contains_text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SegmentedRegion":
        """Create from dictionary."""
        return cls(
            region_id=data["region_id"],
            region_type=RegionType(data["region_type"]),
            bbox_norm=tuple(data["bbox_norm"]),
            confidence=data.get("confidence", 1.0),
            parent_id=data.get("parent_id"),
            children_ids=data.get("children_ids", []),
            reading_order=data.get("reading_order"),
            column=data.get("column"),
            estimated_lines=data.get("estimated_lines"),
            has_decoration=data.get("has_decoration", False),
            ink_colors=data.get("ink_colors", []),
            script_style=data.get("script_style"),
            subject_hint=data.get("subject_hint"),
            contains_text=data.get("contains_text", False),
        )


@dataclass
class LayoutAnalysis:
    """Page layout analysis results."""

    columns: int = 1
    column_widths: List[float] = field(default_factory=list)  # Normalized widths
    column_gap: float = 0.0

    # Margins (normalized)
    margin_top: float = 0.0
    margin_bottom: float = 0.0
    margin_inner: float = 0.0
    margin_outer: float = 0.0

    # Text area
    text_area_bbox: Optional[Tuple[float, float, float, float]] = None

    # Ruling/guidelines
    has_ruling: bool = False
    ruling_type: Optional[str] = None  # "dry_point", "lead", "ink"

    # Page type
    page_type: str = "text"  # "text", "illustration", "mixed", "blank"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "columns": self.columns,
            "column_widths": self.column_widths,
            "column_gap": self.column_gap,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "margin_inner": self.margin_inner,
            "margin_outer": self.margin_outer,
            "text_area_bbox": list(self.text_area_bbox) if self.text_area_bbox else None,
            "has_ruling": self.has_ruling,
            "ruling_type": self.ruling_type,
            "page_type": self.page_type,
        }


@dataclass
class IllustrationAnalysis:
    """Detailed analysis of an illustration region."""

    region_id: str
    bbox_norm: Tuple[float, float, float, float]

    # Content analysis
    subject: str = ""  # Main subject description
    figures: List[Dict[str, Any]] = field(default_factory=list)  # Named figures
    objects: List[str] = field(default_factory=list)  # Identified objects
    symbols: List[str] = field(default_factory=list)  # Symbolic elements

    # Text in illustration
    labels: List[Dict[str, str]] = field(default_factory=list)  # [{text, position}]
    inscriptions: List[str] = field(default_factory=list)

    # Style
    technique: Optional[str] = None  # "pen drawing", "wash", "gilded"
    colors: List[str] = field(default_factory=list)
    style_period: Optional[str] = None  # "Romanesque", "Gothic", "Renaissance"

    # Iconographic identification
    iconographic_type: Optional[str] = None  # "Triumph", "Wheel of Fortune", etc.
    related_text: Optional[str] = None  # Reference to accompanying text

    # Condition
    condition: str = "good"  # "good", "faded", "damaged", "partial"
    condition_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "region_id": self.region_id,
            "bbox_norm": list(self.bbox_norm),
            "subject": self.subject,
            "figures": self.figures,
            "objects": self.objects,
            "symbols": self.symbols,
            "labels": self.labels,
            "inscriptions": self.inscriptions,
            "technique": self.technique,
            "colors": self.colors,
            "style_period": self.style_period,
            "iconographic_type": self.iconographic_type,
            "related_text": self.related_text,
            "condition": self.condition,
            "condition_notes": self.condition_notes,
        }


@dataclass
class SegmentationResult:
    """Complete segmentation result for a page."""

    page_id: str
    image_width: int
    image_height: int

    # Layout analysis
    layout: LayoutAnalysis = field(default_factory=LayoutAnalysis)

    # Detected regions
    regions: List[SegmentedRegion] = field(default_factory=list)

    # Illustration analyses (for illustration regions)
    illustrations: List[IllustrationAnalysis] = field(default_factory=list)

    # Processing metadata
    model: str = ""
    processing_time_ms: Optional[int] = None
    passes_completed: List[str] = field(default_factory=list)

    def get_regions_by_type(self, region_type: RegionType) -> List[SegmentedRegion]:
        """Get all regions of a specific type."""
        return [r for r in self.regions if r.region_type == region_type]

    def get_reading_order(self) -> List[SegmentedRegion]:
        """Get regions sorted by reading order."""
        ordered = [r for r in self.regions if r.reading_order is not None]
        return sorted(ordered, key=lambda r: r.reading_order)

    def get_text_regions(self) -> List[SegmentedRegion]:
        """Get all text-containing regions in reading order."""
        text_types = {
            RegionType.TEXT_BLOCK,
            RegionType.TEXT_LINE,
            RegionType.RUBRIC,
            RegionType.MARGINALIA,
            RegionType.COLOPHON,
        }
        text_regions = [r for r in self.regions if r.region_type in text_types]
        return sorted(text_regions, key=lambda r: r.reading_order or 999)

    def get_illustration(self, region_id: str) -> Optional[IllustrationAnalysis]:
        """Get illustration analysis by region ID."""
        for ill in self.illustrations:
            if ill.region_id == region_id:
                return ill
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "page_id": self.page_id,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "layout": self.layout.to_dict(),
            "regions": [r.to_dict() for r in self.regions],
            "illustrations": [i.to_dict() for i in self.illustrations],
            "model": self.model,
            "processing_time_ms": self.processing_time_ms,
            "passes_completed": self.passes_completed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SegmentationResult":
        """Create from dictionary."""
        layout_data = data.get("layout", {})
        layout = LayoutAnalysis(
            columns=layout_data.get("columns", 1),
            column_widths=layout_data.get("column_widths", []),
            column_gap=layout_data.get("column_gap", 0.0),
            margin_top=layout_data.get("margin_top", 0.0),
            margin_bottom=layout_data.get("margin_bottom", 0.0),
            margin_inner=layout_data.get("margin_inner", 0.0),
            margin_outer=layout_data.get("margin_outer", 0.0),
            text_area_bbox=tuple(layout_data["text_area_bbox"]) if layout_data.get("text_area_bbox") else None,
            has_ruling=layout_data.get("has_ruling", False),
            ruling_type=layout_data.get("ruling_type"),
            page_type=layout_data.get("page_type", "text"),
        )

        return cls(
            page_id=data["page_id"],
            image_width=data["image_width"],
            image_height=data["image_height"],
            layout=layout,
            regions=[SegmentedRegion.from_dict(r) for r in data.get("regions", [])],
            illustrations=[
                IllustrationAnalysis(
                    region_id=i["region_id"],
                    bbox_norm=tuple(i["bbox_norm"]),
                    subject=i.get("subject", ""),
                    figures=i.get("figures", []),
                    objects=i.get("objects", []),
                    symbols=i.get("symbols", []),
                    labels=i.get("labels", []),
                    inscriptions=i.get("inscriptions", []),
                    technique=i.get("technique"),
                    colors=i.get("colors", []),
                    style_period=i.get("style_period"),
                    iconographic_type=i.get("iconographic_type"),
                    related_text=i.get("related_text"),
                    condition=i.get("condition", "good"),
                    condition_notes=i.get("condition_notes"),
                )
                for i in data.get("illustrations", [])
            ],
            model=data.get("model", ""),
            processing_time_ms=data.get("processing_time_ms"),
            passes_completed=data.get("passes_completed", []),
        )
