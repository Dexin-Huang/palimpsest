# Repo Split Execution Plan

Purpose: turn the current Palimpsest monorepo into smaller product-aligned repos without breaking the working manuscript pipeline.

Operating sequence:

`boundary cleanup -> stable contracts -> pure downstream render -> thin CLIs -> repo extraction`

## Target End State

### Phase 1 end state: three repos

1. `palimpsest-discovery`
   - owns `palimpsest/discovery`
   - absorbs the intake-facing part of `palimpsest/library`
   - keeps discovery CLI entrypoints

2. `palimpsest-reconstruct`
   - owns `palimpsest/reconstruct`
   - keeps the page reconstruction CLI surface
   - produces stable reconstruction artifacts for downstream consumers

3. `palimpsest-scholar`
   - owns `palimpsest/packets`
   - owns `palimpsest/reader`
   - keeps packet and reader entrypoints together until render and packet state are fully detached

### Phase 2 end state: optional fourth repo

4. `palimpsest-reader`
   - split out of `palimpsest-scholar` only after packet loading, render-state updates, and render-core primitives are detached from packet workflow logic

## Architectural Rules

1. Package modules own workflow logic.
2. CLI modules own argument parsing, printing, and exit behavior only.
3. Reader is a pure downstream consumer of packet and assembly artifacts.
4. On-disk artifacts are contracts and must be treated as versioned interfaces.
5. Shared core stays intentionally small; broad convenience imports are not architecture.
6. Legacy import and transcription paths should be deleted from the active branch once the canonical path is detached.

## Non-Goals

- Do not extract `palimpsest/commands` as its own repo.
- Do not reintroduce the old `library + transcription + book/canonical/restoration` lane into the active product path.
- Do not split by directory count alone.
- Do not keep adding new workflow logic to broad aggregate modules like `palimpsest.models`.

## Current Boundary Problems

### Discovery

- `palimpsest/commands/discovery.py` still carries fetch, merge, and orchestration logic that belongs in discovery-owned modules.
- Discovery still leaks operational details through command-layer helpers instead of a narrow package API.

### Reconstruct and Page CLI

- `palimpsest/commands/page.py` still owns page-selection, layout execution, packet decode orchestration, and doc-level decode flow.
- Downstream packet logic still assumes reconstruct artifact filenames and workspace layout implicitly instead of through an explicit contract module.

### Packets and Reader

- Reader render remains too close to packet workflow semantics.
- Packet loading, repair, continuity, and render artifact syncing are cleaner than before, but the remaining render-side contract is still not fully explicit.
- Packet, continuity, folio, and assembly schemas are still broader than the future repo boundaries.

### Legacy and Support

- The legacy transcription and conversion lane has been removed from the active branch.
- The remaining risk is stale assumptions in docs or contracts, not runtime imports.
- The `library/<doc_id>` filesystem contract still needs to stay explicit and small.

## Contract Surfaces To Freeze

These are the interfaces that matter more than module layout.

### Library workspace contract

Directory: `library/<doc_id>`

Required files and folders:
- `metadata.json`
- `page_list.json`
- `images/`
- `experiments/<page_id>_packet_v1/`

Contract decisions to codify:
- source metadata shape
- page ordering and page id semantics
- image path conventions
- experiment directory naming

### Reconstruction artifact contract

Owner: reconstruct

Required artifacts:
- `layout_probe.json`
- `region_reads.json`
- `section_resolution.json`
- `box_cleanup.json`
- `page_assembly.json`
- validation metadata

Contract decisions to codify:
- stable filenames
- artifact directory layout
- required versus optional stages
- schema ownership for layout, region, and assembly models

### Packet contract

Owner: packets

Required artifacts:
- `packet.json`
- continuity handoff and window references
- workflow status and next-action semantics
- render artifact references

Contract decisions to codify:
- which fields are authoritative versus repairable
- packet file status semantics
- render output references
- allowed mutation points

### Reader input contract

Owner: reader

Required inputs:
- packet data
- page assembly
- optional witness, translation, notes, terms, and questions markdown

Contract decisions to codify:
- which missing files are acceptable
- assembly assumptions
- navigation metadata expectations
- render meta schema

## Workstreams

### Workstream A: freeze contracts

Deliverables:
- one contract doc for library workspace
- one contract doc for reconstruct artifacts
- one contract doc for packet state
- one contract doc for reader inputs and render outputs

Current contract docs:
- `docs/contracts/LIBRARY_WORKSPACE.md`
- `docs/contracts/RECONSTRUCT_ARTIFACTS.md`
- `docs/contracts/PACKET_CONTRACT.md`
- `docs/contracts/READER_RENDER_CONTRACT.md`

