# CLAUDE.md

Guidance for coding agents working in this repo.

## Project overview

Palimpsest is a library-first pipeline for discovering, transcribing, and
assembling digitized manuscripts. The current canonical path is:

Discovery -> Library intake -> Download -> Transcribe -> Assemble.

## Core commands

Discovery (crawl + master sync):
```
python -m palimpsest discovery run --collection Pal.lat --range 1200-1400 --limit 200 --output discovery/registry/pal_lat_1200-1400_inventory.jsonl
```

Filter interesting candidates:
```
python -m palimpsest discovery filter \
  --input discovery/registry/pal_lat_1200-1400_inventory.jsonl \
  --output discovery/registry/pal_lat_1200-1400_interesting.jsonl
```

Create a library record:
```
python -m palimpsest library intake --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

Download images:
```
python -m palimpsest library download --doc-id vatican_pal_lat_1267
```

Run full pipeline:
```
python -m palimpsest library run --doc-id vatican_pal_lat_1267
```

Worker helper:
```
python -m palimpsest agent-inspect --with-web-search "Find the current official viewer URL"
```

## Transcription CLI (direct)

```
python -m palimpsest transcribe run \
  --image-dir <images_dir> \
  --out-dir <exports/transcriptions_full> \
  --prompt-set transcription_json \
  --workers 10 \
  --skip-existing
```

## Configuration

Use `.env` for model selection:
- `PALIMPSEST_MODEL_TRIAGE`
- `PALIMPSEST_MODEL_VISION`
- `PALIMPSEST_MODEL_READING`
- `PALIMPSEST_MODEL_RECON` (default: `gemini-3.1-flash-image-preview`)

Use `*-image-preview` models for reconstruction/image-generation lanes, not the main transcription lane.

## Repo structure

```
library/           # canonical outputs for each document
discovery/         # master lists, manifest cache, registries
palimpsest/        # core Python package
scripts/           # CLI entry points
docs/              # system documentation
```
