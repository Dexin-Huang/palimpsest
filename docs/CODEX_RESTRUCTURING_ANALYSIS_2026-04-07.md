# Codex Restructuring Analysis — 2026-04-07

**Source:** Strategic consultation with Codex (codex-cli 0.118.0), run 2026-04-07 via `codex exec` on the full Palimpsest + Ariadne repositories.

**Purpose:** Grounded read of the current Palimpsest pipeline and concrete recommendations for restructuring it from a manuscript publishing pipeline into a dual-mode prospecting + ingestion engine feeding Ariadne (Mundaneum-class knowledge compiler).

**Framing inputs Codex was given:** the strategic thesis (labor-bottlenecked dreams), Ariadne's architecture docs (`meridian/ariadne/docs/system-design-v0.2.md`, `why-ariadne.md`), the memory files (project, feedback), and the full Palimpsest codebase.

**Execution mode:** `--dangerously-bypass-approvals-and-sandbox`, read-only task, analysis only.

This document is the cleaned final deliverable. The raw run trace (~1.3 MB, 29 668 lines of reasoning + tool-use logs) lives at `C:/Users/dexin/AppData/Local/Temp/codex_output_palimpsest.md` and will be deleted when no longer needed.

---

## 1. Current Pipeline State

Observed, not inferred:

- The actual CLI is still four-headed: `discovery`, `library`, `book`, `transcribe` in `palimpsest/cli.py:13` and `palimpsest/commands/__init__.py:15`. The repo guide still describes `discovery run`, `discovery filter`, `library run`, and `assemble` in `CLAUDE.md:7`, but those commands do not exist in the current code. **That mismatch is real, not cosmetic.**

- The load-bearing heavy path today is:
  `library intake` → `library download` → `transcribe run` → `survey` → `enrich`.
  Intake writes `metadata.json` and `page_list.json` in `palimpsest/commands/library.py:24`, `palimpsest/library/intake.py:32`, and `palimpsest/library/iiif.py:44`. Download fetches images and logs runs in `palimpsest/library/download.py:55`. Transcription writes `transcriptions.jsonl` page records in `palimpsest/commands/transcribe.py:30`, `palimpsest/transcribe.py:82`, and `palimpsest/models/enriched.py:10`. Survey builds a translation brief in `palimpsest/survey.py:158`. Enrich writes `enriched.jsonl` in `palimpsest/enrich.py:230`.

- **There is no real `assemble` stage.** The terminal stage in code is still publishing HTML. `transcribe publish` calls `build_book_site()` in `palimpsest/commands/transcribe.py:135` and `palimpsest/publish.py:402`. That produces one HTML page per folio plus an index, not an Ariadne manifest.

- The discovery side is already a cheap-tier spine, but manuscript-centric. `sources ingest` scrapes curated adapters, optionally fetches manifests, writes `Manuscript` + `Opportunity` rows, and can run triage in `palimpsest/commands/discovery.py:63` and `palimpsest/discovery/workflow.py:384`. The schema it writes is explicitly manuscript-shaped in `palimpsest/discovery/records.py:34` and `palimpsest/discovery/database.py:107`. **That is a problem for opportunity mode on letters, oral histories, and editorial prefaces.**

- `discovery/triage.py` is mechanically useful. It already has:
  - metadata packet building in `palimpsest/discovery/triage.py:417`
  - model execution/parsing in `palimpsest/discovery/triage.py:245`
  - DB persistence in `palimpsest/discovery/triage.py:341`
  - DB-wide runners in `palimpsest/discovery/triage.py:623` and `palimpsest/discovery/triage.py:745`

  What does not fit is the ontology and evidence surface: the prompt is still "recover tracks of a vanished human world" in `palimpsest/prompts/opportunity_triage.txt:41`, and thumbnail fetch is Vatican-only in `palimpsest/discovery/triage.py:297`.

