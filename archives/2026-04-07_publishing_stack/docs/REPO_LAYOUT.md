# Repo Layout

Purpose: keep Palimpsest split by product lane, not by historical implementation drift.

The canonical system is small:

`discovery -> reconstruct -> packets -> reader`

## 1. Package Boundaries

### `palimpsest/discovery`

Owns:
- source adapters
- source scraping
- manifest-level ingest
- triage
- discovery database

Does not own:
- page reconstruction
- packet authoring
- folio rendering

### `palimpsest/reconstruct`

Owns the canonical page pipeline:

`layout-probe -> region-read -> section-resolution -> validate -> box-cleanup -> assemble`

Files in this lane should be about:
- page geometry
- region reads
- page validation
- targeted visual repair
- page assembly artifacts

It should not contain:
- packet logic
- manuscript-site rendering
- discovery DB logic

### `palimpsest/packets`

Owns:
- `page.packet` creation
- packet repair
- packet continuity
- scholar-agent workflow
- packet templates

It should treat reconstruction outputs as inputs, not reimplement the page pipeline.

### `palimpsest/reader`

Owns:
- folio HTML rendering
- packet site building
- witness reader site building

It should be HTML-first and static-site-first.

It should not contain:
- reconstruction logic
- source discovery logic

### `palimpsest/commands`

Owns:
- thin CLI bindings only

It should not contain core business logic beyond argument plumbing and stage orchestration.

## 2. Current Canonical CLI Surface

- `discovery ...`
- `library ...`
- `page packet`
- `page refresh-packet`
- `page read`
- `page synthesize`
- `page render-html`
- `page handoff`
- `page window`
- `scholar packet`
- `book reader`
- `book site`

Advanced/debug reconstruction rungs remain available under `page`:
- `page layout-probe`
- `page region-read`
- `page section-resolution`
- `page validate`
- `page box-cleanup`
- `page assemble`

## 3. Explicit Non-Goals

These are no longer canonical lanes:

- old `palimpsest/pipeline/*` OCR-style stack
- LaTeX-first packet rendering
- general-purpose `agent.py` CLI surface

## 4. Design Rule

If a new feature does not clearly belong to one of these lanes, it is probably in the wrong place.

Prefer:
- moving code to the right package
- deleting parallel paths
- keeping the CLI thin

over:
- adding one more shared "utils" layer
- keeping multiple competing product stories alive
