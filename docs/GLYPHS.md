# The Glyph Bench

Character-level geometry for the Chinese lane: forced alignment, scribal
exemplars, hand fonts, and few-shot glyph generation. This document is the
complete design. Each module is a station-shaped part — declared inputs, one
artifact out, a deterministic acceptance metric — so any module can be
removed, replaced, or upgraded without touching its neighbors. Changing a
tire never means rebuilding the axle.

## Why this exists

The read station produces text; it does not say WHERE on the page each
character sits, and it cannot say how CONFIDENT it is per character. Both
gaps close with one observation: for Chinese regular script, transcription +
image is a forced-alignment problem, not a recognition problem. We already
know the character sequence; we only need to bind it to ink blobs. That
binding yields, in order of increasing ambition:

1. per-character coordinates (reader tap-to-ink; `page_assembled.alignment`
   is already reserved for this),
2. deterministic verification (blob count vs transcription length catches
   dropped/hallucinated characters with zero model calls),
3. localized triage for emend ("col 14 char 9 matches its glyph poorly"),
4. geometric seam registration (same physical columns identified across
   overlapping captures even where the two transcriptions disagree),
5. the scribe's own glyph shapes — exemplars, a hand font, and eventually
   a generative model of the hand.

Scope: zh lane, regular and semi-regular script. Explicitly out of scope:
Latin cursive (connected script defeats blob segmentation) and full 草書.
Degradation (stains, bleed, tears) is in scope and drives the design.

## Principles

- **Pure code before models.** M1–M3 make no model calls. M4 starts as a
  cheap call to an existing image model and earns a trained model only by
  failing measurably.
- **Attested beats generated, always.** A glyph cropped from the scribe's
  ink is evidence; a glyph synthesized in the scribe's style is a
  hypothesis. Every template carries its tier, and every downstream
  consumer (especially the emend apparatus) must cite which tier it used.
  This is the ink-primacy rule extended to typography.
- **Every module has a number.** A replacement module is judged by the
  module's own acceptance metric, on the same fixtures, before it plugs in.
  No metric, no swap.
- **Artifacts, not calls.** Modules communicate only through fingerprinted
  artifacts on disk. That is what makes them tires.

## Module map

```
page_image_clean ──┐
page_transcription ┴─▶ M1 align ─▶ page_alignment ─▶ M2 exemplars ─▶ glyph_exemplars
                                                          │
                                        ┌─────────────────┼──────────────┐
                                        ▼                 ▼              ▼
                                  M3 hand_font      M4 glyph_gen    M5 consumers
                                  hand_font.otf     glyph_synthetic (emend, seams,
                                                                     reader, rejoin,
                                                                     training data)
```

### M1 — align (page grain, pure code)

Consumes `page_image_clean` + `page_transcription`; produces
`page_alignment`:

```json
{"columns": [{"bbox": [x,y,w,h], "chars": [
    {"ch": "玄", "bbox": [x,y,w,h], "confidence": 0.93,
     "method": "blob|merged|split|interpolated"}]}],
 "stats": {"transcribed": 1613, "boxed": 1402, "count_mismatch_columns": 3}}
```

Method: binarize → connected components → column clustering (vertical
projection; scroll columns are near-parallel) → per-column ordered
alignment of blob sequence to character sequence via dynamic time warping
on vertical position and size (DTW absorbs merged blobs from ink bleed and
split blobs from damage; bag-of-blobs matching is explicitly rejected —
order is the signal). Small interlinear glosses route to a secondary column
pass keyed by glyph height, or are marked unaligned — never force-fit.

Acceptance: boxed fraction and per-column count-mismatch rate on the
P.3477 golden fixtures; regressions fail CI. The count-mismatch stat also
feeds `evaluate` as a new deterministic read-quality metric.

### M2 — exemplars (manuscript grain, pure code)

