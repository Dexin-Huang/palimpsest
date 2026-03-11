# Model Strategy

Palimpsest should use different models for different lanes.

The main mistake to avoid is using one model for everything.

## Current Recommendation

- `triage / scouting`: `gemini-3.1-flash-lite-preview`
- `page reading`: `gemini-3.1-flash-lite-preview`
- `witness extraction`: `gemini-3.1-flash-lite-preview`
- `reconstruction / image editing`: `gemini-3.1-flash-image-preview`

Reason:

- `flash-lite` is the default operating point because the quality/cost curve is
  the most attractive for the active pipeline
- reconstruction image work still belongs on the image-preview lane

## Current Operating Rule

The pipeline now defaults to `gemini-3.1-flash-lite-preview` for the active
reading lanes.

Use Lite when the task is:

- deciding whether a page is worth a deeper pass
- proposing coarse regions
- labeling crops
- smoothing or reconciling small local boundaries
- reading witness text in the canonical region-first pipeline

Use the image model when the task is:

- reconstruction / image generation
- visual editing or synthetic restoration

## Fine-Tuning Threshold

Do not fine-tune just because one page is hard.

Start fine-tuning only when all of the following are true:

1. the prompt shape is already stable
2. the same failure mode repeats across a coherent page family
3. the API model is clearly better than the local/open baseline
4. the page family is large enough to amortize the work

Practical trigger:

- below `~500` high-quality page examples in one lane: do not fine-tune
- around `500-1,000` pages: small LoRA experiments become reasonable
- around `3,000-10,000` pages: serious specialist adapter territory
- above `10,000+` pages in one coherent family: strong case for a durable local worker

These are page-level counts, not manuscripts.

## What Counts As One Lane

A lane should be structurally coherent, for example:

- Jesuit Chinese bilingual philosophy pages
- Latin alchemical recipe pages
- Chinese printed cosmology / diagram commentary
- tabular administrative records

Do not mix everything into one fine-tune set.

## What To Fine-Tune First

Do not fine-tune a giant general model from scratch.

First choice:

- one shared open-weight VLM base
- LoRA adapters per lane
- frontier API as teacher and fallback

That keeps the system simple:

- prompts and routing do most of the work
- adapters handle repeated page families
- the frontier model remains the judge / teacher on hard cases

## Readiness Checklist

Fine-tuning is justified only when:

- you have a frozen benchmark set
- you can score before/after quality on that set
- you have at least one stable prompt baseline
- you know which lane you are adapting for
- the lane is big enough to matter

If those are not true yet, keep iterating on prompts and routing.
