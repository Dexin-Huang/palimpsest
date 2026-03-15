"""Compatibility re-export surface for shared Palimpsest models.

Internal code should prefer concrete submodules such as
``palimpsest.models.packet`` or ``palimpsest.models.layout_probe``.
The package root keeps a narrower star-import surface around the current
product contracts while retaining older root attributes for compatibility.
"""

from .zone import (
    ZoneType,
    TextLayers,
    Confidence,
    ScriptInfo,
    ZoneStyle,
    ZoneStructure,
    ZoneRestorationHints,
    Note,
    Zone,
)
from .page import (
    ImageInfo,
    PreparedImage,
    PreparationStep,
    PreparationInfo,
    Layout,
    Margins,
    PageClassification,
    PageReading,
    PageRestorationHints,
    PageQuality,
    Span,
    Claim,
    PipelineInfo,
    SourceInfo,
    Page,
)
from .packet import (
    ALLOWED_PACKET_STATUSES,
    PacketFileRef,
    PacketWorkflow,
    PagePacket,
)
from .folio_render import (
    SentencePair,
    ColumnWitness,
    MarginaliaEntry,
    WitnessContent,
    InterpretationBlock,
    TermEntry,
    QuestionEntry,
    NoteBlock,
    InterpretationContent,
    FolioRenderSection,
    FolioRenderCover,
    FolioRenderImageRegion,
    FolioRenderImagePanel,
    FolioRenderTextPanel,
    FolioRenderSpread,
    FolioRenderNavigation,
    FolioRender,
)
from .layout_probe import (
    LayoutProbeRegion,
    LayoutProbe,
    RegionRead,
    RegionOrientation,
    PageAssemblyUnit,
    PageAssembly,
    SectionResolutionAssignment,
    PageSectionResolution,
    BoxCleanupDecision,
    PageBoxCleanup,
    PageValidationIssue,
    PageValidation,
)
from .continuity import (
    ContinuityStatus,
    ContinuityItem,
    PageLink,
    PageHandoff,
    WindowSynthesis,
)

__all__ = [
    # Core page contracts
    "Zone",
    "Page",
    # Packet and continuity contracts
    "PacketFileRef",
    "PacketWorkflow",
    "PagePacket",
    "PageHandoff",
    "WindowSynthesis",
    # Reconstruct contracts
    "LayoutProbeRegion",
    "LayoutProbe",
    "RegionRead",
    "PageAssemblyUnit",
    "PageAssembly",
    "PageSectionResolution",
    "PageBoxCleanup",
    "PageValidation",
    # Reader/render contract
    "FolioRender",
]
