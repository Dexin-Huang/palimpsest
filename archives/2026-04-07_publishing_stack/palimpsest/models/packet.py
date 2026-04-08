"""Page packet models for the scholar-facing workflow."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ALLOWED_PACKET_STATUSES = ("empty", "started", "draft", "reviewed", "complete")
PacketStatus = Literal["empty", "started", "draft", "reviewed", "complete"]


class PacketFileRef(BaseModel):
    """One file that belongs to the working page packet."""

    kind: str
    path: str
    status: PacketStatus = Field(default="empty")
    note: Optional[str] = Field(default=None)


class PacketWorkflow(BaseModel):
    """Workflow metadata for the page packet."""

    primary_reasoner: Literal["claude_agent_sdk", "human", "other"] = Field(default="claude_agent_sdk")
    witness_model: Optional[str] = Field(default=None)
    synthesis_model: Optional[str] = Field(default=None)
    next_action: Optional[str] = Field(default=None)


class PacketContinuity(BaseModel):
    """Hot continuity references carried with the packet."""

    previous_packet_path: Optional[str] = Field(default=None)
    previous_handoff_path: Optional[str] = Field(default=None)
    window_synthesis_path: Optional[str] = Field(default=None)


class PagePacket(BaseModel):
    """A page-level scholar packet that accumulates evidence and notes."""

    artifact_type: Literal["page.packet"] = Field(default="page.packet")
    created_at: str
    doc_id: str
    page_id: str
    page_unit: Literal["page", "spread"] = Field(default="page")
    source_image_path: str
    prepared_image_path: Optional[str] = Field(default=None)
    files: Dict[str, PacketFileRef] = Field(default_factory=dict)
    open_questions: List[str] = Field(default_factory=list)
    continuity: PacketContinuity = Field(default_factory=PacketContinuity)
    workflow: PacketWorkflow = Field(default_factory=PacketWorkflow)
    notes: Optional[List[str]] = Field(default=None)
