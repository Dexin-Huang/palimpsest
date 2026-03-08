# Diplomatic Restoration Contract

Purpose: define the first serious output contract for Palimpsest.

This contract sits immediately downstream of `canonical.page`.

The goal is not generic OCR text. The goal is a derived artifact that preserves
the witness closely enough to support scholarship, review, and later readable
edition assembly.

---

## 1. Position In The Pipeline

Current golden path:

`intake -> image prep -> page typing -> transcription -> canonical.page -> diplomatic restoration -> readable edition`

Contract boundary:
- input: `canonical.page`
- primary output: `diplomatic.page`
- assembled output: `diplomatic.book`
- later derived output: `readable.book`

`canonical.page` remains the internal truth object.

`diplomatic.page` is the first derived artifact that a human should be able to
trust and read.

---

## 2. Product Goal

The diplomatic restoration contract exists to answer one question:

`Can Palimpsest reconstruct a page so that it still behaves like the original witness?`

That means the output must preserve:
- line structure
- column structure
- paratext positions where possible
- marginalia and interlinear insertions
- rubric and display text distinctions
- uncertainty and illegibility
- provenance back to source zones and spans

It must not silently normalize, translate, paraphrase, or flatten away the page.

---

## 3. What Counts As Diplomatic

For Palimpsest, a diplomatic restoration is:
- witness-near
- evidence-bound
- structurally faithful
- explicit about uncertainty

It is not:
- a normalized edition
- a translation
- a historical interpretation
- a plain-text OCR dump

Allowed editorial behavior:
- mark uncertain readings
- mark supplied text explicitly
- note unresolved abbreviations
- reorder only when necessary to recover true reading order

Disallowed editorial behavior:
- silently expand every abbreviation into modern form
- silently merge marginalia into main text
- silently drop headers, catchwords, page numbers, or interlinear matter
- silently convert layout-rich pages into flat prose

---

## 4. Output Family

There are three related outputs:

### `diplomatic.page`

The canonical restoration output for a single page.

This is the object all renderers should consume.

### `diplomatic.book`

An ordered assembly of `diplomatic.page` artifacts across a document.

This is used for book-level review, search, and later edition work.

### Rendered views

These are derived from `diplomatic.page` or `diplomatic.book`:
- `.txt`
- `.html`
- `.tex`
- `.xml` or TEI fragments
- overlay or pseudo-facsimile renderings

Rendered views are not the authoritative restoration object.

---

## 5. File Layout

Suggested export layout:

```text
library/
  <doc_id>/
    exports/
      restoration/
        pages/
          <page_id>_diplomatic.json
          <page_id>_diplomatic.txt
          <page_id>_diplomatic.html
        book/
          book_diplomatic.json
          book_diplomatic.txt
          book_diplomatic.html
```

Optional later outputs:
- `book_diplomatic.tex`
- `book_diplomatic.xml`
- `overlay/`
- `pseudo_facsimile/`

---

## 6. `diplomatic.page` Contract

The authoritative page-level restoration object should contain:

- identity
- source reference
- restoration basis
- ordered restoration segments
- linear reading projection
- fidelity notes
- quality and review state

### Required top-level fields

- `artifact_type`
- `doc_id`
- `page_id`
- `created_at`
- `source_schema`
- `source_image_path`
- `basis_layer`
- `segments`
- `linear_text`

Recommended fields:
- `layout_projection`
- `fidelity_flags`
- `open_questions`
- `review`
- `render_hints`

### Required invariant

Every emitted segment must be traceable back to at least one source zone, and
preferably to a source span as well.

---

## 7. `diplomatic.page` Example Shape

```json
{
  "artifact_type": "diplomatic.page",
  "doc_id": "vat_reg_lat_931",
  "page_id": "f011r",
  "created_at": "2026-03-07T18:00:00Z",
  "source_schema": "canonical.page",
  "source_image_path": "images/f011r.jpg",
  "basis_layer": "la_diplomatic",
  "layout_projection": {
    "columns": 1,
    "preserve_line_breaks": true,
    "preserve_marginalia_positions": true
  },
  "segments": [
    {
      "segment_id": "seg_0001",
      "zone_id": "z_main_001",
      "role": "main_text",
      "placement": "main_flow",
      "sequence_index": 0,
      "column_index": 0,
      "line_index": 0,
      "text": "atq; ibi ego tuba cecini sodalibus",
      "break_after": "line",
      "certainty": "uncertain",
      "confidence": 0.88,
      "anchors": {
        "bbox_norm": [0.14, 0.22, 0.63, 0.03]
      },
      "evidence_spans": [
        {
          "zone_id": "z_main_001",
          "char_start": 0,
          "char_end": 34,
          "layer": "la_diplomatic"
        }
      ],
      "editorial": [
        {
          "type": "uncertain_glyph",
          "note": "semicolon-like sign may represent abbreviation"
        }
      ]
    }
  ],
  "linear_text": "atq; ibi ego tuba cecini sodalibus\n",
  "fidelity_flags": [
    "line_structure_preserved",
    "uncertainty_marked"
  ],
  "open_questions": [
    "Abbreviation in opening token remains unresolved."
  ],
  "review": {
    "status": "unreviewed"
  }
}
```

