# Palimpsest Vision: Knowledge Recovery Factory

## Executive Summary
Build a repeatable factory for recovering neglected knowledge traditions from
archives.

North star:
- Turn neglected manuscripts and archival corpora into searchable evidence for
  discovering lost or under-studied knowledge.

Operationally, this still means building a clean, searchable library:
- Full-resolution images
- Scholarly-grade transcriptions (per page + compiled book)
- Translations, structured extraction, and provenance-bearing page JSON
- Downstream reconstructions and translations

This system scales linearly and stays auditable: discovery -> intake ->
processing -> extraction -> comparison.

The purpose is not transcription for its own sake. Transcription is the
evidence layer that makes downstream discovery possible.

Current operating priority:
- build scholar-trustworthy diplomatic restoration first
- derive readable book output from that restoration
- treat broader extraction and large-scale discovery as downstream work

---

## Design Principles
- North star first: optimize for recovering neglected knowledge, not merely OCR
  throughput.
- Wedge first: restoration quality comes before breadth of automation.
- Repeatable: same inputs, same outputs
- Composable: small modules, clean interfaces
- Auditable: everything logged, everything traceable
- Scalable: parallel-friendly and resumable
- Modular: avoid monolith scripts; keep steps inspectable

---

## System Modules (3)

### 1) Discovery -> Intake
Purpose: find promising corpora for knowledge recovery and rank them for ROI.

Responsibilities:
- Collect metadata from catalogs / APIs / lists
- Score "interestingness" in two passes
- Create a stable, structured intake record per doc

Inputs:
- Metadata, catalog pages, optional thumbnails

Outputs:
- Document registry entry (database)
- Per-document page list (image URLs, order, expected filenames)
- Triage results (scores + reasons)

Two-pass ranking:
1) Pass 1 (cheap): metadata-only scoring
2) Pass 2 (selective): Gemini Flash on top-ranked items

Interestingness rubric (initial, editable):
- Newly digitized or previously inaccessible (+)
- Under-studied collection (+)
- Language/domain match for current focus (+)
- Clear marginalia/annotations indicated (+)
- Rare iconography or uncommon text (+)
- Very high page count with low prior coverage (+)
- Signals of scientific, technical, cosmological, geographic, or recipe content (+)
- Enough related material to support cross-document comparison (+)

---

### 2) Processing / Transcription
Purpose: turn page images into publication-grade evidence.

Responsibilities:
- Ensure page images are present
- Two-pass OCR with agentic vision
- Validate JSON and record structured output
- Assemble full book + per-page files

Inputs:
- Page list (page_list.json)
- Full-resolution images
- Prompt templates (external files)
- Model configuration from `.env`

Outputs:
- `*_pass1.json` (draft)
- `*_final.json` (refined)
- Book outputs:
  - `book_diplomatic.txt`
  - `book_normalized.txt`
  - `book.xml`
  - per-page outputs

Core principles:
- Always write intermediate results immediately (atomic writes)
- Full run logging (events, errors, page status)
- Two-pass OCR with strict validation
- Auto-skip non-text pages when flagged
- Preserve enough structure and provenance for later extraction and comparison
- Optimize first for diplomatic restoration, not generic plain-text OCR

Page types (for auto-skip + audit):
- text_page
- cover
- blank
- ownership
- binding
- illustration_only
- index
- other

---

### 3) Recreation / Restoration (Future)
Purpose: visual restoration + scanlation-style overlays.

This should no longer be treated as distant garnish. Restoration is now part of
the first product wedge, with overlays and visual polish still considered
downstream.

Inputs:
- Transcribed text + layout
- Source images
- Reconstruction prompt templates

Outputs:
- Clean reconstructed pages
- Image overlays with translated text + labels

---

## Library-First Folder Layout
Stable `library/` root:

```
library/
  <doc_id>/
    metadata.json         # registry info + triage
    page_list.json        # page URLs + filenames + order
    images/               # full-resolution downloads
    exports/
      transcriptions_full/
      book/
    runs/                 # per-doc logs + status
```

