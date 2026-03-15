"""Compatibility facade for packet workflow helpers.

New code should prefer the owning modules:
- ``palimpsest.packets.decode`` for packet-local decode orchestration
- ``palimpsest.packets.doc_pages`` for document page-list helpers
"""

from .decode import LayoutPipelineResult, PacketDecodeResult, run_layout_pipeline, run_packet_decode

__all__ = [
    "LayoutPipelineResult",
    "PacketDecodeResult",
    "run_layout_pipeline",
    "run_packet_decode",
]
