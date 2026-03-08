# Borg.cin.361 copy progress

Date: 2026-03-08

Goal: build a full witness copy of the manuscript slowly, using the new
`page read -> page synthesize` workflow where possible.

## Current status

### Completed

- `f001r`
  - output: `experiments/f001r_witness_v1/f001r_reading.md`
  - result: this is not manuscript content; it is the Vatican digital cover /
    shelfmark page
- `f004r`
  - content crop:
    - `experiments/f004r_content_crop_v1/f004r_content.jpg`
  - witness:
    - `experiments/f004r_content_crop_v1/read_flash_v1/f004r_content_reading.md`
  - result:
    - full-page witness stalled
    - simple content crop removed the footer and most dead margins
    - cropped witness completed cleanly with `gemini-3-flash-preview`
- `f005r`
  - content crop:
    - `experiments/f005r_content_crop_v1/f005r_content.jpg`
  - witness:
    - `experiments/f005r_content_crop_v1/read_flash_v1/f005r_content_reading.md`
  - result:
    - same pattern as `f004r`
    - cropped witness completed cleanly with `gemini-3-flash-preview`
- `f004r-f005r`
  - synthesis:
    - `experiments/f004r_f005r_synthesis_v1/section_synthesis.md`
  - result:
    - early political-theological section around Heaven, rulership, Shangdi,
      and classical exempla

### Opening hard pages

- `f002r`
  - full-page witness attempt with `gemini-3-flash-preview`: stalled
  - full-page witness attempt with `gemini-3.1-flash-lite-preview`: stalled
  - region scout completed successfully:
    - `experiments/f002r_regions_v1/regions.json`
  - scout result:
    - one main `deep_dive` Chinese text block
    - one Latin header region
    - one small marginal note region
  - crop-level witness attempts were also slow enough to stop manually

- `f003r`
  - full-page witness attempt with `gemini-3-flash-preview`: stalled

## Practical conclusion

The opening folios are a different difficulty class from the mid-manuscript
sections that already worked well (`f150r-f152r`, `f200r-f202r`).

For this manuscript, the full-copy lane should expect:

- easy or moderate pages in many later sections
- slower opening pages that may require crop-first witness capture
- opening scan images that are often spreads rather than clean single-page
  shots

The most useful simplification so far is:

1. crop out the Vatican footer and obvious dead space
2. run the normal witness prompt on the cropped manuscript area
3. only fall back to region experiments if crop-first fails

At least for `f004r-f005r`, this works better than both:

- full-page witness on the raw image
- the current `page regions` lane

The current region subsystem should not be trusted for this opening copy pass
yet:

- model-first regioning produced sensible labels but broken box geometry
- CV-first regioning overfit the bottom footer and labeled only noise

## Recommended continuation point

1. Keep `f001r` as the digital cover witness.
2. Treat `f002r-f003r` as the first hard-page cluster.
3. Use the `f004r-f005r` pattern on `f002r-f003r` before trying anything more
   complex:
   - make a simple manuscript-area crop
   - run the standard witness prompt on the crop
4. Continue forward with `f006r-f007r` using the same crop-first rule.

## Important context

This does **not** mean the manuscript is a bad target.

It means the opening section is visually denser and less cooperative with the
current page-level witness prompt than the already validated later sections.
