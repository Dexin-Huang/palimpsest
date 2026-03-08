# Palimpsest Architecture

Purpose: define the detailed operating architecture for Palimpsest as a
knowledge-recovery system.

Top-level frame:

`Intake -> Processing -> Output`

But in Palimpsest those words mean something more precise:

`Intake -> Evidence Processing -> Knowledge Output`

The point is not to build an OCR conveyor belt. The point is to turn archival
corpora into provenance-bearing evidence and then into research-useful
knowledge artifacts.

Current product priority:
- `canonical.page` as the internal truth object
- diplomatic restoration as the first serious output
- readable edition assembly as the first human-facing output

Large-scale extraction, comparison, and discovery automation remain important,
but they are downstream of restoration quality.

Primary specs:
- `docs/PAGE_EVIDENCE_SCHEMA.md`
- `docs/DIPLOMATIC_RESTORATION_CONTRACT.md`

---

## 1. System Goal

Palimpsest should do three things well:
- ingest promising archival material
- convert page images into reliable evidence
- produce outputs that help recover neglected knowledge traditions

This implies three layers of truth:
- source truth: the archive, manifest, catalog, scan, and metadata
- evidence truth: page-level JSON tied directly to images
- derived knowledge: claims, entities, dossiers, comparisons, and exports

Only the first two are canonical. Everything else is derived.

---

## 2. Top-Level Pipeline

### Intake

Purpose:
- decide what is worth bringing into the system
- normalize it into a stable document record
- prepare it for page-level processing

Outputs:
- opportunity record
- document metadata
- page inventory
- acquisition plan

### Processing

Purpose:
- turn raw page images into auditable evidence
- progressively move from pixels -> text -> meaning -> structured knowledge

Current emphasis:
- produce faithful page reconstruction before broad knowledge extraction
- preserve the information restoration needs even if later stages stay manual

Outputs:
- page images
- page evidence JSON
- diplomatic restoration artifacts
- readable manuscript rollups
- extracted claims/entities/relations later

### Output

Purpose:
- expose the evidence and derived knowledge in forms that are usable by humans
  and downstream systems

Outputs:
- page JSON
- diplomatic restoration
- readable book output
- manuscript dossier later
- claim/entity JSONL later
- HTML/PDF/research exports

---

## 3. Intake, In Detail

Intake is not one command. It is a chain.

### 3.1 Source Discovery

Inputs:
- IIIF collections
- repository listings
- PDF drops
- catalog exports
- manually supplied manifests or URLs

Responsibilities:
- watch known repositories
- detect new or changed releases
- capture enough metadata for triage

Artifacts:
- source event log
- raw metadata capture
- first-seen / last-seen timestamps

### 3.2 Opportunity Registration

Purpose:
- create a stable record that "this thing exists and may matter"

Minimum fields:
- source
- repository
- collection
- shelfmark
- title
- manifest or source URL
- language hints
- date or date range
- page count if known
- thumbnail or sample page if available

Status examples:
- `new`
- `seen`
- `triaged`
- `queued`
- `ingested`
- `archived`

### 3.3 Triage and Ranking

Purpose:
- decide what deserves processing budget

Triage should happen in layers:

1. metadata triage
- cheap
- broad
- run on everything

2. thumbnail or first-page triage
- selective
- only on top candidates

3. strategic triage
- human or high-context pass
- asks whether the corpus matches the current research lane

Ranking criteria:
- under-studied collection
- newly digitized source
- scientific / technical / geographic / recipe / travel content
- dense marginalia or commentary
- good comparative potential
- fit with current target lane

### 3.4 Document Registration

Purpose:
- convert an opportunity into a stable document in `library/`

Outputs:
- `metadata.json`
- `page_list.json`
- folder layout under `library/<doc_id>/`

Responsibilities:
- assign `doc_id`
- normalize metadata
- persist provenance
- record the chosen source of truth for this document

### 3.5 Page Inventory Expansion

Purpose:
- enumerate the pages that will be processed

Outputs:
- ordered page list
- canonical page IDs
- image URLs
- optional dimensions / service URLs / labels

