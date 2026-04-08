# Page Evidence Schema

Purpose: define the canonical per-page evidence object that Palimpsest should
optimize around.

This is the most important schema in the system because it sits at the boundary
between:
- raw archival images
- evidence extraction
- reading / interpretation
- restoration / typesetting
- knowledge extraction

If this object is designed well, intake and outputs can evolve without breaking
the system's core truth layer.

---

## 1. Canonical Principle

The canonical truth for a page is not:
- the rendered book export
- the LaTeX facsimile
- the HTML overlay
- the cleaned image

The canonical truth is:
- the source page image
- the page-level evidence JSON tied directly to that image

Everything else is derived.

That means the page evidence object must carry enough structure to support:
- faithful transcription
- page-level reading
- claim and entity extraction
- later restoration and typesetting

---

## 2. Target Shape

The page evidence object should answer six questions:

1. What page is this?
2. Where did it come from?
3. What does it look like structurally?
4. What text and visual zones does it contain?
5. What is happening on the page?
6. How should later systems reconstruct or extract from it?

---

## 3. Top-Level Sections

The canonical page object should have these sections:

- `identity`
- `source`
- `image`
- `preparation`
- `classification`
- `layout`
- `zones`
- `claims`
- `reading`
- `restoration`
- `quality`
- `pipeline`

In the current model these mostly map to:
- `page_id`, `doc_id`, `schema_version`
- `source`
- `image`
- `preparation`
- `classification`
- `layout`
- `zones`
- `claims`
- `reading`
- `restoration`
- `quality`
- `pipeline`

---

## 4. Identity

Required:
- `schema_version`
- `doc_id`
- `page_id`
- `created_at`

Purpose:
- give the page a stable identity
- mark the object as the canonical page evidence form

Notes:
- canonical schema identifier is `canonical.page`
- earlier experiments can be ignored if they do not match the golden path

---

## 5. Source

Purpose:
- preserve archival provenance

Minimum fields:
- source repository
- collection
- catalog or source reference
- manifest URL when applicable
- provenance note

This section should answer:
- which institution did this come from?
- what is the upstream identifier?
- which manifest or catalog record supports this page?

---

## 6. Image

Purpose:
- describe the original page image used for evidence extraction

Minimum fields:
- `path`
- `width_px`
- `height_px`
- `sha256`
- `iiif_url`
- `reading_direction`

Rule:
- this is the original image reference, not a cleaned derivative
- `reading_direction` must support horizontal and vertical witnesses:
  `ltr`, `rtl`, `ttb`, `btt`

---

## 7. Preparation

Purpose:
- record any image processing steps applied before reading

Why it matters:
- restoration and debugging both need to know whether the model read the raw
  image or a cleaned derivative

Suggested fields:
- prepared image list
- preparation steps
- preferred image kind used for downstream reading

Examples of steps:
- crop
- deskew
- debleed
- contrast
- align
- denoise

Critical rule:
- these are derived helpers
- original image remains canonical

---

## 8. Classification

Purpose:
- route the page through the correct downstream path

Key fields:
- `page_type`
- `genre`
- `domain_tags`
- `languages`
- `scripts`
- `has_illustration`
- `has_diagram`
- `has_table`
- `has_marginalia`
- `has_interlinear_glosses`
- `confidence`

Examples:
- a page may be `text_page` + `recipe` + `alchemy`
- a page may be `map`
- a page may be `illustration_only`
- a page may be `table` + `geography`

Why it matters:
- not all pages should be transcribed the same way
- not all pages should be rendered the same way

---

## 9. Layout

Purpose:
- record the page's macro-structure

Current / target fields:
- `columns`
- `column_gap_norm`
- `margins`
- `ruling`
- `writing_area_bbox_norm`
- `line_count_estimate`
- `has_marginalia`
- `has_interlinear_glosses`
- `has_running_header`

This is the layer restoration needs most.

Without layout, a clean pseudo-facsimile or LaTeX-style edition becomes guessy.

Region roles should support both placement and common paratext functions such as:
- `main_text`
- `margin_outer`
- `margin_inner`
- `marginalia`
- `header`
- `footer`
- `page_number`
- `interlinear`
- `paratext`
- `illustration_label`
- `table`
- `diagram`

---

## 10. Zones

Zones are the heart of the page evidence object.

Each zone should describe:
- what region this is
- where it sits on the page
- its reading order
- its text layers
- its style
- its structural role
- its restoration hints
- its confidence

### Core zone fields

- `zone_id`
- `type`
- `order`
- `bbox_norm`
- `baseline_norm`
- `text`
- `confidence`

### Structural extensions

- `script`
- `structure`
- `restoration`
- `style`
- `notes`

### Why zones matter

Zones are what let the system do all of the following later:
- preserve marginalia positions
- distinguish rubric from main text
- reconstruct line breaks
- output diplomatic and normalized editions
- attach claims back to exact textual spans
- render a pseudo-facsimile

---

## 11. Text Layers

A zone should support multiple textual layers.

Examples already present:
- `la_diplomatic`
- `la_normalized`
- `es_diplomatic`
- `es_normalized`
- `en_literal`
- `en_interpreted`

The architectural principle is:
- diplomatic text captures the witness
- normalized text supports reading and comparison
- translation supports broader discovery and extraction

Future extensions can add more language/script families, but the layer idea
should remain stable.

---

## 12. Script And Style