Implementation direction:
- centralize filename and path conventions in package-owned modules
- stop scattering artifact names as ad hoc string literals
- keep typed models narrow and package-owned

Exit criteria:
- reconstruct, packets, and reader all load artifacts through explicit contract helpers or typed models
- new code can discover artifact locations without command-layer knowledge

### Workstream B: make reader pure

Goal:
- reader renders HTML and render metadata without mutating packet state

Implementation direction:
- move packet render artifact syncing into packet or page workflow callers
- keep `palimpsest/reader/folio.py` focused on loading inputs and rendering outputs
- treat site builds and workspace renders as different callers over the same pure render function

Exit criteria:
- reader modules do not write `packet.json`
- packet render-state updates happen only in packet/page workflow code

### Workstream C: thin the CLI layer

Goal:
- command modules become thin adapters over package-owned services

Priority order:
1. `palimpsest/commands/page.py`
2. `palimpsest/commands/discovery.py`
3. `palimpsest/commands/book.py`
4. `palimpsest/commands/library.py`

Implementation direction:
- move decode, selection, refresh, and fetch orchestration into package modules
- leave argparse setup and human-readable printing in command files

Exit criteria:
- command files are mostly argument parsing plus result reporting
- workflow retries, path selection, and execution sequencing live outside `palimpsest/commands`

### Workstream D: delete legacy paths

Scope:
- old transcription package
- old conversion/export modules
- old transcription CLI surface
- stale docs and prompt assets tied only to that lane

Implementation direction:
- detach the canonical path first
- then delete the legacy lane from the active branch
- rely on git history for recovery instead of keeping dead local code

Exit criteria:
- legacy code is absent from the active branch
- no canonical runtime module imports deleted legacy code

Current status:
- the old transcription/conversion lane has been removed from the active branch
- `book` and `library` now expose only canonical commands

### Workstream E: shrink shared core

Goal:
- keep only genuinely shared contracts and low-level helpers in shared modules

Allowed shared examples:
- model IO helper functions
- narrow typed contracts
- tiny filesystem/path helpers if they are truly cross-lane

Not allowed:
- broad umbrella re-export layers as a substitute for ownership
- CLI orchestration hidden in shared packages
- reader or packet workflow logic leaking into a generic core

Exit criteria:
- each shared module has a narrow, defensible reason to exist

### Workstream F: extract repos

Extraction order:
1. discovery
2. reconstruct
3. packets + reader
4. optional later split of reader

Repo extraction gates:
- stable contracts documented
- package-owned workflows in place
- CLI surfaces already thin
- no reverse imports through the command layer

## Immediate Execution Wave

This is the next practical cleanup wave to run in parallel.

### Lane 1: reader purity

Scope:
- `palimpsest/reader/folio.py`
- packet/page workflow callers that currently depend on render-time packet mutation

Tasks:
- remove packet-state writes from reader render
- move packet render-output syncing into callers
- keep site builds on the pure render path

### Lane 2: discovery orchestration extraction

Scope:
- `palimpsest/commands/discovery.py`
- new or existing modules under `palimpsest/discovery/`

Tasks:
- move fetch, merge, and workflow helpers into discovery-owned modules
- keep CLI subcommands stable
- reduce command-layer ownership of discovery runtime logic

### Lane 3: page orchestration extraction

Scope:
- `palimpsest/commands/page.py`
- new workflow module under `palimpsest/reconstruct/` or `palimpsest/packets/`

Tasks:
- move page selection, decode, and layout execution helpers out of the CLI
- keep packet decode and refresh behavior stable
- make the package API the owner of page decode flow

## Milestones

### Milestone 1: monorepo boundary cleanup

Required:
- no reverse imports from package code into CLI modules
- neutral helpers for shared model and packet-state work
- narrower model imports on the main boundary files

Status:
- in progress

### Milestone 2: explicit contracts

Required:
- contract docs exist
- core artifact filenames and directories are codified
- contract helpers exist where stringly path logic was previously implicit

Status:
- active

### Milestone 3: pure reader and thin CLIs

Required:
- reader is downstream-only
- page and discovery commands are thin
- book and library command paths expose only the canonical surface

Status:
- active

### Milestone 4: extraction-ready monorepo

Required:
- package ownership is clear
- shared core is small
- legacy path is deleted from the active branch
- repo extraction order is low-risk

Status:
- pending

## Completion Criteria For This Execution Series

- reader render path is pure
- page and discovery commands no longer own workflow orchestration
- artifact contracts are written down and reflected in code structure
- legacy modules are removed from the active branch
- the monorepo can be split into three repos without inventing new boundaries during extraction
