# Manuscript Transcription Pipeline

Two-pass transcription for medieval manuscripts using Gemini Agentic Vision.

## Overview

Pass 1 produces a structured JSON draft. Pass 2 refines it against the image
and fixes low-confidence readings.

Outputs:
- `*_pass1.json`
- `*_final.json`
- `exports/book/` (assembled from final pages)

## Key principles

- Always use full-resolution IIIF images.
- Always write intermediate results immediately.
- Never set `max_output_tokens` with Agentic Vision.
- Validate JSON on every pass.

## Model configuration

The model is centralized in `.env`:

```
PALIMPSEST_MODEL_VISION=gemini-3.1-flash-lite-preview
PALIMPSEST_THINKING_LEVEL=high
PALIMPSEST_MEDIA_RESOLUTION=high
```

The code uses `code_execution` to enable Agentic Vision (auto-zoom/crop).

## CLI

Single page:
```
python -m palimpsest transcribe run \
  --image images/f001r.jpg \
  --out-dir exports/transcriptions_full \
  --prompt-set transcription_json
```

Batch:
```
python -m palimpsest transcribe run \
  --image-dir images/ \
  --out-dir exports/transcriptions_full \
  --prompt-set transcription_json \
  --workers 10 \
  --skip-existing
```

## Auto-skip non-text pages

If pass1 returns a non-text `page_type` or near-empty content and
`--auto-skip-non-text` is set, pass2 is skipped and pass1 is copied to final.

## Assembly

Book assembly is automatic after pass2:

- `book_diplomatic.txt`
- `book_normalized.txt`
- `book.xml`
- per-page text outputs