---

## Data Model (Canonical)

North-star implication:
- Per-page JSON remains canonical because every later claim, translation,
  comparison, and hypothesis must point back to auditable page evidence.

### Document Registry (DB)
Minimum fields:
- doc_id
- source_url
- title
- date
- language
- collection
- triage_score
- triage_reason
- newly_digitized (bool)
- status (discovered, queued, ingested, downloaded, transcribing, assembled, reviewed)
- focus_domains (optional; e.g. alchemy, astronomy, pharmacology, maps)

Doc ID scheme (current decision):
- Format: `<source>_<collection>_<identifier>` (lowercase, ASCII, underscores)
- Example: `vatican_pal_lat_1267`

### metadata.json
Document-level information (single file per doc):
```
{
  "doc_id": "vatican_pal_lat_1267",
  "source_url": "...",
  "title": "...",
  "date": "...",
  "language": "...",
  "collection": "...",
  "triage_score": 0.82,
  "triage_reason": "...",
  "newly_digitized": true,
  "status": "downloaded",
  "created_at": "...",
  "updated_at": "..."
}
```

### page_list.json
Page list and ordering:
```
{
  "doc_id": "vatican_pal_lat_1267",
  "pages": [
    {
      "page_id": "f001r",
      "url": "...",
      "filename": "f001r.jpg",
      "order": 1,
      "width": 2500,
      "height": 3500
    }
  ]
}
```

Page ID rules (current decision):
- Prefer folio ids when provided (e.g., `f001r`, `f001v`)
- Otherwise use `page_0000`, `page_0001`, ... (zero-based, 4-digit)
- Ordering always comes from `order` in page_list.json

---

## Golden Path (Beep-Boop Flow)

1) Discovery -> Intake
   - Identify candidate docs
   - Add to registry + page list

2) Download
   - Fetch all page images to `images/`
   - Store checksums + sizes

3) Transcription
   - Run pass1 + pass2 (Gemini Agentic Vision)
   - Auto-skip non-text pages when appropriate

4) Assembly
   - Build full book output + per-page files

5) Extraction / Comparison
   - Extract entities, claims, and references
   - Compare across manuscripts and time periods

6) Review / Publish
   - Quick human audit
   - Finalize outputs for research or publication

One-command example (current):
```
python -m palimpsest library run --doc-id <doc_id>
```
Behavior: downloads images, runs transcription, assembles book.

---

## Operational Details

Transcription pipeline details live in `docs/TRANSCRIPTION_PIPELINE.md`.

Logging + audit:
- Per-doc runs live under `exports/transcriptions_full/_runs/`
- `events.jsonl`, `errors.jsonl`, `status.json`
- Optional `_traces/` for agentic artifacts

Config + prompts:
- `.env` controls model and API key
- `PALIMPSEST_MODEL_VISION=gemini-3-flash-preview`
- Prompts in `palimpsest/prompts/sets/transcription_json/`

---

## Scaling + Performance

Parallelization:
- Workers configurable (default 10)
- Shardable across machines (shard_count, shard_index)

Resumability:
- Skip existing outputs
- Re-run only failed pages

Target behavior:
- Stable, repeatable outputs
- Sub-minute pass2 per page on average

---

## Implementation Status
Done:
- Document registry (JSONL)
- Intake command (creates folder + writes metadata + page list)
- Download command (fetches images, stores checksums/sizes)
- Single command pipeline (download -> transcribe -> assemble)

---

## Non-Goals (For Now)
- No UI until pipeline is stable
- No complex metadata normalization beyond core fields
- No multi-model experimentation in the base pipeline
- No claim generation without provenance-bearing evidence

---

## Decisions (Current Defaults)
- Registry format: JSONL (simple and diffable; migrate to SQLite later if needed)
- Doc ID scheme: `<source>_<collection>_<identifier>` (lowercase, ASCII)
- Non-folio pages: `page_0000` zero-based with 4-digit padding
- Primary metadata source: IIIF manifest when available, otherwise catalog page
