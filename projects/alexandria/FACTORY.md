# Palimpsest Factory

Goal: a repeatable, auditable machine that turns large archival collections into
structured, searchable outputs with clear provenance. The canonical truth is the
per-page JSON; everything else is derived.

## Module 1: Opportunities (Discovery + Triage)

Purpose: maintain a living database of potential finds, with a clear initial
interest mark and a cheap AI triage pass. This is where we surface things that
have not been seen because ROI was too low.

Inputs
- IIIF manifests and catalog metadata
- Optional first-page image for quick triage

Outputs
- Opportunities database (authoritative list)
- Initial interest mark (metadata-only)
- AI triage results (Gemini Flash)
- Queue of items ready for processing

Core behavior
- Two-pass triage:
  1) Pass 1: metadata-only scoring for all items (cheap, fast)
  2) Pass 2: Gemini Flash on top-ranked items (metadata-first; image optional)
- Keep track of first_seen_at and last_seen_at to detect newly digitized items
- Human overrides always win

Data fields (minimum)
- manuscript_id, shelfmark, repository
- manifest_url, canvas_count
- initial_interest (bool), initial_score (0-10)
- interest_score (0-10), interest_reason
- triage_method, triage_model, triage_at, triage_json
- first_seen_at, last_seen_at
- status (new, triaged, queued, processed, archived)

## Module 2: Processing / Transcription

Purpose: turn queued opportunities into canonical page JSON at scale.

Inputs
- Selected opportunities
- Page images (downloaded from IIIF or local)

Outputs
- pages/*.page.json (canonical truth)
- Optional claims.jsonl and QA artifacts
- Pipeline metadata saved into each page JSON

Stages
1) Ingest: download images, register provenance
2) Layout/Segmentation: find zones
3) Transcription: diplomatic + normalized
4) Translation: English layers (optional)
5) Claims extraction: structured facts with spans
6) Validation: schema + zone sanity checks

Repeatability requirements
- Record model IDs + prompt versions in each page JSON
- Keep run config per batch
- Never mutate source images; only derived outputs change

## Module 3: Recreation (Reconstruction / Exports)

Purpose: generate human-readable outputs (HTML overlay, PDF, PPTX, scanlations).
Inputs: page JSON + images
Outputs: exports only (no new truth)

---

Current focus: Modules 1 and 2.
