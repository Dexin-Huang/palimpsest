# Rescript Scaffold (PARES → Pasajeros a Indias)

This repository is a **minimal, elegant scaffold** for building a reconstructed historical corpus from scanned pages.

Design goal: **reconstruct a “universe snapshot”** (people, places, movements, permissions, commodities) from high-volume archival material, with full provenance and deterministic exports.

---

## Core idea (the only idea you need)

Canonical truth lives in **one file per page**:

- Scan image (immutable)
- Geometry (where text is on the page)
- Text layers (diplomatic → normalized → translation)
- Claims (structured extractions with confidence + spans)

Everything else is derived:
- HTML overlay viewer
- Searchable PDF
- PPTX “editable facsimile”
- TEI / PAGE XML / ALTO exports (optional)

---

## Folder layout

```
projects/
  pasajeros_a_indias/
    raw/                  # downloads + source manifests, untouched
    images/               # page images (source scans)
    pages/                # canonical *.page.json (one per page)
    claims/               # optional: claims.jsonl at doc/corpus level
    entities/             # optional: canonicalized people/places/ships
    index/                # optional: search index / embeddings
    exports/
      html/               # human QA & browsing
      pdf/                # searchable facsimiles
      pptx/               # editable facsimiles
schemas/
scripts/
web/
node/
```

---

## The canonical object: a Page

See:
- `schemas/page_schema_v1.md`
- `projects/pasajeros_a_indias/pages/pares_pasajeros_demo_p0001.page.json`

The demo uses normalized coordinates (`bbox_norm`) so renderers can place text correctly on:
- web (pixels)
- pdf (points)
- ppt (inches)

---

## System pipeline (abstracting the “LLM part”)

1) **Ingest**
   - Download images + metadata into `raw/` and `images/`.

2) **Layout**
   - Produce zones (lines/headers/marginalia) with bounding boxes.
   - Store in `pages/*.page.json` under `zones[]`.

3) **Text layers**
   - Fill `text.es_diplomatic`, `text.es_normalized`, translations, etc.

4) **Claims**
   - Extract structured facts as `claims[]` referencing spans in zones.
   - This is how you turn documents into a world model.

5) **Canon / Universe snapshot**
   - Merge claims into corpus-level `entities/` and `events/`.
   - Build a time-sliced snapshot.

6) **Publish**
   - Render HTML/PDF/PPTX
   - Build indexes

---

## Outputs (what you can regenerate forever)

### HTML overlay (fastest QA)
Open a viewer that shows:
- scan as background
- positioned text overlays (toggle Spanish/English layers)

### PDF
- background scan
- invisible selectable text overlay (searchable)

### PPTX
- each page becomes a slide
- scan as background
- each zone becomes an editable text box
- optional: add a second “translation layer” as hidden/alt slide

---

## Universe reconstruction strategy (how you “rebuild the world”)

Store extractions as *claims*, not “truth”:
- claims are local, page-scoped, with provenance + confidence
- later you canonicalize into *entities* and *events*

Recommended corpus-level tables:
- `entities/persons.jsonl`
- `entities/places.jsonl`
- `entities/ships.jsonl`
- `events/events.jsonl` (each with `date`, `place`, `participants`, `source_spans`)

This lets you answer questions like:
- “Who left Seville for New Spain in 1603?”
- “Which origins dominate by decade?”
- “Which families travel together?”
- “How do routes change across wars/epidemics?”

---

## Next step (when you start using real PARES pages)

Add a `raw/source_manifest.jsonl` for downloaded PARES records:
- `source_doc_url`
- `archive_signature`
- `title`
- `date_range`
- list of page image URLs

Then write an `ingest_pares.py` that converts each page into:
- `images/<page_id>.jpg`
- `pages/<page_id>.page.json` with geometry placeholders

---

## Demo files included

- Placeholder scan: `projects/pasajeros_a_indias/images/sample_page.png`
- Demo canonical page JSON:
  - `projects/pasajeros_a_indias/pages/pares_pasajeros_demo_p0001.page.json`

Render scripts are provided as templates in:
- `scripts/`
- `web/`
- `node/`