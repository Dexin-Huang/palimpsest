# Palimpsest

Palimpsest reads the tracks history forgot.

Palimpsest is a manuscript discovery and reading system for digitized archival
corpora. It is built for a specific goal: recover neglected human worlds from
scans that exist online but are thinly cataloged, weakly indexed, or rarely
read end to end.

The project is not centered on OCR as an end in itself. Its operating model is:

`source discovery -> page witness -> scholar packet -> section synthesis -> edition`

The first product is not a generic archive crawler. It is a reliable,
repeatable way to turn a page image into:

- a witness-first transcription
- a compact scholarly workspace
- a translation and interpretation layer
- a rendered edition spread

## What It Does

- Discovers candidates from public digital sources such as Vatican IIIF,
  Gallica, and IDP.
- Maintains a discovery database with source metadata, triage scores, and
  source-specific context.
- Prepares page images deterministically before reading them.
- Produces page-level witness memos instead of asking one prompt to do
  everything at once.
- Builds `page.packet` workspaces for front-to-back scholarly work.
- Synthesizes several page witnesses into section-level interpretation.
- Renders linked HTML folios and book readers from structured page data.

## Operating Model

1. `Discovery`
   Find high-upside material from public sources with thin metadata or
   under-described content.

2. `Intake`
   Create a library record from a IIIF manifest or a curated source reference.

3. `Prepare`
   Crop away dead page area and focus on the manuscript-bearing region.

4. `Read`
   Produce a witness-first reading of one page.

5. `Packetize`
   Create a scholar-facing bundle for notes, translation, interpretation,
   continuity, and edition work.

6. `Synthesize`
   Read several adjacent pages together so interpretation uses context instead
   of forcing one page to explain the whole manuscript.

7. `Render`
   Build a linked folio or book from structured page output.

## Design Principles

- Witness first. Translation and interpretation come after the page has been
  read.
- Deterministic where possible. Preparation and rendering should not depend on
  a model.
- Small, explicit artifacts. A page packet is better than a giant implicit
  session state.
- Source adapters stay thin. Each source should emit references, not invent a
  new subsystem.
- Public corpora first. The system is meant to run unattended over accessible
  digital collections.

## Quick Start

Install in editable mode:

```bash
python -m pip install --user --editable .
```

Create a local `.env` from [`.env.example`](.env.example):

```env
GEMINI_API_KEY=your-api-key-here
PALIMPSEST_MODEL_TRIAGE=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_VISION=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_READING=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_RECON=gemini-3.1-flash-image-preview
```

Check the CLI surface:

```bash
python -m palimpsest --help
```

## Golden Path

### 1. Discover candidates

List registered sources:

```bash
python -m palimpsest discovery sources list
```

Preview a curated source lane:

```bash
python -m palimpsest discovery sources scrape --source idp --collection chinese_medicine --limit 5
python -m palimpsest discovery sources scrape --source gallica --collection chinese_divination --limit 5
```

Ingest directly into the discovery DB and triage:

```bash
python -m palimpsest discovery sources ingest --source idp --collection chinese_daoism --limit 5 --triage
```

### 2. Intake one document

From a IIIF manifest:

```bash
python -m palimpsest library intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

### 3. Run the library pipeline

```bash
python -m palimpsest library run --doc-id vatican_pal_lat_1267
```

This materializes the main output lanes:

- `exports/transcriptions_full/`
- `exports/canonical_pages/`
- `exports/restoration/`

### 4. Read one page properly

Prepare the page:

```bash
python -m palimpsest page prepare --image library/<doc_id>/images/<page>.jpg
```

Create the page packet:

```bash
python -m palimpsest page packet --image library/<doc_id>/images/<page>.jpg
```

`page packet` creates the packet workspace and, by default, runs the canonical
reconstruction ladder immediately.

`page refresh-packet` reruns that same ladder for an existing packet.

Canonical page pipeline inside `page packet` / `page refresh-packet`:

```text
layout-probe -> region-read -> section-resolution -> validate -> box-cleanup -> assemble -> render-html
```

This is intentionally region-first:
- `layout-probe` finds coarse inclusive boxes
- `region-read` transcribes each box in full
- `section-resolution` assigns canonical text to each box
- `validate` flags structural boundary problems before repair
- `box-cleanup` only touches genuinely implicated neighboring box pairs
- `assemble` builds the page witness object
- `render-html` turns that into the linked folio view

Advanced stage commands are also available when you need to inspect or rerun a
single rung of the ladder:

- `page layout-probe`
- `page region-read`
- `page section-resolution`
- `page validate`
- `page box-cleanup`
- `page assemble`

Read the page witness:

```bash
python -m palimpsest page read --image library/<doc_id>/images/<page>.jpg
```

### 5. Advance the scholarly packet

Ingest the witness into the packet:

```bash
python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task fill_witness \
  --witness library/<doc_id>/experiments/<page>_reading/<page>_reading.md
