"""Legacy models retained for reader/packet site compatibility."""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


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
