# Folio Render Contract

Purpose: define the structured object that the edition UI consumes.

The key rule is:

- `canonical.page` is the evidence object
- `page.packet` is the scholar workspace
- `folio.render` is the presentation object

So yes: the pipeline should emit JSON here.
Not XML first.
If we need TEI/XML later, we derive it from the same structured JSON.

Current design target:

- this HTML folio design is the gold standard
- one book should assemble as:
  - title page
  - contents page
  - folio sequence
  - ending page
- the book owns the cover
- individual folios inside a book should open directly into the reading spread
- a standalone folio render may still keep its own cover when viewed by itself

---

## 1. Why This Exists

The HTML folio templates should not scrape freeform markdown forever.

They should receive a stable object with the same shape every time:

- `cover`
- `content`
- `interpretation`

That makes the system:

- composable
- testable
- scalable to hundreds of folios
- renderer-agnostic

---

## 2. Position In The Pipeline

Recommended sequence:

`image -> canonical.page -> page.packet -> scholar pass -> folio.render -> html/pdf/site`

Where:

- `canonical.page` stores zones, text layers, layout, and provenance
- `page.packet` stores the working notes and local scholar files
- `folio.render` is the clean UI payload

---

## 3. Format Choice

Use:

- `JSON` as the primary render contract

Do not use:

- XML as the first-class render boundary

Reason:

- the UI wants a modern structured payload
- JSON is simpler to validate and easier to feed into HTML templates
- TEI/XML remains valuable as a downstream scholarly export, not the UI source of truth

---

## 4. Required Top-Level Shape

The folio render payload should contain:

- `artifact_type`
- `created_at`
- `doc_id`
- `page_id`
- `page_label`
- `book_title`
- `page_unit`
- `source_image_path`
- `cover`
- `spread`
- `navigation`

Canonical identifier:

- `folio.render`

---

## 5. Cover

The cover is its own template piece.

Required fields:

- `label`
- `title`
- `subtitle`
- `nav_hint`

This is not where manuscript ids should leak through in raw form.
Use display labels such as `Folio 4r`.

---

## 6. Spread

The spread is also a fixed assembly shape.

It has:

- `image`
- `content`
- `interpretation`

### `image`

Required fields:

- `folio_label`
- `source_label`
- `image_path`
- `caption`

### `content`

This is the default right-side face.

Required fields:

- `header_label`
- `header_title`
- `sections`

Typical sections:

- `Diplomatic Witness`
- `Direct Translation`

### `interpretation`

This is the flipped/apparatus face.

Required fields:

- `header_label`
- `header_title`
- `sections`

Typical sections:

- `Interpretation`
- `Notes`
- `Names and Terms`
- `Open Questions`

---

## 7. Section Shape

Each section should be a reusable template block:

- `kind`
- `title`
- `body_html`
- `wide`

This is the current practical version.

Later, `body_html` can become a richer block tree if needed.
For now, the important part is that the folio renderer receives
predictable assembled sections instead of mining arbitrary markdown.

Current operating rule:

- each packet heading becomes one typed `folio.render` section
- subheadings stay nested inside that section
- witness headings become `kind = witness`
- translation headings become `kind = translation`
- interpretation headings become `kind = interpretation`
- notes, terms, and questions keep their own kinds

---

## 8. Scaling Rule

To build a 500-page manuscript the same way every time:

1. each folio emits one `folio.render.json`
2. each folio page is rendered from the same templates
3. the book/site layer only adds navigation and indexing

So:

- no hand-authored folio HTML
- no per-page special casing in the renderer
- only structured payload + shared templates
- the book builder owns title/contents/ending pages
- the folio renderer owns the cinematic folio experience itself

---

## 9. Current Practical Rule

Right now:

- witness and translation are still authored in packet markdown
- interpretation, notes, terms, and questions are still packet markdown

But the renderer should convert that into `folio.render.json` first.

That makes the markdown files authoring inputs, not UI contracts.
