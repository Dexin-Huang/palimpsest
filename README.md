# Palimpsest

Palimpsest reads the tracks history forgot.

Palimpsest is a manuscript discovery and reading system for digitized archival
corpora. It is built for a narrow purpose: recover neglected human worlds from
scans that already exist online, but remain thinly cataloged, weakly indexed,
or rarely read end to end.

The project is not organized around OCR as an end in itself. Its operating
model is:

`discovery -> reconstruction -> packets -> reader`

The core product is a repeatable way to turn a page image into:

- a witness-first transcription
- a structured page packet for scholarly work
- a section-level synthesis across neighboring pages
- a linked HTML folio or book

## What It Does

- Discovers candidates from public digital sources such as Vatican IIIF,
  Gallica, and IDP.
- Maintains a discovery database with source metadata, triage scores, and
  source-specific context.
- Reconstructs pages through a canonical region-first pipeline.
- Builds `page.packet` workspaces for notes, translation, interpretation, and
  continuity.
- Renders static HTML folios and manuscript readers from structured page data.

## Canonical Page Pipeline

Palimpsest now uses one reconstruction ladder:

`layout-probe -> region-read -> section-resolution -> validate -> box-cleanup -> assemble`

This is intentionally region-first:

- `layout-probe` proposes coarse semantic boxes
- `region-read` transcribes each box in full
- `section-resolution` assigns one canonical text block to each box
- `validate` flags structural boundary problems
- `box-cleanup` repairs only implicated neighboring pairs
- `assemble` builds the canonical page witness

The reader then renders the assembled page as a linked folio HTML view.

## Design Principles

- Witness first. Translation and interpretation come after the page has been
  read.
- Deterministic where possible. Preparation, assembly, and rendering should not
  depend on a model unless necessary.
- Small explicit artifacts. A page packet is better than a giant implicit
  session state.
- Thin source adapters. Each source should emit references, not become its own
  subsystem.
- Public corpora first. The system is meant to run unattended over accessible
  digital collections.

## Install

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

Check the CLI:

```bash
python -m palimpsest --help
```

## Quick Start

### 1. Discover candidates

List registered public sources:

```bash
python -m palimpsest discovery sources list
```

Preview a curated source lane:

```bash
python -m palimpsest discovery sources scrape --source idp --collection chinese_medicine --limit 5
python -m palimpsest discovery sources scrape --source gallica --collection chinese_divination --limit 5
```

Ingest directly into the discovery database and triage:

```bash
python -m palimpsest discovery sources ingest --source idp --collection chinese_daoism --limit 5 --triage
```

### 2. Intake a document

From a IIIF manifest:

```bash
python -m palimpsest library intake ^
  --doc-id vatican_pal_lat_1267 ^
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

### 3. Create a page packet

```bash
python -m palimpsest page packet --image library/<doc_id>/images/<page>.jpg
```

`page packet` creates the packet workspace and runs the canonical reconstruction
pipeline immediately. To rerun that same ladder later:

```bash
python -m palimpsest page refresh-packet --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json
```

### 4. Read and advance the packet

Read a page witness directly:

```bash
python -m palimpsest page read --image library/<doc_id>/images/<page>.jpg
```

Fill the packet witness from that reading:

```bash
python -m palimpsest scholar packet ^
  --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json ^
  --task fill_witness ^
  --witness library/<doc_id>/experiments/<page>_reading/<page>_reading.md
```

Then advance notes, translation, and interpretation:

```bash
python -m palimpsest scholar packet --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json --task annotate
python -m palimpsest scholar packet --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json --task translate
python -m palimpsest scholar packet --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json --task interpret
```

### 5. Render a folio or a book

Render a single folio:

```bash
python -m palimpsest page render-html --packet library/<doc_id>/experiments/<page>_packet_v1/packet.json
```

Build a manuscript reader:

```bash
python -m palimpsest book site --packets-dir library/<doc_id>/experiments --output-dir library/<doc_id>/site_build
```

### 6. Synthesize a section

```bash
python -m palimpsest page synthesize ^
  --input library/<doc_id>/experiments/<page1>_reading/<page1>_reading.md ^
  --input library/<doc_id>/experiments/<page2>_reading/<page2>_reading.md ^
  --input library/<doc_id>/experiments/<page3>_reading/<page3>_reading.md
```

## Package Layout

The repo is split by product lane:

- `palimpsest/discovery`
  Source adapters, ingest, triage, and discovery DB work.
- `palimpsest/reconstruct`
  Canonical page reconstruction.
- `palimpsest/packets`
  `page.packet` creation, continuity, and scholar workflow.
- `palimpsest/reader`
  Canonical HTML folio and book rendering.
- `palimpsest/commands`
  Thin CLI entrypoints.

See [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md) for the explicit repo shape.

## Source Adapters

Current public adapters:

- `vatican`
- `idp`
- `gallica`

The intended source flow is:

`adapter -> refs -> discovery DB -> intake -> packets -> reader`

## Model Defaults

- `gemini-3.1-flash-lite-preview`
  - triage
  - reconstruction
  - reading
- `gemini-3.1-flash-image-preview`
  - image-generation / reconstruction-image lane
- `claude-sonnet-4-5`
  - packet scholar workflow

`*-image-preview` models are not the default witness-reading lane.

## Current Status

Palimpsest is early, active, and opinionated.

The stable path today is:

- discover from curated public sources
- reconstruct one page through the canonical region-first ladder
- build a page packet
- advance translation and interpretation with explicit continuity
- render a linked folio or manuscript reader

The repository includes real packet artifacts, dossiers, and manuscript reads
from live runs. It is a working research system, not just a framework stub.

## Documentation

Core docs:

- [docs/VISION.md](docs/VISION.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md)
- [docs/PAGE_PACKET.md](docs/PAGE_PACKET.md)
- [docs/READER_PRODUCT.md](docs/READER_PRODUCT.md)
- [docs/SOURCE_ADAPTERS.md](docs/SOURCE_ADAPTERS.md)
- [docs/DISCOVERY_SYSTEM.md](docs/DISCOVERY_SYSTEM.md)
- [docs/READING_PROMPTS.md](docs/READING_PROMPTS.md)
- [docs/MODEL_STRATEGY.md](docs/MODEL_STRATEGY.md)

Further project docs remain under [docs/](docs/).

## References

- [references/README.md](references/README.md)
- [references/github_repos.md](references/github_repos.md)
- [references/github_repos.json](references/github_repos.json)
