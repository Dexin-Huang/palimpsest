from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


class LayoutProbeRegion(BaseModel):
    """A coarse semantic region proposed for downstream transcription or review."""

    region_id: str
    label: str
    role: str
    bbox_norm: Tuple[float, float, float, float]
    reading_order: Optional[int] = Field(default=None)
    contains_text: bool = Field(default=True)
    confidence: Optional[float] = Field(default=None)
    page_side: Optional[Literal["left", "right", "center", "full"]] = Field(default=None)
    column_index: Optional[int] = Field(default=None)
    reconstruction_priority: Optional[Literal["high", "medium", "low", "ignore"]] = Field(default=None)
    ignore_for_reconstruction: bool = Field(default=False)
    script_hint: Optional[str] = Field(default=None)
    orientation_hint: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)

    @field_validator("bbox_norm", mode="before")
    @classmethod
    def _validate_bbox(cls, value):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("bbox_norm must be [x, y, w, h]")
        coords = tuple(float(item) for item in value)
        for index, coord in enumerate(coords):
            if coord < 0.0 or coord > 1.0:
                raise ValueError(f"bbox_norm[{index}] out of range: {coord}")
        return coords

    @field_validator("page_side", mode="before")
    @classmethod
    def _normalize_page_side(cls, value):
        if value is None:
            return value
        text = str(value).strip().lower()
        if text in {"both", "all", "whole", "entire"}:
            return "full"
        if text in {"middle"}:
            return "center"
        if text in {"left", "right", "center", "full"}:
            return text
        return None

    @field_validator("reconstruction_priority", mode="before")
    @classmethod
    def _normalize_priority(cls, value):
        if value is None:
            return value
        text = str(value).strip().lower()
        if text in {"skip", "none", "discard"}:
            return "ignore"
        if text in {"high", "medium", "low", "ignore"}:
            return text
        return None


class LayoutProbe(BaseModel):
    """Top-level coarse page layout proposal."""

    artifact_type: Literal["page.layout_probe"] = Field(default="page.layout_probe")
    created_at: str
    doc_id: str
    page_id: str
    image_path: str
    page_unit: Literal["page", "spread"] = Field(default="page")
    writing_area_bbox_norm: Optional[Tuple[float, float, float, float]] = Field(default=None)
    regions: List[LayoutProbeRegion] = Field(default_factory=list)


class RegionOrientation(BaseModel):
    """Full transcription read for one coarse page region."""

    artifact_type: Literal["page.region_read"] = Field(default="page.region_read")
    page_id: str
    region_id: str
    label: str
    role: str
    source_block: Optional[str] = Field(default=None)
    diplomatic_lines: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_lines(self):
        if self.source_block and not self.diplomatic_lines:
            self.diplomatic_lines = [line for line in self.source_block.splitlines() if line.strip()]
        if not self.source_block and self.diplomatic_lines:
            self.source_block = "\n".join(self.diplomatic_lines)
        return self


class PageAssemblyUnit(BaseModel):
    """One assembled witness unit tied to a page region."""

    unit_id: str
    region_id: str
    label: str
    role: str
    bbox_norm: Tuple[float, float, float, float]
    page_side: Optional[Literal["left", "right", "center", "full"]] = Field(default=None)
    column_index: Optional[int] = Field(default=None)
    reading_order: Optional[int] = Field(default=None)
    source_block: Optional[str] = Field(default=None)
    diplomatic_lines: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_lines(self):
        if self.source_block and not self.diplomatic_lines:
            self.diplomatic_lines = [line for line in self.source_block.splitlines() if line.strip()]
        if not self.source_block and self.diplomatic_lines:
            self.source_block = "\n".join(self.diplomatic_lines)
        return self


class PageAssembly(BaseModel):
    """Deterministically assembled page witness from region reads."""

    artifact_type: Literal["page.assembly"] = Field(default="page.assembly")
    created_at: str
    doc_id: str
    page_id: str
    image_path: str
    page_unit: Literal["page", "spread"] = Field(default="page")
    units: List[PageAssemblyUnit] = Field(default_factory=list)


class SectionResolutionAssignment(BaseModel):
    """Canonicalized text ownership for one coarse region."""

    region_id: str
    label: str
    role: str
    page_side: Optional[Literal["left", "right", "center", "full"]] = Field(default=None)
    column_index: Optional[int] = Field(default=None)
    render_in_witness: bool = Field(default=True)
    source_block: str = Field(default="")
    notes: List[str] = Field(default_factory=list)


class PageSectionResolution(BaseModel):
    """Page-level canonical region text assignment after inclusive-box transcription."""

    artifact_type: Literal["page.section_resolution"] = Field(default="page.section_resolution")
    created_at: str
    doc_id: str
    page_id: str
    assignments: List[SectionResolutionAssignment] = Field(default_factory=list)


class BoxCleanupDecision(BaseModel):
    """Cleaned ownership split for one overlapping pair of coarse regions."""

    pair_id: str
    region_a: str
    region_b: str
    relation: Literal["no_overlap", "region_a_owns_overlap", "region_b_owns_overlap", "shared", "uncertain"]
    cleaned_source_block_a: str = Field(default="")
    cleaned_source_block_b: str = Field(default="")
    reason: Optional[str] = Field(default=None)


class PageBoxCleanup(BaseModel):
    """Targeted cleanup for overlapping coarse region pairs."""

    artifact_type: Literal["page.box_cleanup"] = Field(default="page.box_cleanup")
    created_at: str
    doc_id: str
    page_id: str
    decisions: List[BoxCleanupDecision] = Field(default_factory=list)


class PageValidationIssue(BaseModel):
    """A structural issue detected in an assembled page."""

    issue_id: str
    issue_type: Literal[
        "header_spill_into_main",
        "marginalia_script_contamination",
        "page_number_noise",
    ]
    severity: Literal["low", "medium", "high"] = Field(default="medium")
    region_ids: List[str] = Field(default_factory=list)
    message: str
    evidence: List[str] = Field(default_factory=list)


class PageValidation(BaseModel):
    """Cheap page-level structural QA after assembly."""

    artifact_type: Literal["page.validation"] = Field(default="page.validation")
    created_at: str
    doc_id: str
    page_id: str
    status: Literal["clean", "needs_repair"] = Field(default="clean")
    score: int = Field(default=0)
    issues: List[PageValidationIssue] = Field(default_factory=list)