Responsibilities:
- parse IIIF manifest or PDF page structure
- preserve ordering
- resolve folio names where possible
- detect missing or duplicate pages

### 3.6 Acquisition Planning

Purpose:
- decide how the document will actually be fetched and processed

Questions:
- IIIF image fetch or local PDF render?
- full resolution or working resolution?
- do we already have images?
- should this doc be sharded?
- does it need specialist routing?

Outputs:
- acquisition job
- processing queue entry
- expected file plan

---

## 4. Processing, In Detail

Processing should be thought of as multiple semantic layers.

### 4.1 Image Acquisition

Purpose:
- ensure the system has local access to the page images it will reason over

Outputs:
- raw page image files
- checksums
- size metadata

Responsibilities:
- fetch or render pages
- write deterministic filenames
- avoid repeated downloads
- record source URL and retrieval metadata

### 4.2 Page Preparation

Purpose:
- make the page usable without erasing source truth

Possible operations:
- orientation fix
- cropping
- bleed-through reduction
- contrast normalization
- recto/verso alignment

Important rule:
- prepared images are derived artifacts
- original images remain untouched

Outputs:
- optional prepared image derivatives
- page-prep metadata

### 4.3 Page Typing

Purpose:
- determine what kind of page this is before expensive reading

Examples:
- text page
- illustration-only
- blank
- index
- table
- map
- ownership note
- binding / cover

Why it matters:
- avoids wasting model calls
- changes prompt or extraction schema
- enables specialist routing

### 4.4 Evidence Extraction

This is where "transcription" lives, but it is only one part.

Two primary modes:

1. faithful page reading
- diplomatic transcription
- normalized transcription
- optional translation

2. direct structured reading
- page image -> constrained JSON
- use when the target structure is known

The system should support both as first-class paths.

Outputs:
- raw model result
- validated page evidence JSON
- page-level confidence and provenance

### 4.5 Reading / Comprehension

This is the layer after transcription.

Purpose:
- understand what the page is doing, not just what characters it contains

Questions:
- is this a recipe, itinerary, commentary, inventory, prayer, gloss, or table?
- what entities or substances appear?
- what operations or claims are being described?
- what parts seem firsthand versus copied tradition?

Outputs:
- page interpretation block
- page type refinement
- extraction hints

This is the stage the user was pointing at with:
- transcription -> reading -> etc.

### 4.6 Intra-Document Aggregation

Purpose:
- connect individual pages into manuscript-level context

Responsibilities:
- stitch page text into folio sequence
- detect repeated sections or headings
- infer section boundaries
- carry forward glossary or named-entity continuity

Outputs:
- book-level text
- section map
- manuscript summary

### 4.7 Knowledge Extraction

Purpose:
- convert evidence into machine-tractable knowledge objects

Examples:
- entities: people, places, substances, texts, instruments
- claims: "X causes Y", "A is mixed with B"
- procedures: recipe steps, process stages
- references: citations, authorities, biblical sites, historical names
- relations: same idea, same place, same substance, contradiction, transmission

Critical rule:
- every extracted object must point back to page evidence

Outputs:
- `claims.jsonl`
- `entities.jsonl`
- `relations.jsonl`
- extraction traces

### 4.8 Cross-Document Comparison

Purpose:
- move from one manuscript to knowledge recovery

Comparisons may include:
- repeated recipes across manuscripts
- variant descriptions of the same place
- recurring named authorities
- same substance under different names
- transmission chains across centuries or languages

Outputs:
- comparison candidates
- similarity links
- contradiction flags
- manuscript cluster summaries

### 4.9 QA and Review

Purpose:
- keep the evidence layer trustworthy

Checks:
- schema validity
- provenance completeness
- missing pages
- broken ordering
- low-confidence outputs
- page type mismatches
- extraction without supporting spans

Outputs:
- QA status
- review queue
- rerun queue

---

## 5. Output, In Detail

Output should be grouped into three families.

### 5.1 Canonical Evidence Outputs

These are the system's stable truth artifacts.

Per document:
- `metadata.json`
- `page_list.json`

