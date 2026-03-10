# Page Packet

Purpose: define the scholar-facing working bundle for one page unit.

`page.packet` replaces the earlier "carcass" idea.

It is a better name because it is:
- neutral
- elegant enough to keep
- concrete enough to use in code
- broad enough to cover single pages and spread images

The page packet is not the canonical truth object.

That remains:
- source image
- `canonical.page`

The page packet is the working bundle a scholar-agent moves through front to
back while building:
- witness
- notes
- translation
- interpretation
- edition output

---

## 1. Position In The Workflow

Recommended sequence:

`image -> page prepare -> page.packet -> witness fill -> section synthesis -> edition render`

Where:

- `page prepare` is deterministic cropping / cleanup
- `page.packet` is the scholar workspace
- `witness fill` is the evidence-first pass
- `section synthesis` adds broader translation and interpretation
- `folio.render` becomes the structured presentation object
- `edition render` builds human-facing output such as HTML or PDF from `folio.render`

---

## 2. Design Rule

The page packet should behave like a careful scholar's folder for one page.

It should contain:
- the prepared image
- the current witness
- local notes
- translation draft
- interpretation draft
- terms / glossary notes
- open questions
- hot continuity references to the prior handoff and local window
- a minimal edition template

It should not:
- replace `canonical.page`
- hide provenance
- encourage essay-like drift before the witness exists

---

## 3. Artifact Name

Canonical artifact name:

- `page.packet`

Recommended CLI:

- `python -m palimpsest page packet --image ...`
- `python -m palimpsest scholar packet --packet ...`

Why not `carcass`:
- too dead-sounding
- too provisional
- wrong tone for a scholar-facing workflow

Why not `dossier`:
- too finished
- better reserved for manuscript-level or section-level outputs

Why `packet`:
- a bounded bundle
- easy to understand
- works for both humans and agents

---

## 4. What The Claude Agent Should Do

The Claude agent SDK should power the packet workflow after raw page reading,
not replace witness extraction itself.

Good responsibilities:
- fill notes
- refine translation
- distinguish direct evidence from probable inference
- accumulate cross-page observations
- keep a running glossary
- maintain the edition-facing files

Bad responsibilities:
- freestyle OCR
- invent witness text
- silently rewrite the diplomatic layer

So the clean split is:

- Gemini or another multimodal reader: raw witness pass
- Claude agent SDK: scholar pass over the packet

Dedicated command:

```bash
python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task advance \
  --witness library/<doc_id>/experiments/<page>_reading/<page>_reading.md
```

Common dedicated tasks:
- `fill_witness`
- `annotate`
- `translate`
- `interpret`
- `render_edition`

This is intentionally separate from the general `agent`, `agent-edit`, and
other grunt-worker commands.

---

## 5. Packet Contents

Suggested file layout:

```text
experiments/<page>_packet/
  packet.json
  packet_meta.json
  edition_render_meta.json
  prepared/
    <page>_prepared.jpg
    prepare_meta.json
  witness.md
  notes.md
  translation.md
  interpretation.md
  terms.md
  questions.md
  folio_render.json
  edition_spread.tex
  edition_spread.pdf
```

This is deliberately small.

---

## 6. Packet File Grammar

The scholar lane should not write arbitrary markdown.

Each packet file should keep a stable heading grammar so the render layer can
assemble `folio.render.json` deterministically.

Working rule:
- each `##` heading becomes one render section
- `###` headings stay nested inside the nearest parent section
- witness and translation may rename unit headings to visible labels such as
  `Left Column`, `Upper Register`, or `Main Panel`
- witness and translation should also use stable internal markers so structured
  content can be compiled into `folio.render`

Required shapes:

- `witness.md`
  - one or more witness-unit `##` headings
  - inside each witness unit:
    - `**Header**: ...`
    - `**Page Number**: ...` when visible
    - optional `**Marginalia** (script, position):` plus fenced block
    - `**Main Text**` followed by diplomatic witness only
  - final `## Layout Notes`
