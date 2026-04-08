# Reader Product

Purpose: define the actual book experience Palimpsest should optimize for.

This is not the global "Library of Alexandria" shell yet.

First we need one excellent manuscript reader.

The reader should feel like:
- a real book
- image-first
- scholarly
- calm
- downloadable
- web-native

---

## 1. Product Boundary

The reader product is:

`one manuscript -> one static web book`

Each manuscript book should contain:
- a manuscript cover page
- a contents page
- one folio page per page unit
- an ending page

Everything else is secondary.

Not first:
- a giant global portal
- 3D library navigation
- social features
- search across the world

Those can sit on top later.

---

## 2. Reader Goal

A reader should be able to:
- open a manuscript
- turn through it front to back
- zoom into the source folio
- read the diplomatic witness
- read a direct translation
- reveal interpretation only when wanted
- see exactly where text came from on the image
- download the result as a bundle

If that works well, Palimpsest already has a real product.

---

## 3. Core Reader Shape

The canonical experience should be:

1. `Cover`
   - manuscript title
   - source / shelfmark
   - short descriptor
   - progress / readiness note
   - entry action

2. `Contents`
   - list of folios in order
   - readiness markers
   - optional section markers later

3. `Folio page`
   - source image on the left
   - reading panel on the right
   - interpretation panel hidden until selected

4. `Ending`
   - end of manuscript
   - return to contents
   - download options

This is the book.

---

## 4. Folio Interaction Model

Each folio should behave like this:

### Left side
- raw source image
- zoom
- pan
- optional reset view
- region overlays hidden by default

### Right side default
- witness transcription
- direct translation

### Right side alternate views
- interpretation
- notes
- terms
- questions

### Linking behavior
- hover a witness block -> highlight only its source region on the image
- hover an image region -> highlight only the matching witness block
- click a witness block -> pin the highlight
- click a region -> scroll the text pane to the matching block

Important:
- do not show all boxes all the time
- overlays should appear only on interaction or in an explicit debug mode

---

## 5. Display Priorities

The folio should optimize for this order:

1. image legibility
2. witness fidelity
3. witness <-> image provenance
4. direct translation
5. interpretation

Interpretation is important, but it should not visually dominate the witness.

The right panel should default to:
- witness
- direct translation

Interpretation should be a deliberate selection, not always-on noise.

---

## 6. Binding Model

The reader needs three structured layers:

1. `page.assembly`
   - canonical region-first witness object
   - provenance and box ownership

2. `translation.bindings`
   - direct translation aligned to witness units
   - not part of reconstruction

3. `folio.render`
   - final UI object for one folio page

That means:
- reconstruction should stop at witness
- translation should be generated after witness
- interpretation should be generated after translation

This keeps the binding honest.

---

## 7. Unification

Right now there are two web paths:
- packet folio site
- witness reader site

That split should not survive.

The final product should be:

`one unified manuscript web builder`

Modes can still differ internally:
- witness-only
- packet-backed scholarly

But the user should see one coherent reader product.

The final builder should assemble:
- cover
- contents
- folios
- ending
- metadata
- downloadable assets

from one manuscript-level manifest.

---

## 8. Zoom And Image Behavior

Zoom is mandatory.

Minimum acceptable behavior:
- wheel / pinch zoom
- click-and-drag pan
- reset button
- fit-to-page default

Optional later:
- region jump
- mini-map
- deep zoom tiles

We do not need IIIF tile serving first.

A good image-viewer layer over the static folio image is enough for the first product.

---

## 9. Region Highlighting

Region highlighting should be:
- sparse
- interactive
- reversible

Default state:
- no overlays visible

On hover:
- show only the one matching region

On click:
- pin only the selected region

Optional debug mode:
- show all regions
- show labels
- show box roles

The default reading experience should never look like an annotation dump.

---

## 10. Translation Pass

Translation should be its own stage.

Pipeline:

`layout-probe -> region-read -> section-resolution -> validate -> box-cleanup -> assemble`

Then:

`assemble -> translation pass -> interpretation pass -> folio.render`

Translation should operate on:
- deduped witness blocks
- clean region ownership
- stable reading order

Not on raw overlapping crops.

The translation output should be:
- close
- local
- restrained

It should not become essay-like commentary.

---

## 11. Interpretation Pass

Interpretation should be selectable, not compulsory.

Interpretation content should be grouped into:
- what this page is doing
- direct evidence
- probable inference
- links to adjacent pages
- terms / names
- questions

This content belongs in a separate reader face or tab.

Default reader state should not force interpretation ahead of witness.

---

## 12. Download Product

Every manuscript should be downloadable.

Minimum bundle:
- static HTML book
- assets/images
- folio render JSON
- page assembly JSON
- witness markdown
- translation markdown
- interpretation markdown when available

Possible formats:
- `site.zip`
- `reader.zip`

Optional later:
- downloadable static site bundle
- packet bundle
- TEI export

The downloadable static site is the first real "virtual library" artifact.

---

## 13. What We Optimize Next

The next product work should focus on:

1. unify packet site and witness reader
2. add zoom/pan to the folio image
3. make region highlighting hover-only by default
4. keep interpretation behind a deliberate toggle
5. add translation bindings cleanly from assembled witness units
6. produce one manuscript-level static site bundle

Not next:
- 3D library shell
- global search
- advanced social/discovery UI

---

## 14. Reader Manifest

The manuscript reader should eventually build from one manifest like:

```json
{
  "artifact_type": "manuscript.reader",
  "doc_id": "vatican_borg_cin_361",
  "title": "Vatican Borg. cin. 361",
  "source": "Biblioteca Apostolica Vaticana",
  "cover": {
    "title": "Vatican Borg. cin. 361",
    "subtitle": "Multilingual Jesuit-era Chinese manuscript"
  },
  "contents": [
    {"page_id": "f004r", "href": "folios/f004r/index.html", "status": "ready"}
  ],
  "downloads": [
    {"kind": "site_zip", "href": "downloads/site.zip"}
  ]
}
```

This becomes the clean bridge between reconstruction and publication.

---

## 15. Standard

The standard is simple:

If a scholar or curious reader can open the manuscript, turn pages, inspect the source closely, understand where the transcription came from, and selectively reveal translation and interpretation, then the reader is good.

If it feels like a pile of dev artifacts, it is not good enough.