The schema should preserve visual and scribal signals needed for both reading
and restoration.

### Zone script metadata

Examples:
- language
- script family
- hand
- abbreviation density

### Zone style metadata

Examples:
- rubric color
- initial size
- decorative treatment

This matters because a reconstruction engine or LaTeX exporter needs to know:
- which lines are rubricated
- which glyphs or initials should be visually distinct
- which text belongs to a margin versus the main block

---

## 13. Reading

This is the layer after transcription.

Purpose:
- store evidence-bound understanding of what the page is doing

Suggested fields:
- `summary`
- `genre`
- `domain_tags`
- `notable_features`
- `first_person_voice`
- `procedural_text`
- `questions`
- `confidence`

Important distinction:
- `text` is what the page says
- `reading` is what the page is doing

Examples:
- "This page is a recipe sequence for metal whitening"
- "This page is a first-person travel description of Egypt"
- "This page is a table of place names"

This is critical for turning transcription into knowledge recovery.

---

## 14. Claims

Purpose:
- store structured extractions tied to the source text

Current claim fields:
- `claim_id`
- `type`
- `value`
- `span`
- `confidence`
- `attributes`

Claims should always be traceable back to:
- page
- zone
- character span
- text layer

This is non-negotiable.

---

## 15. Restoration / Typesetting Hints

This is the section that supports the "LaTeX version of the old page" idea.

These fields do not make the reconstructed page canonical.
They make it possible.

Suggested fields:
- `preserve_columns`
- `preserve_line_breaks`
- `preserve_marginalia_positions`
- `preserve_interlinear_insertions`
- `preserve_rubrication`
- `preserve_initials`
- `preferred_text_layer`
- `output_modes`
- `notes`

### Why this belongs in the evidence object

Because restoration depends on page evidence:
- zone geometry
- style
- line order
- columns
- rubrication
- initials
- marginalia placement

If we do not preserve those signals in the canonical object, restoration
outputs will become disconnected from evidence.

### Supported restoration targets

The schema should support at least these derived outputs:

1. `diplomatic_edition`
- faithful textual edition
- preserve line breaks and page structure

2. `normalized_edition`
- readable clean edition
- may relax some visual constraints

3. `pseudo_facsimile`
- approximate the original page's spatial feel
- preserve columns, rubrication, initials, marginalia

4. `overlay`
- put text or translation back onto the original page image

5. `latex`
- scholarly printable version
- either diplomatic or reconstructed

6. `tei`
- machine-readable scholarly edition format

---

## 16. Quality

Purpose:
- help routing and QA

Suggested fields:
- `legibility`
- `bleed_through`
- `skew`
- `crop_quality`
- `notes`

These fields explain why a page was easy or hard and help decide:
- specialist model?
- cleanup needed?
- human review needed?

---

## 17. Pipeline

Purpose:
- capture how the page evidence was produced

Suggested fields:
- component assumptions
- model versions
- prompt versions
- notes

This section is what keeps the system auditable and rerunnable.

---

## 18. Minimal Example

```json
{
  "schema_version": "canonical.page",
  "doc_id": "vatican_pal_lat_1267",
  "page_id": "f002r",
  "source": {
    "name": "Vatican Library",
    "collection": "Pal.lat",
    "source_doc_ref": "Pal.lat.1267",
    "iiif_manifest": "https://..."
  },
  "image": {
    "path": "images/f002r.jpg",
    "width_px": 2500,
    "height_px": 3500
  },
  "classification": {
    "page_type": "text_page",
    "genre": "recipe",
    "domain_tags": ["alchemy", "metallurgy"],
    "languages": ["latin"],
    "scripts": ["latin_bookhand"]
  },
  "layout": {
    "columns": 1,
    "line_count_estimate": 29,
    "has_marginalia": false
  },
  "zones": [
    {
      "zone_id": "z1",
      "type": "line",
      "order": 1,
      "bbox_norm": [0.11, 0.14, 0.74, 0.02],
      "text": {
        "la_diplomatic": "Recipe text...",
        "la_normalized": "Recipe text expanded..."
      },
      "structure": {
        "region_role": "main_text",
        "column_index": 0,
        "line_index": 0
      },
      "restoration": {
        "preserve_line_break_after": true,
        "render_class": "main_text"
      }
    }
  ],
  "reading": {
    "summary": "Procedural metallurgical recipe for whitening copper.",
    "genre": "recipe",
    "domain_tags": ["alchemy", "metallurgy"],
    "procedural_text": true
  },
  "restoration": {
    "preserve_columns": true,
    "preserve_line_breaks": true,
    "preserve_rubrication": true,
    "preferred_text_layer": "la_diplomatic",
    "output_modes": ["diplomatic_edition", "pseudo_facsimile", "latex"]
  }
}
```

---

## 19. Current Implementation Status

Current local models now carry the golden-path shape for:
- page classification
- page reading
- page restoration hints
- page quality
- image preparation metadata
- richer zone structure
- zone script metadata
- zone restoration hints

The working assumption is that Palimpsest is early enough to rewrite around this
canonical shape directly rather than carrying migration baggage.

---

## 20. Design Rule Going Forward

When deciding whether a field belongs in the canonical page evidence object,
ask:

`Would a future scholar, extractor, or reconstruction engine need this field to remain tied directly to the page witness?`

If yes, it likely belongs here.

If not, it probably belongs in a derived output.
