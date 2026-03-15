# Architectural Beautification Plan

Purpose: finish the high-leverage architectural cleanup so the repo is not just split-ready, but internally elegant.

Current priority debt:

1. `palimpsest/discovery/database.py` is still too large even after the compat split.
2. `palimpsest/packets/workflow.py` still mixes packet-core logic with doc/batch orchestration.
3. `palimpsest/web/folio_fragments.py` still carries too much presentation weight in one file.

## Progress So Far

Already landed:
- packet load/read paths are now explicit about mutation instead of repairing packet state on read
- `palimpsest/discovery/access.py` establishes a canonical access surface for active discovery flows
- `palimpsest/discovery/compat.py` owns the compatibility-only scholarship / our-work / JSONL bridge
- region-read ownership moved out of `reconstruct/pipeline.py` into `reconstruct/region_reads.py`
- probe layout helpers moved out of `reconstruct/pipeline.py` into `reconstruct/probe_layout.py`
- shared reconstruct response/time helpers moved into `reconstruct/common.py`
- `palimpsest/reconstruct/resolve_support.py` gives `resolve.py` one clear support boundary
- folio shell/page HTML moved out of `reader/folio.py` into `web/folio_page.py`
- book cover/contents/ending composition moved out of `reader/site.py` into `web/site_pages.py`
- packet workspace rendering moved out of `palimpsest.packets.workflow` and behind `palimpsest.reader.packet`
- CLI imports now point at owning modules instead of broad `packets` / `reconstruct` barrels where safe

Current third-wave focus:
- split canonical discovery DB code into smaller owners without schema migration
- split packet decode/doc selection/batch helpers out of `palimpsest/packets/workflow.py`
- turn `palimpsest/web/folio_fragments.py` into smaller render-owned modules
- finish with real end-to-end smoke paths instead of only compile/import checks

Third-wave progress:
- `palimpsest/discovery/records.py` now owns canonical discovery record types and shaping helpers
- `palimpsest/discovery/stats.py` now owns discovery stats/reporting queries
- `palimpsest/packets/doc_pages.py` now owns document page-list helpers
- `palimpsest/packets/decode.py` now owns packet-local decode orchestration
- `palimpsest/web/structured_faces.py` now owns structured witness / interpretation face rendering helpers

## North Star

The code should read like the product story:

`discovery -> reconstruct -> packets -> reader`

That means:
- each lane owns one coherent responsibility
- files are named after stage ownership, not implementation accidents
- mutation points are explicit
- render code lives with render code
- database shape reflects the current product, not old ambitions

## Workstream A: Reconstruct Modularization

Owner:
- dedicated reconstruct worker

Goal:
- turn `pipeline.py` into a thin composition module over small stage-owned internals

Problems to remove:
- probe, region-read, cleanup, validation, and assembly internals living in one file
- private underscore imports across modules
- mixed concerns: geometry, prompt IO, crop IO, model response cleanup, and artifact writes

Target module shape:
- `reconstruct/probe_stage.py`
- `reconstruct/region_reads.py`
- `reconstruct/assembly.py`
- `reconstruct/validation.py`
- `reconstruct/cleanup.py`
- `reconstruct/io.py` or similarly narrow artifact IO helper module

Execution phases:
1. Extract pure artifact/path and image helper functions that do not encode stage decisions.
2. Move region-read load/write/run logic behind a dedicated module.
3. Move assembly and validation flows behind dedicated modules.
4. Reduce `pipeline.py` to orchestration and compatibility exports only.

Acceptance:
- no cross-module underscore imports into `pipeline.py`
- each stage has one obvious owner
- probe outputs and region-read outputs use contract helpers only
- `pipeline.py` is a coordinator, not a dumping ground

## Workstream B: Discovery Database Contraction

Owner:
- dedicated discovery worker

Goal:
- reduce discovery state to discovery-owned concepts and quarantine the rest

Problems to remove:
- manuscript scoring/status fields tied to older product stories
- scholarship and our-work tables living inside discovery core
- package imports that route back through `palimpsest.discovery`
- docs and code still implying multiple discovery stories

Target database shape:
- `manuscripts`
- `opportunities`
- `enrichment`
- `audit`

Quarantine candidates:
- `scholarship`
- `our_work`
- legacy scoring/status fields that are not required by the current DB-first discovery flow

Execution phases:
1. Define the minimal discovery-owned schema and access layer.
2. Mark quarantine tables and fields explicitly in code and docs.
3. Move non-discovery concepts behind a legacy or migration boundary.
4. Trim workflow and triage to import directly from discovery-owned modules.

Acceptance:
- `database.py` clearly separates canonical schema from legacy carryover
- new discovery code does not depend on scholarship or our-work tables
- package-root imports are not the default internal path
- docs describe one ingest-and-triage story

Current next cut:
1. Keep `compat.py` as the owner of compatibility CRUD and JSONL bridge code.
2. Split canonical DB concerns into smaller modules such as records/schema vs canonical CRUD vs stats/reporting.
3. Leave `access.py` as the public discovery boundary.

Acceptance for this wave:
- `database.py` stops being the only owner of all canonical DB behavior
- compatibility code stays out of canonical modules
- discovery internals read through `access.py` or direct owning modules, not package-root barrels
- canonical record types no longer live in `database.py`
- stats/reporting SQL no longer lives in `database.py`

## Workstream C: Packets Workflow Decomposition

Owner:
- dedicated packets worker

Goal:
- separate packet-core decode logic from doc/page selection and batch orchestration

Problems to remove:
- `workflow.py` mixes packet decode, page-list selection, retry orchestration, and doc-wide batch helpers
- packet-core functions still sit beside CLI-adjacent utility logic

