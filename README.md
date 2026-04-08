# Palimpsest

Palimpsest reads the tracks history forgot.

Palimpsest is a dual-mode prospecting and ingestion engine for neglected textual
archives. It feeds [Ariadne](../meridian/ariadne/), a Mundaneum-class knowledge
compiler that assembles source-grounded semantic structure out of primary
evidence. Palimpsest exists to do the work Ariadne's default Cartographer
cannot: find labor-killed dreams in published prefaces, and turn image-bound
manuscripts into clean, anchored text.

The operating thesis: the ideas most worth recovering are the ones that were
right but early — specifically, the ones whose only bottleneck was menial
cognitive labor (cataloguing, indexing, transcribing, cross-referencing).
AI's unique civilizational contribution is unlimited cataloguing labor at
trustworthy provenance. Palimpsest hunts for projects that died waiting for
exactly that.

## Status

Palimpsest is currently mid-restructure from a manuscript publishing pipeline
into its dual-mode form. The source-mode heavy path works end-to-end; the
opportunity-mode prospecting path is being rebuilt. The gate sequencing and
the live state of the restructuring are tracked in
[`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md).

Live top-level CLI surface (see `palimpsest/cli.py`):

- `palimpsest discovery ...` — discovery + triage over curated public sources
- `palimpsest library ...` — canonical library intake, download, clean, status
- `palimpsest transcribe ...` — VLM transcription, survey, enrich

The retired `palimpsest book` / publishing stack has been archived; see
[§5 Repo layout](#repo-layout) for what moved where.

## Quick start

Install in editable mode:

```bash
python -m pip install --user --editable .
```

Create a local `.env` from [`.env.example`](.env.example). The relevant keys:

```env
GEMINI_API_KEY=your-api-key-here
PALIMPSEST_MODEL_TRIAGE=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_VISION=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_READING=gemini-3.1-flash-lite-preview
PALIMPSEST_MODEL_RECON=gemini-3.1-flash-image-preview
```

Use `*-image-preview` models only for image-generation / reconstruction lanes,
never for the main transcription lane.

Smoke-test the CLI:

```bash
python -m palimpsest --help
```

### A full source-mode run

One document, end-to-end, from IIIF manifest to enriched JSONL. These are the
commands verified in [`CLAUDE.md`](CLAUDE.md) against the current package.

Create a canonical library record from a IIIF manifest:

```bash
python -m palimpsest library intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

Download page images:

```bash
python -m palimpsest library download --doc-id vatican_pal_lat_1267
```

Transcribe the images to JSONL:

```bash
python -m palimpsest transcribe run \
  --image-dir library/vatican_pal_lat_1267/images \
  --prompt-name transcription_json \
  --workers 10 \
  --skip-existing
```

Build a translation brief (glossary, outline, terms):

```bash
python -m palimpsest transcribe survey \
  --input library/vatican_pal_lat_1267/transcription/transcriptions.jsonl
```

Enrich with glossary + overlap context + boundary repair:

```bash
python -m palimpsest transcribe enrich \
  --input library/vatican_pal_lat_1267/transcription/transcriptions.jsonl
```

The terminal artifact today is `enriched.jsonl`. The phase-B `assemble` stage
(see [§6 Current state + what's next](#current-state--whats-next)) will turn
that into the Ariadne handoff bundle.

## Architecture overview

Palimpsest is dual-mode by design:

- **Source mode** is the expensive pipeline:
  `discover → intake → download → transcribe → survey → enrich → (future) assemble`.
  It runs on manuscripts, scans, and marginalia that Ariadne cannot consume
  directly. The eventual output per document is a three-file bundle:
  `<doc_id>.md`, `<doc_id>.manifest.json`, `<doc_id>.anchors.json`, sitting
  beside the source markdown so Ariadne's honeycomb can pick it up as a
  sidecar without re-transcribing.

- **Opportunity mode** is the cheap broad scan. It hunts clean published
  material — critical-edition prefaces, editorial introductions, colophons,
  marginalia, oral histories — for labor-killed dreams, and emits structured
  `DreamCandidate` records into a rolling portfolio shortlist. The opportunity
  schema and Gallica prefaces adapter are under active construction;
  see the addendum linked in [Status](#status) for current gate state.

For the full strategic framing, see
[`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) and
[`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md).

## Recovery pointer

If you are reading this repo for the first time and the code looks like it is
halfway between two worlds, it is. Palimpsest just finished a 16-commit
restructuring from a manuscript publishing pipeline (with an HTML reader, page
packets, and a folio renderer as its terminal stage) into a dual-mode ingestion
engine that feeds Ariadne. The old publishing stack has been archived to
`archives/2026-04-07_publishing_stack/`. The new shape, the sequencing gates,
and everything Codex got right and wrong during the restructuring analysis are
captured in
[`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md)
and its
[ADDENDUM](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md). Read the
addendum first if you want the corrected sequencing.

## Repo layout

```
palimpsest/        # core Python package
  cli.py           # top-level CLI entrypoint
  commands/        # subparser wiring: discovery, library, transcribe
  discovery/       # source adapters, triage, discovery DB
  library/         # IIIF intake, download, image cleaning
  transcribe.py    # VLM transcription engine
  survey.py        # translation brief builder
  enrich.py        # batch translation with glossary + overlap + repair
  prompts/         # external prompt files
library/           # canonical outputs for each document
  <doc_id>/
    metadata.json
    page_list.json
    images/
    transcription/
discovery/         # registries, manifest cache, crawl artifacts
docs/              # system documentation
archives/          # retired stacks (publishing stack lives here)
scripts/           # thin CLI wrappers
```

Every document lives under `library/<doc_id>/` with stable metadata, a
page list, downloaded images, and per-page JSONL outputs. Canonical JSON
is the source of truth; anything derived from it belongs outside the
library root.

## Current state + what's next

Working today:

- Source-mode heavy path from IIIF manifest through `enriched.jsonl`.
- Discovery DB with Vatican, IDP, and Gallica adapters.
- Manuscript-shaped triage via `discovery/triage.py`.

In progress:

- **Phase A — Honeycomb sidecar bypass** (in the Ariadne repo). Teaches
  `ariadne/honeycomb.py` to recognize `<stem>.manifest.json` beside
  `<stem>.md` and consume the pre-built manifest instead of re-transcribing.
- **Phase D — Discovery schema reset**. The current `Opportunity` record is
  manuscript-shaped; opportunity mode needs a corpus-agnostic `Prospect`.
- **Phase C — Gallica prospecting adapter.** First opportunity-mode corpus:
  Collection Budé → Classiques Garnier → Pléiade.
- **Phase B — Source-mode `assemble` stage.** The missing terminal step that
  emits the `<doc_id>.md` + `<doc_id>.manifest.json` + `<doc_id>.anchors.json`
  bundle Ariadne expects.
- **Phase E — Vestigial layer cleanup.** Lands after the new seams exist.

For the reasoning behind this ordering (and the reversal from Codex's
original sequence), see
[`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md).

## Further reading

- [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md) — core principles and repo layout rules
- [`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md) — full strategic analysis
- [`docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md`](docs/CODEX_RESTRUCTURING_ANALYSIS_2026-04-07_ADDENDUM.md) — verification pass + corrected sequencing
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/MODEL_STRATEGY.md`](docs/MODEL_STRATEGY.md)
- [`docs/READING_PROMPTS.md`](docs/READING_PROMPTS.md)
- [`CLAUDE.md`](CLAUDE.md) — canonical command reference for coding agents
