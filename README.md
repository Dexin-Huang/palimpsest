# Palimpsest

Palimpsest is a library-first factory for turning digitized manuscripts into
clean, searchable outputs with full provenance.

Core idea: a stable per-page JSON is the canonical truth. Everything else is
derived (books, HTML viewers, overlays).

## Modules

1) Discovery / Opportunities
   - Crawl metadata, score "interestingness", and maintain a master list.

2) Processing / Transcription
   - Download full-res images, run a two-pass transcription, assemble a book.

3) Recreation (future)
   - Generate restored pages and scanlation-style overlays.

## Quickstart (Golden Path)

1) Crawl a range and append to master list:
```
python scripts/palimpsest.py discovery run --collection Pal.lat --range 1200-1400 --limit 200 --output discovery/registry/pal_lat_1200-1400_inventory.jsonl
```

2) Filter interesting candidates (metadata-only pass):
```
python scripts/palimpsest.py discovery filter \
  --input discovery/registry/pal_lat_1200-1400_inventory.jsonl \
  --output discovery/registry/pal_lat_1200-1400_interesting.jsonl
```

3) Create a library record from a manifest:
```
python scripts/palimpsest.py library intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

4) Run the full pipeline (download -> transcribe -> assemble):
```
python scripts/palimpsest.py library run --doc-id vatican_pal_lat_1267
```

Single entrypoint:
```
python -m palimpsest <command> ...
```

All files in `scripts/` are thin wrappers around the unified CLI.

Defaults: transcription uses the `transcription_json` prompt set unless overridden.

## Layout (Library First)

```
library/
  <doc_id>/
    metadata.json
    page_list.json
    images/
    exports/
      transcriptions_full/
      book/
```

## Configuration

Create a local `.env` (see `.env.example`):

- `GEMINI_API_KEY`
- `PALIMPSEST_MODEL_VISION` (default: gemini-3-flash-preview)
- `PALIMPSEST_MODEL_TRIAGE` (optional)
- `PALIMPSEST_MODEL_RECON` (optional)

## Docs

- `docs/VISION.md` - system vision and data model
- `docs/FACTORY.md` - module summary
- `docs/TRANSCRIPTION_CLI.md` - transcription CLI usage
- `docs/DISCOVERY_SYSTEM.md` - discovery tools and workflow
- `docs/PHILOSOPHY.md` - repository philosophy and guardrails