Consumes all `page_alignment` + `page_image_clean`; produces
`glyph_exemplars`: per character, every confident undamaged crop, size- and
skew-normalized, with quality scores; an index records instance count and
cross-instance self-similarity. Multi-exemplar by design — a scribe is not
deterministic, and matching against the instance cloud beats matching
against any collapsed mean.

Acceptance: exemplar purity — cross-instance similarity within a character
must exceed similarity across characters (a confusion audit catches
alignment errors leaking into the library).

### M3 — hand_font (manuscript grain, pure code)

Consumes `glyph_exemplars`; produces `hand_font.otf` + coverage report:
cleanest exemplar per character, binarized, vectorized (potrace), assembled
with fontTools; the scribe's 俗字 variants become OpenType alternates, not
corrections. Coverage is complete by construction for THIS manuscript's
inventory; everything else falls back or waits for M4.

Uses: rendering candidate readings for damaged spans in the scribe's own
hand; display face for the reader/EPUB; the content-reference input to M4.

Acceptance: round-trip score — rendered glyphs re-matched against held-out
exemplar instances must beat a base-font control by a stated margin.

### M4 — glyph_gen (manuscript grain, model, pluggable executor)

Consumes `glyph_exemplars` (+ variant tables for 俗字 forms); produces
`glyph_synthetic`: in-style glyph images for characters with no clean
attested exemplar. Tier is stamped `generated` on every output.

Executor ladder, in factory style (same pattern as `executor: codex|omp`):
1. `image-model` — few-shot prompt to the configured image-generation lane
   (exemplar grid + target character). Zero training. The baseline.
2. `trained` — a dedicated few-shot font-generation model (FontDiffuser
   class), fine-tuned on aggregate Dunhuang exemplars, per-scribe style
   embedding. Built ONLY if the baseline fails its acceptance test.

Acceptance (both executors, same harness): hold out attested characters,
generate them from the remainder, score against the real ink. The score is
a per-manuscript number; below threshold, the synthetic tier is disabled
downstream for that manuscript — the system degrades to attested-only
rather than matching against bad hypotheses.

Known failure mode, designed against: the generator works from a modern
reference glyph, so a scribe's structurally different 俗字 can be
"generated over." Variant tables are inputs, generated variants are scored
alongside standard forms, and no synthetic match ever outranks attested
testimony (see Principles).

### M5 — consumers

- **emend**: new evidence class `ink (partial, exemplar N=k)` — surviving
  strokes of a damaged graph scored against attested exemplars (and, tier-
  labeled, against synthetic hypotheses). Localized triage worklist from M1
  confidence replaces text-only suspicion.
- **seams**: blob-constellation registration across adjacent captures;
  text-independent overlap detection backing up (eventually replacing) the
  fuzzy text match in `seams.py`.
- **reader**: `page_assembled.alignment` filled; tap a character, see ink.
- **rejoining (future)**: the M4 style embedding doubles as a scribe
  fingerprint; run across collections to propose fragment joins. This is an
  Ariadne-grade relation and gets its own design when we get there.
- **training data (future)**: render text in the hand over aged grounds as
  read-model fine-tuning data, per-scribe.

## Build order and status

| Stage | Module | Cost | Status |
|---|---|---|---|
| 1 | M1 align + golden fixtures on P.3477 | pure code | next |
| 2 | M2 exemplars | pure code | after M1 |
| 3 | M3 hand_font.otf | pure code | after M2, cheap by-product |
| 4 | M4 baseline (image-model executor) + held-out eval | model calls | after M3 |
| 5 | M4 trained executor | training project | only if stage 4 fails its number |
| 6 | M5 emend evidence + seam geometry + reader | integration | ships with M1/M2 partially |

Every stage ships value alone; no stage assumes the next exists.

## Scaling note

Per-manuscript artifacts come first. The aggregate play — one multi-hand
model, scribe = style embedding, cold-start improving with every scroll
processed — reuses the same artifacts (`glyph_exemplars` across the
library IS the training corpus). Nothing in M1–M3 needs rework for it.