Per page:
- original image
- optional prepared image
- page evidence JSON

These should be enough to re-derive everything else.

### 5.2 Knowledge Outputs

These are derived, but strategically central.

Examples:
- manuscript dossier
- story memo
- recipe inventory
- claims/entities/relations JSONL
- comparative notes
- research-ready tables

These are where Palimpsest becomes useful for discovery.

### 5.3 Human-Facing Outputs

Examples:
- book text
- HTML viewer
- PDF exports
- overlays
- timelines
- maps
- search indexes

These should never become the sole source of truth.

---

## 6. Canonical Data Objects

The cleanest architecture has four main object types.

Detailed page-level contract:
- `docs/PAGE_EVIDENCE_SCHEMA.md`

### Document Record

Purpose:
- represent one manuscript / book / archival unit

Examples of fields:
- `doc_id`
- `source`
- `repository`
- `collection`
- `shelfmark`
- `manifest_url`
- `title`
- `date`
- `languages`
- `status`

### Page Record

Purpose:
- represent one page image and its acquisition metadata

Examples:
- `page_id`
- `order`
- `label`
- `source_url`
- `filename`
- `width`
- `height`

### Page Evidence Record

Purpose:
- represent everything we know about one page from direct processing

Examples:
- page type
- diplomatic text
- normalized text
- translation
- structured fields
- confidence
- provenance spans
- model and prompt version

### Knowledge Record

Purpose:
- represent extracted knowledge tied back to evidence

Examples:
- claim
- entity
- relation
- procedure
- observation
- section summary

---

## 7. Job System / Queues

The operational architecture should use queues between stages.

Core queues:
- discovery queue
- triage queue
- intake queue
- download queue
- page-prep queue
- reading queue
- extraction queue
- comparison queue
- QA queue

This matters because not every document should go through every stage at once.

Queue design goal:
- documents and pages can pause, resume, rerun, or fork into specialist lanes

---

## 8. Model Routing

Processing should not assume one model path.

Routing lanes:
- cheap page classification
- specialist page reading
- structured extraction
- fallback frontier model for hard pages

Routing dimensions:
- language / script
- layout family
- page type
- damage level
- task type

This is where local "grunt workers" and future specialist adapters fit, but
they are implementation details under the processing layer, not the top-level
architecture.

---

## 9. CLI Boundary

A clean CLI boundary should mirror the architecture:

### Intake commands
- source discovery
- opportunity triage
- document intake
- page inventory generation

### Processing commands
- image acquisition
- page prep
- transcription / reading
- extraction
- comparison
- QA

### Output commands
- book assembly
- dossier generation
- export rendering
- search index generation

Current repo alignment is partial:
- `discovery` maps to early intake
- `library intake/download/run` maps to intake + processing
- `transcribe` maps to evidence extraction
- `book` maps to one output family

Future commands should preserve this separation rather than collapsing back into
one opaque "run everything" script.

---

## 10. What "Reading" Means In Palimpsest

This deserves to be explicit.

`transcription` is character- and line-level evidence capture.

`reading` is interpretive but still evidence-bound:
- what is happening on the page?
- what genre is this page?
- what does the text claim or describe?
- what should be extracted downstream?

`knowledge output` is the next step:
- what does this manuscript contribute to our understanding of a tradition?

If these are collapsed into one blob, the system becomes hard to debug and hard
to trust.

---

## 11. Practical Operating Loop

The detailed architecture should support this real loop:

1. Find a promising corpus.
2. Intake it into stable document/page records.
3. Process the pages into evidence JSON.
4. Read the manuscript at page and section level.
5. Extract claims, stories, recipes, places, and entities.
6. Compare across manuscripts.
7. Produce a dossier that a scholar could actually use.
8. Decide whether that lane is worth scaling.

---

## 12. Architecture Summary

The compact version is:

### Intake
- discover
- triage
- register
- inventory
- queue

### Processing
- acquire
- prepare
- classify
- transcribe
- read
- aggregate
- extract
- compare
- review

### Output
- canonical evidence
- knowledge artifacts
- human-facing exports

That is the frame future work should plug into.