- `discovery/scout.py` does not implement opportunity mode. It ranks already-triaged manuscripts and emits a markdown scouting memo in `palimpsest/discovery/scout.py:119` and `palimpsest/discovery/scout.py:177`. Useful remnants: candidate collection and workspace writing. Wrong for the new job: prompt, output type, ranking logic, and mission.

- **Ruthless vestigial layer:**
  - `palimpsest/commands/book.py:10`
  - `palimpsest/publish.py:1`
  - `palimpsest/reader/witness.py:158`
  - `palimpsest/web/site_pages.py:42`
  - `palimpsest/web/folio_page.py:105`

  These are presentation stacks for the dead book/reader goal.

---

## 2. Question A — Archive Selection

> Signal density below is Codex's estimate, not a published metric.

### Broad opportunity-mode targets

| Rank | Archive | Access | Legal status | Mode | Signal | Call |
|---|---|---|---|---|---|---|
| 1 | **Gallica critical-edition prefaces / editorial intros** | SRU + IIIF + export tooling | Public online; Gallica explicitly supports IIIF embedding and broad non-commercial reuse; commercial reproduction can require fees | Opportunity | VH | **Best first-wave corpus.** Matches the exact textual zones opportunity mode will scan. |
| 2 | Darwin Correspondence Project | Searchable site + XML letter pages/indexes | Public web access; project copyright/takedown still applies | Opportunity | H | Clean, structured, high-quality editorial corpus; cheap to query. |
| 3 | Newton Project | Searchable texts + XML/TEI infrastructure | Public web access; image/reuse depends partly on holding institution | Opportunity | H | Strong for editorial ambition, unfinished work, commentary traditions. |
| 4 | Computer History Museum oral histories | Public catalog/transcripts + CC0 metadata API | OPENCHM metadata/API is CC0; site content/images have tighter terms | Opportunity | H but noisy | **Use only a narrow knowledge-systems lane:** Engelbart, Nelson, Kay, Alto, hypertext, publishing. |
| 5 | Corpus Thomisticum | Public text corpus | Publicly readable; rights reserved | Opportunity | VH but narrow | Excellent positive-seed corpus for labor-at-scale dreams; **bad as sole calibration corpus because it is too thesis-pure.** |
| 6 | Niels Bohr Library oral histories (AIP) | Public repository / virtual access | Mixed public access; quoting transcripts requires permission | Opportunity | M | Good for reflective project histories; weaker on explicit labor-bottleneck language. |
| 7 | Mundaneum online catalogue | Online catalog; limited full-text access | Catalog public; deeper consultation/reproduction by request/fees | Opportunity | H conceptually / L operationally | **Important symbolically, bad as a first affordable prospecting target.** |

### Source-mode targets

| Rank | Archive | Access | Legal status | Mode | Signal | Call |
|---|---|---|---|---|---|---|
| 1 | **IDP Dunhuang collections** | Public web + IIIF | Rights vary by item/partner; IDP defaults to personal/non-commercial reuse unless otherwise stated | Source | VH | **Best existing fit.** The repo already has targeted IDP lanes. |
| 2 | Wellcome Collection manuscripts/archives | Catalogue API + IIIF Image API | Item-level rights are explicit: CC-BY, CC-BY-NC, OGL, or in-copyright | Source | H | Very good for medical notebooks, correspondence, collection-building files, marginalia. |
| 3 | Cambridge Digital Library | Public collections + IIIF manifests | Public online; site terms reserve content, some collections have looser item-level terms | Source | H | **High-value only when collection-specific:** Michaelides Arabic papyri, WongAvery Chinese materials, Royal Asiatic manuscripts. |
| 4 | Gallica manuscript lanes | SRU + IIIF | Public online; reuse varies with item/public-domain status | Source | H in targeted lanes | Good for Chinese medicine/divination and other already-coded lanes; **bad as a blanket "all Gallica manuscripts" ingest.** |
| 5 | e-codices | Public IIIF | Public online; reuse terms need per-item checking | Source | M-H | Strong for technical, alchemical, computistical, and miscellany codices. Weak for generic famous manuscripts. |
| 6 | Vatican DigiVatLib | Public IIIF viewer/manifests | Personal study/download okay; publication/reuse requires permission | Source | Variable | **Collection-specific only. "Pal.lat 1200–1400" was a calibration corpus, not a strategy.** |
| 7 | British Library digitised manuscripts | Catalog + IIIF manifests; many images downloadable | Catalog text CC-BY; image rights vary; some free 2000px downloads | Source | M | Worth targeted lanes, not blanket ingest. |

