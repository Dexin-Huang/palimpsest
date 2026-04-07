# Codex Restructuring Analysis — 2026-04-07 — ADDENDUM: Three-Agent Verification + Sequencing Reversal

**Purpose:** Records a three-agent independent verification pass on `CODEX_RESTRUCTURING_ANALYSIS_2026-04-07.md` and Codex's own reversal of its original sequencing in response. The main analysis doc is preserved unchanged; this addendum is the correction layer.

**Method:**
- Three Explore agents dispatched in parallel to independently verify Codex's grounded claims, sketch the honeycomb sidecar change concretely, and stress-test the corpus recommendations.
- Second Codex session (resumed from session ID `019d68ba-c193-7a60-8a5e-f7a6cc1ab318`) presented with the verification findings and asked to re-rank the concrete work items.

**Headline outcome:** Codex owned the reversal without hedging. The practical program becomes **A → D → C → B → E** (see §3 for decode).

---

## 1. Verification results — confirmations (high trust)

Codex's structural claims held up line-by-line:

- Load-bearing path: `library intake → library download → transcribe run → survey → enrich`. No `assemble` stage exists in code.
- HTML publishing is the real terminal stage via `cmd_publish` at `commands/transcribe.py:135` → `build_book_site()` in `publish.py`.
- Manuscript-only discovery schema blocker confirmed at `discovery/records.py:34`.
- Vestigial layer is cleanly isolated — zero hidden imports from load-bearing code into `publish.py`, `reader/*`, or `web/*`.
- **Ariadne's `ManifestNode` schema is stable and emission-ready.** `manifest.py:152-244` matches `system-design-v0.2.md §B` line-for-line. No drift. Palimpsest can emit manifests with no schema gaps.
- Domain orchestrator sidecar bypass exists at `meridian/ariadne/ariadne/orchestrator.py:151` (`find_manifest_for_source`) and `:174-242` (`_phase_create_manifests`, explicit gate at line 198).
- **Honeycomb has ZERO sidecar awareness.** `meridian/ariadne/ariadne/honeycomb.py:26-29` `SUPPORTED_EXTENSIONS` treats `.manifest.json` as ordinary data. Zero mentions of sidecar logic in honeycomb.py.
- Gallica as first opportunity-mode corpus is feasible. SRU + IIIF APIs work, non-commercial reuse free with BnF attribution. Target sub-collections (in order): Collection Budé, Classiques Garnier, Pléiade sample.

## 2. Corrections to the original analysis

### 2.1 Domain bypass is 95 %, not complete

Codex described the domain-mode sidecar bypass as complete. Actually: it skips Cartographer creation but still runs manifest embedding (`orchestrator.py:220-227`) and billboard indexing (`:230`). Negligible cost but technically not zero intrusion.

### 2.2 The deletion list roughly doubles

Codex named `commands/book.py`, `publish.py`, `reader/witness.py`, `web/site_pages.py`, `web/folio_page.py`. Verification found these additional dead files, all only imported by the dead web/reader layer (zero load-bearing imports):

**Dead model files:**
- `palimpsest/models/folio_render.py`
- `palimpsest/models/packet.py`
- `palimpsest/models/continuity.py`
- `palimpsest/models/page.py`
- `palimpsest/models/zone.py`

**Dead web utilities:**
- `palimpsest/web/folio_fragments.py`
- `palimpsest/web/structured_faces.py`
- `palimpsest/web/markup.py`
- `palimpsest/web/common.py`
- `palimpsest/web/__init__.py`

**Dead reader support:**
- `palimpsest/reader/__init__.py`
- `palimpsest/reader/common.py`

### 2.3 `contracts.py` has a clean extractable split

**Live constants** to move to a new `palimpsest/library_contracts.py`:
- `METADATA_FILENAME`, `PAGE_LIST_FILENAME`, `IMAGES_DIRNAME`, `CLEANED_IMAGES_DIRNAME`, `REGISTRY_FILENAME`, `EXPERIMENTS_DIRNAME`, `RUNS_DIRNAME`
- Helper functions: `metadata_path()`, `page_list_path()`, `images_dir()`, and peers

**Dead constants** to archive with the rest of the publishing stack:
- `PACKET_FILENAME`, `WITNESS_FILENAME`, `TRANSLATION_FILENAME`, `INTERPRETATION_FILENAME`, `TERMS_FILENAME`, `QUESTIONS_FILENAME`, `EDITION_HTML_FILENAME`, `FOLIO_RENDER_FILENAME`, `RENDER_META_FILENAME`
- ~50 `layout_probe` / `section_synthesis` / `box_cleanup` / `page_validation` / `page_assembly` / `visual_pair_repairs` helpers at lines 14-283

### 2.4 Line-number drift