Target boundary:
- packet decode + retry logic in a decode-owned module
- doc/page selection helpers in a batch/doc module
- `workflow.py` becomes a small facade or disappears

Execution phases:
1. Pull doc/page selection helpers into a dedicated module.
2. Move `run_packet_decode` and its packet-local helpers into a decode-owned module.
3. Leave compatibility exports in `workflow.py` only if callers still need them.

Acceptance:
- `workflow.py` no longer reads like a mixed utility file
- packet decode has one obvious owner
- doc/batch helpers have one obvious owner
- `commands/page.py` does not need broad packets barrel imports

Current landed slice:
- `load_doc_pages`, `select_doc_pages`, and `packet_dir_for_page` moved into `packets/doc_pages.py`
- `run_layout_pipeline` and `run_packet_decode` now live in `packets/decode.py`, with `workflow.py` reduced to a compatibility facade

## Workstream D: Reader/Web Separation

Owner:
- dedicated reader/web worker

Goal:
- make `reader` the packet-to-render adapter and `web` the render/layout home

Problems to remove:
- HTML shell, JS behavior, and navigation markup embedded in `reader/folio.py`
- site page composition split between `reader/site.py` and `web`
- inline style debt in fragment builders

Target boundary:
- `reader` loads packet + assembly inputs and prepares a render model
- `web` owns HTML shell assembly, spread/page markup, site page builders, and presentation JS

Execution phases:
1. Extract HTML shell and folio page assembly out of `reader/folio.py` into `web`.
2. Extract title/contents/ending page builders out of `reader/site.py` into `web`.
3. Replace inline presentation fragments with named render helpers.
4. Leave `reader` as a thin adapter that calls web-owned render functions.

Acceptance:
- `reader/folio.py` no longer contains the document shell and page JS blob
- `reader/site.py` stops hand-building site pages
- `web` is the clear owner of render composition
- the render contract stays unchanged for callers

Current next cut:
1. Split `folio_fragments.py` by render concern instead of keeping witness, interpretation, and spread composition in one file.
2. Keep `folio_page.py` and `site_pages.py` as the shell/page owners.
3. Leave `reader` as the adapter only.

Acceptance for this wave:
- `folio_fragments.py` shrinks materially
- web-owned render files read like intentional components, not one long string-builder module
- no presentation ownership moves back into `reader`

Current landed slice:
- `render_lacuna`, `render_translation_inline`, `render_structured_witness_face`, and `render_structured_interpretation_face` moved into `web/structured_faces.py`

## Execution Order

Recommended order:

1. Discovery canonical DB breakup
2. Packets workflow decomposition
3. Web fragment polish
4. Real end-to-end smoke verification

Reason:
- discovery DB and packets workflow are the last large ownership problems
- web polish should happen after the lane boundaries are cleaner
- end-to-end verification is more useful once the structural cuts have landed

## Current Agent Assignments

### Lane 1: Discovery

Scope:
- `palimpsest/discovery/**`
- discovery docs if needed

Deliverable:
- a concrete canonical DB breakup
- if safe, the first low-risk extraction slice out of `database.py`
- current wave: keep compatibility in `compat.py` and split canonical owners more clearly

### Lane 2: Packets

Scope:
- `palimpsest/packets/**`
- `palimpsest/commands/page.py` only if needed for the new owning module boundary

Deliverable:
- a workflow split that separates packet decode from doc/batch helpers
- if safe, the first low-risk extraction slice out of `workflow.py`
- current wave: establish obvious owners for decode vs selection/batch helpers

### Lane 3: Web

Scope:
- `palimpsest/web/**`
- reader/render docs

Deliverable:
- a concrete fragment split plan
- if safe, extraction of one render concern out of `folio_fragments.py`
- current wave: make web render modules read like deliberate components

### Lane 4: Validation

Scope:
- read-only by default
- smoke scripts / verification notes if needed

Deliverable:
- end-to-end verification plan and residual-risk notes

## Verification Standard

Every wave should pass:
- `python -m py_compile` on touched Python files
- import smoke for touched entrypoints
- targeted grep checks for the bad pattern being removed

At the end of the full beautification program, run:
- one end-to-end packet decode
- one folio render
- one book/site build
- one discovery ingest or triage smoke path

Suggested smoke commands from repo root:

```powershell
python -m palimpsest discovery triage --db discovery/manuscripts.db --limit 1 --dry-run

python -m palimpsest page decode-doc `
  --doc-dir library/vatican_borg_cin_361 `
  --start-page f004r `
  --end-page f004r `
  --fail-fast `
  --title "Vatican Borg. cin. 361"

python -m palimpsest page render-html `
  --packet library/vatican_borg_cin_361/experiments/f004r_packet_v1/packet.json `
  --out-dir .tmp/f004r_render_smoke `
  --title "Vatican Borg. cin. 361"

python -m palimpsest book site `
  --packet library/vatican_borg_cin_361/experiments/f001r_packet_v1/packet.json `
  --packet library/vatican_borg_cin_361/experiments/f002r_packet_v1/packet.json `
  --packet library/vatican_borg_cin_361/experiments/f003r_packet_v1/packet.json `
  --output-dir .tmp/borg361_site_smoke `
  --title "Vatican Borg. cin. 361"
```

Current smoke status:
- `python -m palimpsest discovery stats --db discovery/manuscripts.db` passed
- `python -m palimpsest page render-html ... --out-dir .tmp/f004r_render_smoke` passed
- `python -m palimpsest book site ... --output-dir .tmp/borg361_site_smoke` passed
