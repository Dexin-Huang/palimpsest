# Reconstruction Techniques

This is the current technique ladder for region-first page reconstruction.

The benchmark gate is:

- [f004r benchmark](D:\Projects\palimpsest\benchmarks\pages\vatican_borg_cin_361\f004r\README.md)

We should improve the pipeline in this order and only fan changes out once `f004r` mostly passes.

## Canonical pipeline

`layout-probe -> region-read -> section-resolution -> box-cleanup -> assemble -> render-html`

## Technique ladder

### 1. Coarse semantic boxes

Use a cheap multimodal pass to produce only the large semantic regions:

- header
- main text
- marginalia
- page number
- footer / ignore

Rules:

- boxes should be inclusive enough to avoid clipping visible text
- boxes do not need to be disjoint
- this pass should stay coarse and cheap

### 2. Full transcription per region

For each region, ask only for:

- the full detailed transcription of that region

Rules:

- no translation in this pass
- no summary in this pass
- no invented punctuation
- use `[]` for visible candidate readings
- use `()` for supplied restoration of damaged text

This keeps the top of the funnel simple.

### 3. Canonical section assignment

Take the raw region transcriptions and assign one canonical `box -> text`.

This is where the system decides:

- what the header really is
- what the main body really is
- whether a page number should render
- whether a footer should be ignored

This should operate at the level of whole boxes, not pairwise lines.

### 4. Targeted box-pair cleanup

Only if needed, send implicated neighboring pairs to a cheap model.

Typical pairs:

- `header + main_text`
- `main_text + marginalia`
- `page_number + nearby box`

This stage should answer:

- does any text belong in the other box?
- what is the cleaned text for each box?

This is the current best tool for:

- header spill into main text
- Chinese spill into Latin marginalia
- page-number junk

### 5. Deterministic validators

Add cheap rule-based checks before or after cleanup:

- `header_spill_into_main`
- `marginalia_script_contamination`
- `page_number_noise`
- `truncation_risk`
- `duplicate_short_line_spill`

These should decide whether a page needs repair without manual inspection.

### 6. Lightweight visual QA

If deterministic validators flag a page, run one cheap image-aware QA pass.

Inputs:

- full page image
- overlay boxes
- current box texts

Outputs:

- issue list
- implicated box pairs
- whether targeted repair is needed

This is the scalable way to catch subtle boundary mistakes like:

- `民立君` collapsing to `民主`
- `恒性` drifting toward `惺性`

### 7. Classical CV refinement

Use CV as a boundary prior, not as the final reader.

Useful techniques:

- connected components / blob extraction
- line-fragment grouping
- boundary ink density
- script-shape heuristics

Best use:

- detect clipped edges
- detect spill beyond a box edge
- detect whether a blob belongs with header, body, or marginalia

Do not use CV alone to decide final historical characters.

### 8. Escalation only for ambiguous pages

If validators and repair still fail, escalate:

- rerun implicated boxes
- split one dense region
- or send the page to manual review

Most pages should not need this.

## Iteration order for `f004r`

1. Fix header/main boundary ownership.
2. Fix marginalia contamination.
3. Fix page-number cleanup.
4. Add truncation / clipped-edge detection.
5. Add validators so the page can fail automatically.
6. Only then apply the same logic to the rest of the manuscript.

## Success shape for `f004r`

The page should resolve to:

- 1 right header
- 1 right main Chinese text block
- 1 left header
- 1 left main Chinese text block
- 1 right Latin marginalia block
- 1 page number
- 1 ignored modern footer

The witness-bearing boxes are:

- 2 headers
- 2 large Chinese body blocks
- 1 marginalia block

If we do not get this shape reliably, the pipeline is not ready to scale.