- `translation.md`
  - one or more translation-unit `##` headings
  - each translation unit should share the witness unit title before the colon,
    e.g. `## Left Column: Heaven Endows Constant Nature`
  - inside each translation unit:
    - `**Main Text**` followed by close translation paragraphs only
  - `## Translation Notes`
  - `## Interpretive Restraint`
- `interpretation.md`
  - `## What This Page Is Doing`
  - `## Direct Evidence`
  - `## Probable Inference`
  - `## Connection to Adjacent Pages`
  - `## Interpretive Restraint`
- `notes.md`
  - `## Layout`
  - `## Text Structure`
  - `## Citations And Allusions`
  - `## Marginalia And Non-Main Text`
  - `## Uncertainty Markers`
- `terms.md`
  - `## People And Beings`
  - `## Works And Texts`
  - `## Places And Institutions`
  - `## Technical Terms`
- `questions.md`
  - `## Witness Uncertainties`
  - `## Cross-Page Checks`
  - `## Research Follow-Ups`

This is intentional:
- packet markdown remains authorable
- `folio.render.json` is the structured UI artifact
- the compiler should not need to guess where witness, translation, terms, and
  interpretation live

---

## 7. `page.packet` JSON Shape

Minimum fields:
- `artifact_type`
- `created_at`
- `doc_id`
- `page_id`
- `page_unit`
- `source_image_path`
- `prepared_image_path`
- `files`
- `continuity`
- `workflow`
- `open_questions`

### `page_unit`

Allowed values:
- `page`
- `spread`

This matters because early Vatican scans are often spread images rather than
clean single-page shots.

### `files`

Each file entry should include:
- `kind`
- `path`
- `status`
- `note`

Allowed status values:
- `empty`
- `started`
- `draft`
- `reviewed`
- `complete`

Typical file kinds:
- `witness`
- `notes`
- `translation`
- `interpretation`
- `terms`
- `questions`
- `edition_tex`
- `edition_pdf`

### `workflow`

Minimum fields:
- `primary_reasoner`
- `witness_model`
- `synthesis_model`
- `next_action`

Typical next actions:
- `fill_witness`
- `fill_notes`
- `draft_translation`
- `draft_interpretation`
- `review_terms`
- `review_questions`
- `prepare_section_synthesis`
- `render_edition`
- `complete`

### `continuity`

Hot continuity fields:
- `previous_packet_path`
- `previous_handoff_path`
- `window_synthesis_path`

These should point at the smallest useful prior state.

The scholar packet workflow should load them automatically when present, so
front-to-back reading does not depend on manually re-supplying the same context
files on every run.

---

## 8. Page Packet Vs Section Synthesis

`page.packet` is page-local.

It should answer:
- what is visibly on this page?
- what do I need to note before moving on?
- what remains unresolved here?

`section synthesis` is cross-page.

It should answer:
- what is this run of pages doing?
- what should be translated or interpreted in context?
- which terms and arguments stabilize across multiple folios?

That separation keeps the workflow disciplined.

---

## 9. Edition Layout

The first edition layout should stay simple.

Facing-page model:
- left side: witness / diplomatic text
- right side: translation and interpretation

The page packet should generate a minimal LaTeX skeleton for this.

Important:
- LaTeX is a render target
- the packet is the working source bundle
- `tectonic` is the default local renderer for packet editions
- font policy prefers environment override, then bundled fonts, then system fallback

---

## 10. Practical Use

Example:

```bash
python -m palimpsest page packet --image library/<doc_id>/images/f004r.jpg
python -m palimpsest page read --image library/<doc_id>/images/f004r.jpg
python -m palimpsest scholar packet --packet library/<doc_id>/experiments/f004r_packet/packet.json --task render_edition
python -m palimpsest page render --packet library/<doc_id>/experiments/f004r_packet/packet.json
python -m palimpsest page synthesize --input ...
```

Then the scholar-agent can work through:
- `witness.md`
- `notes.md`
- `translation.md`
- `interpretation.md`
- `terms.md`
- `questions.md`

from front to back through the manuscript.

That is the right abstraction for "the agent works like a scholar."