Codex's structural analysis is sound but numerical citations need re-verification before use:

- "recover tracks of a vanished human world" prompt is at `prompts/opportunity_triage.txt:50`, not `:41`
- `triage_batch()` is at `discovery/triage.py:465`, not `:623`
- The `:745` runner Codex cited in `triage.py` does not exist (file is 910 lines total)
- `build_parser()` is at `cli.py:13` but the register calls are at 16-19

### 2.5 Stale docs Codex didn't flag

All artifacts of the dead publishing era. Future sessions reading them will regress to the old framing, so they should be archived alongside the dead code:

- `docs/READER_PRODUCT.md`
- `docs/FOLIO_RENDER_CONTRACT.md`
- `docs/PAGE_EVIDENCE_SCHEMA.md`
- `docs/PAGE_PACKET.md`
- `docs/DIPLOMATIC_RESTORATION_CONTRACT.md`
- `docs/ARCHITECTURAL_BEAUTIFICATION_PLAN.md`
- Likely `docs/TRANSCRIPTION_ARCHITECTURE.md`

## 3. The sequencing reversal

### 3.1 Original Codex ordering (from §5 of the main analysis)

1. Settle source-mode artifact contract
2. Redesign discovery data structures
3. Reframe `triage.py`
4. Replace `scout.py`
5. Gallica adapter
6. Ariadne honeycomb sidecar support (parallel / long-term)
7. Deletion last

### 3.2 Verification agent 2 pushback

- Ariadne's domain mode lives in `meridian/ariadne/ariadne/legacy/domain.py`. No active development. **Domain mode is on life support.**
- Honeycomb is the production path. Orchestrator, registry, billboard indexing, runtime all built around honeycomb patterns.
- The honeycomb sidecar fix is small: ~100-150 lines, localized to `honeycomb.py` `scan_pile()` (281-329) and `register_sources()` (345-378), plus a hash-matching invariant. **Not a refactor — a localized addition.**
- Building Palimpsest against a deprecated path means painful rip-and-replace when honeycomb becomes mandatory.

### 3.3 Agent 2's concrete sidecar invariant

```
IF file.suffix == ".manifest.json"
  AND file has a sibling with same stem (e.g., "<stem>.md")
  AND sibling.hash matches source_record.artifact_hash
THEN:
  - do not register manifest as a source
  - load and validate manifest JSON
  - store mapping: source_id → manifest_path
  - pass to orchestrator as pre-built
ELSE:
  - register manifest as ordinary .json dataset source
```

Hash-matching means stale sidecars degrade gracefully — worst case the manifest is treated as ordinary data. No corruption risk.

### 3.4 Codex's reversal

Presented with the verification findings in a resumed session, Codex reversed without hedging:

> *"Agent 2 is right. This flips my original ordering. The consumer path that matters is honeycomb, not legacy domain mode, and the missing logic is small and localized in honeycomb.py. If you do not land this first, every Palimpsest source-mode artifact is either targeting a deprecated path or waiting on a known, cheap Ariadne fix. This is the cleanest critical-path move: bounded scope, low conceptual risk, immediate architectural clarity."*

> *"I do not think my original ordering survives the verification pass. My earlier sequence assumed legacy domain mode was an acceptable temporary landing zone; that assumption is now wrong. The practical program is A → D → C → B, with E trailing the architectural cutover."*

### 3.5 New ordering with defenses

**1. A — Honeycomb sidecar bypass in Ariadne (START HERE).**
> *"Bounded scope, low conceptual risk, immediate architectural clarity. Without it, every Palimpsest source-mode artifact is either targeting a deprecated path or waiting on a known cheap Ariadne fix."*

**2. D — Redesign discovery data structures (kill manuscript-shaped `Opportunity`).**
> *"The real blocker on opportunity mode. The current discovery core is manuscript-shaped, so if you start prospecting work before replacing that object model, you will either jam non-manuscript corpora into the wrong tables or build throwaway glue you will delete a week later."*

**3. C — Gallica prospecting adapter.**
> *"Fastest strategic validation after D: a real corpus, cheap signals, and immediate feedback on whether the labor-bottleneck detector is actually finding the right thing. But it should not come before the schema reset. Adapter first is how bad abstractions become permanent."*

**4. B — Source-mode assemble stage in Palimpsest.**
> *"The manifest schema is already stable enough; the missing piece is not 'what should a manifest look like?' but 'can the live Ariadne path consume one without hacks?' Once A lands, building the assemble stage makes sense because it is targeting a real intake path instead of a dead bridge."*

**5. E — Vestigial layer cleanup.**
> *"Do this after the new seams exist, not before. Cleanup-first here is mostly aesthetic discipline masquerading as sequencing. Delete the dead web/publish layer when the replacement outputs are real."*