```

`fill_witness` is the scholar-lane way to populate packet files. The lower-level
`page ingest-reading` command exists for deterministic packet backfill and repair.

Then let the dedicated scholar lane advance notes, translation,
interpretation, and edition work:

```bash
python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task annotate

python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task translate

python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task interpret
```

Render the folio HTML when the packet is ready:

```bash
python -m palimpsest page render-html --packet library/<doc_id>/experiments/<page>_packet/packet.json
```

## Package Layout

The repo is now split by product lane instead of by old implementation era:

- `palimpsest/discovery`
  Source adapters, ingest, triage, and discovery DB work.
- `palimpsest/reconstruct`
  Canonical page reconstruction:
  `layout-probe -> region-read -> section-resolution -> validate -> box-cleanup -> assemble`
- `palimpsest/packets`
  Scholar-facing `page.packet` creation, continuity, and packet advancement.
- `palimpsest/reader`
  Canonical HTML folio and book rendering.
- `palimpsest/commands`
  Thin CLI entrypoints only.

See [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md) for the explicit repo shape.

### 6. Preserve continuity

Generate the forward handoff for the next page:

```bash
python -m palimpsest page handoff \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --next-page-id <next_page_id>
```

Generate a sliding window synthesis across adjacent packets:

```bash
python -m palimpsest page window \
  --packet library/<doc_id>/experiments/<page1>_packet/packet.json \
  --packet library/<doc_id>/experiments/<page2>_packet/packet.json \
  --packet library/<doc_id>/experiments/<page3>_packet/packet.json
```

### 7. Synthesize a section

```bash
python -m palimpsest page synthesize \
  --input library/<doc_id>/experiments/<page1>_reading/<page1>_reading.md \
  --input library/<doc_id>/experiments/<page2>_reading/<page2>_reading.md \
  --input library/<doc_id>/experiments/<page3>_reading/<page3>_reading.md
```

## Source Radar

Palimpsest distinguishes between:

- `automation_fit`: how suitable a source is for unattended pipeline work
- `north_star_fit`: how well it matches the goal of recovering vanished human
  worlds
- `access`: the practical delivery mode

Current public adapters:

- `vatican`
- `idp`
- `gallica`

The intended pattern is simple:

`adapter -> refs -> discovery DB -> intake -> page packets`

## Project Layout

```text
library/
  <doc_id>/
    metadata.json
    page_list.json
    images/
    exports/
      transcriptions_full/
      canonical_pages/
      restoration/

docs/
  VISION.md
  ARCHITECTURE.md
  PAGE_PACKET.md
  SOURCE_ADAPTERS.md
  DISCOVERY_SYSTEM.md

palimpsest/
  commands/
  discovery/
  models/
  prompts/
```

## Model Policy

- `gemini-3.1-flash-lite-preview` for triage, witness reading, and page reconstruction work
- `gemini-3.1-flash-image-preview` for reconstruction / image-generation lanes
- `claude-sonnet-4-5` for the dedicated scholar packet workflow

`*-image-preview` models should not be the default witness-reading lane.

## Current Status

This project is early, active, and opinionated.

The current stable path is:

- discover from curated public sources
- map one page into coarse semantic boxes
- transcribe each box in full
- validate and repair only the implicated boundaries
- assemble one canonical page witness
- move page by page with packet continuity
- synthesize every few pages
- render linked HTML folios/books deterministically

The repo already includes working manuscript dossiers and packet artifacts from
real runs. It is intended to be used as a live research system, not just a
framework skeleton.

## Documentation

- [docs/VISION.md](docs/VISION.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/PRODUCT_FOCUS.md](docs/PRODUCT_FOCUS.md)
- [docs/PAGE_EVIDENCE_SCHEMA.md](docs/PAGE_EVIDENCE_SCHEMA.md)
- [docs/DIPLOMATIC_RESTORATION_CONTRACT.md](docs/DIPLOMATIC_RESTORATION_CONTRACT.md)
- [docs/READING_PROMPTS.md](docs/READING_PROMPTS.md)
- [docs/PAGE_PACKET.md](docs/PAGE_PACKET.md)
- [docs/READER_PRODUCT.md](docs/READER_PRODUCT.md)
- [docs/CONTINUITY_STATE.md](docs/CONTINUITY_STATE.md)
- [docs/SOURCE_ADAPTERS.md](docs/SOURCE_ADAPTERS.md)
- [docs/DISCOVERY_SYSTEM.md](docs/DISCOVERY_SYSTEM.md)
- [docs/MODEL_STRATEGY.md](docs/MODEL_STRATEGY.md)
- [docs/knowledge_recovery_vision.md](docs/knowledge_recovery_vision.md)
- [docs/AGENT_WORKERS.md](docs/AGENT_WORKERS.md)

## References

- [references/README.md](references/README.md)
- [references/github_repos.md](references/github_repos.md)
- [references/github_repos.json](references/github_repos.json)