### First calibration corpus for opportunity mode

- **Not early cybernetics broadly.**
- **Best first corpus: Gallica public-domain critical-edition prefaces / editorial introductions.**
- Reason: it matches the exact surfaces opportunity mode will read, gives abundant positives and negatives, and filters out compute/politics noise.
- Phase-2 corpus: narrow CHM knowledge-systems oral histories. **Not "Macy / Wiener / Minsky / etc." as a blob.**

---

## 3. Question B — Tiered Pipeline + CLI + Deletions

### Tiered architecture

| Tier | Input | What fits now | Promotion gate | Output |
|---|---|---|---|---|
| **Triage** | IIIF metadata + bibliographic metadata + low-res front matter/colophon/middle sample | `discovery/triage.py` runner/persistence mostly fits | There is a readable high-signal region and the source is cheap enough to target-scan | `TriageVerdict` |
| **Opportunity** | Targeted VLM read over prefaces, editorial intros, marginalia, colophons | Very little of `scout.py` fits beyond bundling | At least one grounded DreamCandidate with explicit or strong close-paraphrase evidence and a viable portfolio class | `dream_candidates.jsonl` |
| **Source** | Intake/download/transcribe/survey/enrich + new assemble | `library/*`, `transcribe.py`, `survey.py`, `enrich.py` fit | **Manual portfolio elevation only; never auto-promote just because something is interesting** | `<doc_id>.md`, `<doc_id>.manifest.json`, `<doc_id>.anchors.json` |

### Honest read

**Load-bearing:**
- `palimpsest/library/intake.py:32`
- `palimpsest/library/iiif.py:44`
- `palimpsest/library/download.py:55`
- `palimpsest/transcribe.py:178`
- `palimpsest/survey.py:158`
- `palimpsest/enrich.py:230`
- Discovery DB access in `palimpsest/discovery/workflow.py:384` and `palimpsest/discovery/database.py:137`

**Vestigial or wrong-shaped:**
- HTML publishing/reader stack (above)
- Manuscript-only discovery schema in `palimpsest/discovery/records.py:34`
- Quarantined carryover tables and old obscurity/WTF prioritization in `palimpsest/discovery/database.py:74`

**`discovery/triage.py` fit:** ~70 % reusable mechanically, ~30 % must change. Keep the execution loops, parsing, and persistence. Replace the prompt, scoring ontology, record type, and image sampling strategy. The current `fetch_thumbnail()` is unusably source-specific for the new framing.

**`discovery/scout.py` fit:** ~20 % reusable, 80 % rewrite. Keep candidate bundling if useful. Replace prompt, candidate schema, ranking logic, and output type entirely.

### Stage gates

- **Triage → Opportunity:** not "score ≥ X". Evidence required: one of front matter / editorial intro / colophon / marginalia is present, visually or catalogically identifiable, and accessible cheaply enough for a targeted read.

- **Opportunity → Source:** not "interesting idea". Evidence required: grounded quote or close paraphrase naming an abandoned or scaled-back project, plus `modern_feasibility != no`, plus the source class is still image-bound enough that Ariadne cannot just read it directly.

### CLI shape

- The user sketch is right at the top level: `palimpsest prospect ...` and `palimpsest ingest ...`.
- **Better cut:**
  - `palimpsest prospect <corpus>` — full cheap pass
  - `palimpsest prospect shortlist` — merge DreamCandidates into the standing portfolio
  - `palimpsest ingest <doc-id>` — heavy pipeline for one promoted source
