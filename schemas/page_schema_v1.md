# Rescript Page Schema v1 (Human-readable)

This is the canonical *page* object. Everything else (HTML/PDF/PPTX/TEI) is derived from this.

## Core Principles
- The scan image is immutable truth.
- Geometry is stored in normalized coordinates (0..1) so it can be rendered at any scale.
- Text is stored in multiple layers (diplomatic, normalized, translations).
- Extracted facts are stored as *claims* with provenance and confidence.

## Required fields
- `schema_version`: `rescript.page.v1`
- `page_id`, `doc_id`
- `image.path`, `image.width_px`, `image.height_px`
- `zones[]` with `zone_id`, `type`, `order`, `bbox_norm`, `text`

## Zone fields
- `type`: `line | marginalia | header | stamp | table_cell | illustration | unknown`
- `bbox_norm`: `[x, y, w, h]` where each value is in `0..1`
- `baseline_norm` (optional): list of points `[[x,y], ...]` in `0..1` coordinates
- `text` (object):
  - `es_diplomatic`
  - `es_normalized`
  - `en_literal`
  - `en_interpreted`
  Add additional languages/passes as needed.

## Claims (extraction)
- `claims[]` where each claim has:
  - `claim_id`
  - `type` (e.g., `person_name`, `origin_place`, `destination_place`, `ship_name`, `cargo_item`, `date_recorded`)
  - `value`
  - `span`: `{zone_id, char_start, char_end}` referencing a source text string
  - `confidence`

## Recommended extensions
- `entities/` and `events/` as corpus-level canonicalization outputs
- `gazetteer/` for place normalization and mapping
- `lexicon/` for abbreviations and formula templates