## 4. Corpus refinement — the cybernetics carve-out

Codex rejected early cybernetics wholesale (mixes labor-bottlenecked with compute-limited, institution-limited, politics-killed). Verification agrees for the broad corpus but identified **real labor-bottleneck-pure sub-corpora** Codex missed:

- **Machine Translation 1950–1966**, specifically the **Georgetown-IBM Experiment**. Employed 200-250 people full-time at $3M/year by 1958 *because human translation labor was the binding constraint* on reading Russian scientific papers. The "Dostoevsky Machine in Georgetown" paper documents this directly. This is as labor-pure as it gets.
- **Early information retrieval and indexing 1945–1970** — Cleverdon, Salton et al. explicitly framed their work around manual indexing labor economics.
- **Engelbart's Augment/NLS bootstrapping** — the entire philosophy ("each new feature is created using the features created before it") is a labor-efficiency architecture.

**Revised phase-2 corpus:** machine translation papers (1950–1966) + early IR papers. **Not** "narrow CHM knowledge-systems lane." MT is more precisely labor-bottleneck-pure than oral histories.

## 5. DreamCandidate schema refinements

Base schema from §4 of the main analysis holds, with these changes:

**Add fields:**
- `language_of_source` — required. French, Latin, Greek prefaces have different signal patterns.
- `temporal_markers: {articulated_date, articulation_context}` with `articulation_context` enum (`preface|colophon|editorial_note|postscript`). Disambiguates 1887 original lament from 2020 modern editor's note.
- `portfolio_elevation_status: unreviewed|promoted_to_source|rejected|merged_with` — tracks the stage gate.
- `scale_aspiration: personal|institutional|national|universal` — disambiguates scale from genre.

**Collapse:**
- `evidence_statement_mode` + `bottleneck_evidence` → single `evidence_mode: source_stated|close_paraphrase|cartographer_inferred`. They should never diverge.

**Expand `bottleneck_type` enum:**
- Add `knowledge_engineering` (MT dictionary building), `manual_indexing` (IR literature), `annotation` (linguistic tagging).

**Independence:**
- Do NOT reuse `ManifestNode` types directly in `DreamCandidate`. Map `evidence_mode` → `statement_mode` only at promotion time. This preserves the distinction between prospect confidence and validated manifest confidence.

## 6. Practical program

```
Phase 1: Honeycomb sidecar bypass (A)
  Location: D:/Projects/meridian/ariadne/
  Files: ariadne/honeycomb.py (scan_pile ~281-329, register_sources ~345-378)
  Scope: ~100-150 lines, localized addition
  First deliverable: docs/SIDECAR_BYPASS_v0.1.md — written invariant spec (half day)
  Then: implementation against spec
  Duration: 2-3 days total

Phase 2: Discovery data structure redesign (D)
  Location: D:/Projects/palimpsest/
  Files: discovery/records.py, discovery/database.py
  Scope: replace manuscript-shaped Opportunity with corpus-agnostic Prospect
  Must precede any new archive adapter work
  Duration: 3-5 days

Phase 3: Gallica prospecting adapter (C)
  Location: D:/Projects/palimpsest/discovery/sources/
  Files: new gallica_critical_prefaces.py adapter
  Prerequisite: D complete
  Target sub-collections: Collection Budé → Classiques Garnier → Pléiade
  Duration: 1 week

Phase 4: Source-mode assemble stage (B)
  Location: D:/Projects/palimpsest/
  Files: new assemble.py, retire cmd_publish, wire as palimpsest ingest <doc-id>
  Prerequisite: A complete
  Output contract: <doc_id>.md + <doc_id>.manifest.json + <doc_id>.anchors.json
  Duration: 1 week

Phase 5: Vestigial cleanup (E)
  Move expanded dead-file list to archives/2026-04-07_publishing_stack/
  Extract library_contracts.py from contracts.py
  Archive stale docs
  Duration: 1 day
```

## 7. First concrete work item

Half-day spec: `docs/SIDECAR_BYPASS_v0.1.md` in the Ariadne repo (`D:/Projects/meridian/ariadne/`). The spec nails down:

- Naming convention: `<stem>.manifest.json` beside `<stem>.<supported_ext>`
- Hash-matching invariant: sidecar consumed iff sibling source hash matches `source_record.artifact_hash`
- Stale-sidecar behavior: degrade gracefully (treat as ordinary `.json` source), never error
- Pre-registration validation: manifest JSON must parse and match `Manifest` / `ManifestNode` schema
- Orchestrator handoff: sidecar-detected manifests pass directly to the existing pre-built path used by legacy domain mode
- Rollout: feature-flag behind a honeycomb config option initially, default on after one week of green runs

Implementation follows the spec, not the other way around.
