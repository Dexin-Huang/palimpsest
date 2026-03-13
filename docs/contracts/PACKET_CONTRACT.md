# Packet Contract

Owner: packets

Source constants: `palimpsest/contracts.py`

## Workspace Naming

Canonical packet workspace inside a library document:
- `experiments/<page_id>_packet_v1/`

Canonical packet files:
- `packet.json`
- `packet_meta.json`
- `witness.md`
- `notes.md`
- `translation.md`
- `interpretation.md`
- `terms.md`
- `questions.md`
- `index.html`
- `render.json`

Packet-local reconstruct subtree:
- `layout_probe/layout_probe.json`
- `layout_probe/layout_overlay.png`
- `layout_probe/region_reads.json`
- `layout_probe/section_resolution.json`
- `layout_probe/box_cleanup.json`
- `layout_probe/page_validation.json`
- `layout_probe/page_assembly.json`

## Semantics

- `packet.json` is the canonical scholar-facing state bundle for one page unit.
- `packet_meta.json` is generation metadata, not the primary state record.
- markdown files are the editable scholar-facing working artifacts.
- `index.html` and `render.json` are render outputs that may be referenced from packet state.
- packet repair may normalize missing or stale file references back onto the canonical workspace layout.

## Mutation Rules

- packet workflow code may update `packet.json`.
- reader render code should not mutate `packet.json` directly.
- render-output sync into packet state should happen explicitly in packet or page workflow callers.