- `discovery`, `library`, and `transcribe` should become low-level / admin surfaces, not the public conceptual surface.
- `book` should die.

### Delete entirely when implementation starts

- `palimpsest/commands/book.py:1`
- `palimpsest/publish.py:1`
- `palimpsest/reader/witness.py:1`
- `palimpsest/reader/common.py:1`
- `cmd_publish` in `palimpsest/commands/transcribe.py:135`
- `add_book_subparser` wiring in `palimpsest/commands/__init__.py:27` and `palimpsest/cli.py:5`

**Do not delete `palimpsest/contracts.py:1` wholesale yet.** It contains real library constants alongside probably-dead render/page-assembly names.

---

## 4. Question C — Output Formats + Ariadne Handoff

### What exists because of the dead publishing goal

- Almost all of `palimpsest/publish.py:1` exists to build HTML chrome.
- `palimpsest/commands/book.py:10` is purely a static reader wrapper.
- The structurally useful residue is only this: ordered page records, page/image co-location, and the fact that `page_list.json` already gives you a stable page sequence and source image URL seed for anchors in `palimpsest/library/iiif.py:62`.

### Source-mode output

- The user's proposed contract is correct.
- Keep these as the only handoff artifacts:
  - `<doc_id>.md`
  - `<doc_id>.manifest.json`
  - `<doc_id>.anchors.json`
- Keep `transcriptions.jsonl` and `enriched.jsonl` as intermediate run artifacts, **not** as Ariadne-facing outputs.

**Refinement:**

- `<doc_id>.md` should be dense, folio-ordered, no presentation chrome, with stable anchor labels embedded inline.
- `<doc_id>.manifest.json` should target Ariadne's existing `Manifest` container in `meridian/ariadne/ariadne/manifest.py:205` and the settled node schema in `meridian/ariadne/docs/system-design-v0.2.md:75`.
- `<doc_id>.anchors.json` should map each `source_anchor_id` to: `page_id`, `canvas_id`, `manifest_url`, `image_service`, `region_xywh`, and optionally character offsets in the markdown/transcription stream.

### Opportunity-mode output

- Make `DreamCandidate` **first-class and JSONL, not memo text.**
- Write per-run batches to `discovery/prospects/<corpus>/<timestamp>/dream_candidates.jsonl`.
- Maintain a rolling merge at `discovery/portfolio/shortlist.json`.

**Suggested schema:**

```json
{
  "id": "dream_<hash>",
  "corpus_id": "gallica_critical_prefaces",
  "source_doc_id": "ark_12148_bpt6k...",
  "source_title": "string",
  "attempted_project": "string",
  "original_author": "string|null",
  "evidence_text": "grounded quote or tight paraphrase",
  "evidence_statement_mode": "source_stated|close_paraphrase|cartographer_inferred",
  "source_anchor_ids": ["a1", "a2"],
  "bottleneck_type": "cataloguing|transcription|translation|cross_reference|collation|other",
  "bottleneck_evidence": "source_stated|cartographer_inferred",
  "modern_feasibility": "yes|partial|no",
  "portfolio_class": "mundaneum|oed|pinakes|commentary|provenance|observation|critical_edition|new",
  "confidence": 0.0,
  "notes": "string"
}
```

### Ariadne handoff — critical finding

- **Good news:** no Ariadne manifest-schema change is needed. Palimpsest can emit the current `Manifest` / `ManifestNode` shape.
- **Bad news:** the sidecar bypass is not fully wired for honeycomb piles.
- In current Ariadne, honeycomb scan treats `.md`, `.json`, `.jsonl`, etc. as ordinary sources in `meridian/ariadne/ariadne/honeycomb.py:26`. It will hash a `.manifest.json` as just another dataset file.
- The sibling-sidecar bypass already exists **only in the legacy domain orchestrator** via `find_manifest_for_source()` in `meridian/ariadne/ariadne/orchestrator.py:151` and `_phase_create_manifests()` in `meridian/ariadne/ariadne/orchestrator.py:174`.
- So:
  - **Long-term:** Ariadne needs a honeycomb-side change to ignore sidecars as standalone sources and consume `<stem>.manifest.json` beside `<stem>.md`.
  - **Short-term:** if you want zero Ariadne changes, use the legacy domain-side sidecar path.

