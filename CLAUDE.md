# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Palimpsest (Rescript) is a scaffold for processing scanned historical documents into structured, searchable data. The primary use case is Spanish colonial archives ("Pasajeros a Indias" - passenger records to the Americas).

## Commands

All scripts run from repo root.

**Validate a page JSON:**
```bash
python scripts/validate_page.py --page projects/pasajeros_a_indias/pages/<page>.page.json
```

**Render searchable PDF:**
```bash
python scripts/render_pdf_overlay.py \
  --page projects/pasajeros_a_indias/pages/<page>.page.json \
  --out projects/pasajeros_a_indias/exports/pdf/<page>.pdf \
  --layer es_normalized
```

**Render HTML viewer:**
```bash
python scripts/render_html_single.py \
  --page projects/pasajeros_a_indias/pages/<page>.page.json \
  --out projects/pasajeros_a_indias/exports/html/<page>.html
```

Python dependencies: `reportlab` (for PDF).

## Architecture

**Single source of truth**: `*.page.json` files in `projects/<project>/pages/`. Everything else (HTML/PDF/PPTX) is derived from these.

**Coordinate system**: All geometry uses normalized coordinates (`bbox_norm: [x, y, w, h]`) where values are 0-1 fractions of image dimensions. This allows rendering at any scale.

**Text layers** in each zone:
- `es_diplomatic` - exact transcription preserving original spelling
- `es_normalized` - modernized Spanish spelling
- `en_literal` - direct English translation
- `en_interpreted` - contextual English interpretation

**Claims**: Structured extractions (person names, places, dates) with `span` references back to source text zones. Claims are local/page-scoped assertions, later canonicalized into corpus-level entities.

**Pipeline stages**: ingest → layout → transcription → normalization → translation → claim extraction → export

## Key Schema (rescript.page.v1)

Required fields: `schema_version`, `page_id`, `doc_id`, `image` (path, width_px, height_px), `zones[]`

Each zone requires: `zone_id`, `type` (line|marginalia|header|stamp|table_cell|illustration|unknown), `order`, `bbox_norm`, `text`

## Project Structure Convention

```
projects/<name>/
  raw/       # source downloads (immutable)
  images/    # page scans
  pages/     # canonical *.page.json
  claims/    # optional corpus-level claims.jsonl
  entities/  # canonicalized persons/places/ships
  exports/   # html/, pdf/, pptx/
```
