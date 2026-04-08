# Palimpsest archives

This directory holds code and documentation that was retired when the
project was restructured from a manuscript publishing pipeline into a
dual-mode prospecting + ingestion engine feeding Ariadne (a Mundaneum-
class knowledge compiler).

Files here are NOT deleted — they're preserved for reference and git
history. They are not imported by any live code.

## `2026-04-07_publishing_stack/`

Retired 2026-04-07 on branch `restructure/cleanup-2026-04-07`. Captures
the publishing/reader/web stack that existed to produce static HTML
"book" editions of transcribed manuscripts, plus stale documentation
from that era.

**Why retired:** the publishing goal is dead. The new mission is to
feed Palimpsest outputs into Ariadne as structured manifest artifacts,
not to package them as HTML reader sites. See
`../../docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md` and
`../../docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md` for
the full strategic framing.

**What's here:**

- `palimpsest/commands/book.py` — CLI wrapper for the static reader site
- `palimpsest/publish.py` — HTML book-site builder
- `palimpsest/reader/` — reader-site artifact builders (witness, common)
- `palimpsest/web/` — HTML rendering utilities (folio pages, markup,
  theme, structured faces)
- `palimpsest/models/` — dead models that only served the web/reader layer
  (folio_render, packet, continuity, page, zone)
- `docs/` — retired design documents from the publishing era (READER_PRODUCT,
  FOLIO_RENDER_CONTRACT, PAGE_EVIDENCE_SCHEMA, PAGE_PACKET,
  DIPLOMATIC_RESTORATION_CONTRACT, ARCHITECTURAL_BEAUTIFICATION_PLAN,
  TRANSCRIPTION_ARCHITECTURE, REPO_LAYOUT)

**Recovery:** git history is preserved. Use `git log --follow
archives/2026-04-07_publishing_stack/...` to trace any file back through
its pre-archive history.

**Do NOT** import from this directory in live code. It's archived
specifically because nothing live needs it.