---

## 5. Sequencing Plan

1. **Settle the source-mode artifact contract first:** `.md`, `.manifest.json`, `.anchors.json`. Without that, every pipeline discussion stays mushy.
2. Redesign discovery data structures next: manuscript-only `Opportunity` is the wrong core object for prospecting.
3. Reframe `triage.py` into corpus-agnostic cheap triage.
4. Replace `scout.py` with structured DreamCandidate extraction and shortlist merging.
5. Add the first opportunity corpus adapter: Gallica critical-edition prefaces.
6. In parallel, add Ariadne honeycomb sidecar support.
7. Only after the new contract exists, delete the book/publish stack.

**Parallelizable:**

- Ariadne sidecar ingestion work can run in parallel with Palimpsest source assembler design.
- New archive adapters can run in parallel with DreamCandidate schema work.
- HTML-stack deletion should wait until after the new outputs land.

---

## 6. Pushback

Codex's explicit dissent. Each point is a place where the framing (as given by the user and reflected by Claude) was weak or about to make a mistake.

1. **`assemble` is not an existing stage. Stop talking as if it is.** The repo today ends in translation JSONL and HTML publishing.

2. **"Early cybernetics archive" is too sloppy for calibration.** It mixes labor-bottlenecked dreams with compute-limited, institution-limited, and politics-killed projects. That will poison the detector.

3. **Blanket archive names are a fame trap.** "Vatican", "BL", "Parker", "Cambridge" are not targets. Specific collections are targets. Most famous manuscript shelves are common-text waste.

4. **Do not let opportunity mode emit analyst prose.** Markdown scouting memos are disposable. DreamCandidate JSON is the product.

5. **Killing `book` is correct, but if you also kill page-level provenance just because the reader is dead, you will break the Otlet-lineage requirement at the source.** Delete chrome, not anchors.

6. **Your sidecar story is only half-true until Ariadne honeycomb learns it.** Domain mode already has the bypass. Honeycomb does not.

7. **"An organized `.md` file almost right" is not the real contract.** The `.md` is for humans. The load-bearing contract is anchor-resolved manifest plus auditable anchor map.

---

## 7. Implementation Targets

### Palimpsest files to touch first

- `palimpsest/cli.py`
- `palimpsest/commands/__init__.py`
- `palimpsest/commands/discovery.py`
- `palimpsest/commands/transcribe.py`
- `palimpsest/discovery/workflow.py`
- `palimpsest/discovery/triage.py`
- `palimpsest/discovery/scout.py`
- `palimpsest/discovery/database.py`
- `palimpsest/discovery/records.py`
- `palimpsest/discovery/sources/registry.py`
- `palimpsest/library/iiif.py`
- `palimpsest/transcribe.py`
- `palimpsest/survey.py`
- `palimpsest/enrich.py`
- `palimpsest/models/enriched.py`

### Ariadne files to touch

- `meridian/ariadne/ariadne/honeycomb.py`
- `meridian/ariadne/ariadne/orchestrator.py`

### Delete after replacement lands

- `palimpsest/commands/book.py`
- `palimpsest/publish.py`
- `palimpsest/reader/witness.py`
- `palimpsest/reader/common.py`
- likely `palimpsest/web/site_pages.py`
- likely `palimpsest/web/folio_page.py`

### First two concrete branches

If work starts now, the first two concrete branches should be:

1. **Palimpsest prospecting schema + `triage.py` reframing.**
2. **Ariadne honeycomb sidecar bypass.**
