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
    """Follow-up orientation read for one crop."""

    artifact_type: Literal["page.region_orientation", "page.region_read"] = Field(default="page.region_read")
    page_id: str
    region_id: str
    label: str
    role: str
    reading_direction: Optional[str] = Field(default=None)
    line_flow: Optional[str] = Field(default=None)
    start_edge: Optional[str] = Field(default=None)
    script_hint: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    pairs: List["RegionReadPair"] = Field(default_factory=list)
    diplomatic_lines: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @field_validator("pairs", mode="before")
    @classmethod
    def _normalize_pairs(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        normalized: list[dict] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"source": item, "translation": ""})
            elif isinstance(item, dict):
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def _sync_pair_lines(self):
        if self.pairs and not self.diplomatic_lines:
            self.diplomatic_lines = [pair.source for pair in self.pairs if pair.source]
        elif self.diplomatic_lines and not self.pairs:
            self.pairs = [RegionReadPair(source=line, translation="") for line in self.diplomatic_lines]
        return self


class RegionReadPair(BaseModel):
    """One aligned source/direct-translation unit from a cropped region."""

    source: str
    translation: str = Field(default="")
    kind: Literal["line", "header", "page_number", "marginalia", "other"] = Field(default="line")
    note: Optional[str] = Field(default=None)


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
    reading_direction: Optional[str] = Field(default=None)
    line_flow: Optional[str] = Field(default=None)
    start_edge: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    pairs: List[RegionReadPair] = Field(default_factory=list)
    diplomatic_lines: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_pair_lines(self):
        if self.pairs and not self.diplomatic_lines:
            self.diplomatic_lines = [pair.source for pair in self.pairs if pair.source]
        elif self.diplomatic_lines and not self.pairs:
            self.pairs = [RegionReadPair(source=line, translation="") for line in self.diplomatic_lines]
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