---

## 8. Segment Contract

Segments are the core unit of restoration.

Each segment should correspond to a single readable unit that still respects
page structure:
- a main-text line
- a marginal note
- an interlinear insertion
- a rubric line
- a header or page number
- a table cell or diagram label when those must be preserved diplomatically

### Required fields

- `segment_id`
- `zone_id`
- `role`
- `placement`
- `sequence_index`
- `text`
- `break_after`
- `certainty`
- `evidence_spans`

### Recommended fields

- `column_index`
- `line_index`
- `anchors`
- `confidence`
- `editorial`

### Allowed `role` values

- `main_text`
- `rubric`
- `initial`
- `marginalia`
- `interlinear`
- `header`
- `footer`
- `page_number`
- `catchword`
- `caption`
- `diagram_label`
- `table_cell`
- `other`

### Allowed `placement` values

- `main_flow`
- `margin_outer`
- `margin_inner`
- `header`
- `footer`
- `interlinear`
- `floating`

### Allowed `break_after` values

- `none`
- `line`
- `paragraph`
- `column`
- `page`

### Allowed `certainty` values

- `certain`
- `uncertain`
- `supplied`
- `illegible`
- `damaged`

---

## 9. Fidelity Rules

The restoration layer must follow these rules:

1. Prefer `la_diplomatic` or the equivalent diplomatic source layer when it
   exists.
2. Preserve original lineation when the source evidence supports it.
3. Preserve column membership and segment ordering.
4. Keep marginalia and interlinear matter distinct from the main flow.
5. Mark uncertainty instead of flattening it away.
6. Every editorial supply must be explicit.
7. Every segment must retain provenance to page evidence.

These are more important than producing smooth prose.

---

## 10. Linear Reading Projection

`diplomatic.page` should include a `linear_text` field because humans still
need a quick reading surface.

But this is only a projection.

Rules:
- line breaks should survive where they are meaningful
- marginalia should not be silently injected into the main flow
- interlinear material should be marked, not flattened away
- if a page is mostly non-linear, the projection should remain conservative

This field is for usability, not truth.

---

## 11. `diplomatic.book` Contract

The book-level object is assembled from page restorations.

It should contain:
- `artifact_type = "diplomatic.book"`
- `doc_id`
- `created_at`
- ordered page list
- concatenated or sectioned book text
- assembly notes
- review status

Minimum shape:

```json
{
  "artifact_type": "diplomatic.book",
  "doc_id": "vat_reg_lat_931",
  "created_at": "2026-03-07T18:10:00Z",
  "pages": [
    {"page_id": "f001r", "path": "pages/f001r_diplomatic.json"},
    {"page_id": "f001v", "path": "pages/f001v_diplomatic.json"}
  ],
  "book_text": "...",
  "review": {
    "status": "partial"
  }
}
```

`diplomatic.book` is a convenience assembly, not a replacement for page-level
restoration objects.

---

## 12. Readable Edition Boundary

`readable.book` should be derived from `diplomatic.book`, not directly from raw
transcription output.

That keeps the readable edition honest.

Readable edition transformations may include:
- abbreviation expansion
- normalized spelling
- punctuation smoothing
- translated or modernized notes
- paragraph shaping for readability

But those are later transformations.

The restoration contract stops before that layer.

---

## 13. Review And QA

Every `diplomatic.page` should be reviewable against the witness.

Minimum review fields:
- `status`
- `reviewer`
- `updated_at`
- `notes`

Suggested statuses:
- `unreviewed`
- `machine_only`
- `human_checked`
- `scholar_checked`
- `blocked`

The system should support partial confidence, not binary pass/fail.

---

## 14. Evaluation Criteria

A good restoration contract should support evaluation of:
- transcription fidelity
- line break fidelity
- marginalia retention
- uncertainty marking
- provenance completeness
- renderability into `.txt`, `.html`, `.tex`, and TEI forms

The contract is successful when it makes these measurable.

---

## 15. Bootstrap Scope

For the first implementation, Palimpsest only needs to do a small subset well:

1. main-text lines
2. rubric lines
3. marginalia
4. page numbers and headers
5. linear diplomatic `.txt` assembly
6. JSON provenance for every emitted segment

Do not wait for perfect support for every exotic layout before shipping the
first restoration path.

---

## 16. Design Rule Going Forward

If a restoration output choice would make the page easier to read but less
faithful to the witness, the contract should prefer faithfulness and push the
easier reading into `readable.book`.
