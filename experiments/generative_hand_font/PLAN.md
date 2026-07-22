# P.3477 Sparse Hand Generator — Master Experiment Plan

Status: **development evidence only** (2026-07-22). The writer-specimen queue
has enough human-attested characters for a non-qualifying engineering smoke,
but the independent held-out queue is empty. Model training remains blocked
until the active input path is source-preserving and the intended split is
fingerprinted.

## Product goal

Build an installable traditional-Chinese font from a sparse specimen of words or
glyphs written by one hand. Canonical Kai supplies the requested character's
content and topology; it must not remain the dominant visible style. A learned
writer transformation supplies the hand's proportions, component layout,
stroke construction, terminals, rhythm, spacing, and recurring
idiosyncrasies.

The operating model is:

```text
canonical Kai character K(c) + sparse writer specimen S(w)
-> writer identity z(w)
-> writer-conditioned glyph G(K(c), z(w))
-> validated outline at Unicode codepoint c
-> installable P3477-Generated.ttf
```

Equivalently, the observed manuscript provides sparse cells in a
`writer × character` matrix. Population pretraining supplies the prior needed
to fill unobserved cells; P.3477 adaptation estimates one writer column from a
small number of observed cells.

Generated pixels and outlines are reconstructions, never documentary evidence.
Authentic traced glyphs retain source-image provenance and remain distinguishable
from generated completions.

## Master decision

The experiment is valid, but its claim is deliberately narrow:

> Given a broadly pretrained Chinese glyph generator and a sparse set of
> human-attested P.3477 glyphs, can the model infer a transferable visual
> representation of this hand and generate character identities that were not
> present in the style references?

A positive result would establish a **synthetic style-imitation capability**.
It would not recover the writer's physical stroke order, prove authorship, or
reconstruct missing documentary ink. Sparse P.3477 samples identify a style
inside a population prior; they are not enough to train Chinese character
structure from scratch.

The program has two product uses:

1. **Source-linked re-typesetting.** Render the diplomatic or critical text in
   an explicitly synthetic P.3477-derived hand while every rendered character
   links back to its source crop, label decision, and provenance.
2. **Analysis by synthesis.** For a disputed source crop, render several
   candidate characters in the learned hand and test whether generated-candidate
   similarity improves correct-character ranking over canonical-font,
   geometric, OCR, and exemplar baselines.

The second use is an experiment, not an assumption. A writer generator enters
transcription only if held-out evidence shows that it reduces character error
without adding semantic stroke failures.

## Relationship to the manuscript pipeline

The current production path is:

```text
page_image_clean
-> read: Gemini returns ordered columns of diplomatic characters
-> align: geometry binds that fixed sequence to ink cells
-> survey / translate / assemble / reconstruct / reference / emend
-> publish: optional coordinate evidence
```

The current aligner is forced geometry, not traditional OCR. It does not test
whether a crop visually resembles its claimed character; its confidence is a
cell-height fit. Consequently, generated glyphs must never be trained from
automatic `page_alignment` labels. The invalidated first run demonstrates the
circular failure:

```text
Gemini guess -> geometric binding -> noisy pseudo-label
-> writer model learns the guess -> generated image appears to confirm it
```

The qualified future architecture is instead:

```text
immutable page image
├─> geometry-first unlabeled glyph inventory
└─> page-level ordered transcription candidates
              │
              v
       joint sequence alignment
              │
              v
  per-character evidence lattice
  ├─ crop-level OCR/VLM top-k
  ├─ human-attested same-hand exemplars
  ├─ canonical component/topology evidence
  └─ generated same-hand candidate similarity (experimental)
              │
              v
   adjudicated diplomatic transcription
              │
        survey / translate / emend
              │
              v
 source-linked synthetic re-typesetting
```

This experiment remains outside the production recipe. If analysis by
synthesis qualifies, integrating it would require either:

- a same-socket `read` ensemble variant that still consumes
  `page_image_clean + page_regions` and produces `page_transcription`; or
- architecture work for a new post-alignment evidence artifact and
  adjudication station before survey and translation.

The second option changes artifact inputs and shapes, so it is not authorized
by this experiment. It requires a separate contract/station design and its own
evaluation suite. Re-typesetting may first ship as a non-authoritative library
derivation from the published book and a fingerprinted writer model.

## Product contract

### Input

- a declared specimen budget measured in distinct characters and source words;
- source-image crops with character identity, bbox, page, line, and hash;
- canonical traditional-Chinese Kai glyphs for requested Unicode characters;
- a declared output repertoire.

The first development budgets are 8, 16, 32, and 64 distinct specimen
characters. Reference selection is frozen before evaluating a budget.

### Output

- `P3477-Generated.ttf`, with a valid Unicode `cmap` and font metrics;
- authentic observed outlines where a trusted specimen exists;
- writer-conditioned generated outlines for absent characters;
- a glyph-level provenance manifest identifying `authentic` versus `generated`;
- specimen sheets rendering isolated glyphs, words, and manuscript phrases;
- a terminal evaluation report with paired observations and failure cases.

The first executable repertoire is the 190-character inventory already frozen
by `scribe_template_retrieval`. Repertoire expansion follows only after writer
identity survives the held-out gate.

## What Kai means

Kai is content supervision, not appearance ground truth. It fixes:

- Unicode identity;
- component inventory and topology;
- basic stroke connectivity;
- recognizable traditional-character structure.

The writer transformation is expected to change:

- global width, height, slant, balance, and density;
- component scale, placement, compression, and spacing;
- stroke width distribution, curvature, joins, hooks, and terminals;
- writer-specific simplifications and recurring allographs when supported by
  the specimen.

A model that leaves correct-writer, no-writer, and wrong-writer outputs visually
interchangeable has failed even if a retrieval metric improves.

## Existing baselines and boundaries

`experiments/scribe_template_retrieval/` is preserved as the frozen weak-style
baseline. Its MX-Font run established a small measurable transfer signal, but
the generated glyphs remain visually source-font-dominant and are rejected as
a product result.

`experiments/hand_font/` is preserved as the authentic-outline baseline. It
already proves that verified P.3477 ink can be vectorized, mapped to Unicode,
and packaged into a TTF, but it cannot generate absent characters.

`experiments/generative_hand_font/` owns only the research program:

```text
attest.py          adapt P.3477 proposals to the reusable annotation service
benchmark.py       freeze human-attested specimens and held-out cases
clean_document.py prototype source-preserving page readability derivatives
glyph_alignment.py prototype glyph placement and refinement representations
smoke.py           run the bounded, explicitly non-qualifying harness smoke
adapt.py           fit the current MX-Font control without target leakage
generate.py        render the frozen output repertoire
build_font.py      combine authentic and generated outlines with provenance
evaluate.py        score identity, content, leakage, and font behavior
review.py          collect human-attested visual comparison evidence
```

No production recipe or station socket changes during this program. A generated
font cannot become gold or qualification authority, and it becomes an
experimental recognition signal only after Q3 passes. Model-development
commands are local; this plan authorizes no paid provider call.


## Worktree and artifact safety

Run this program only from the isolated
`palimpsest-exp-align-scribe-template-v1` worktree on an `experiment/*` branch.
Source, tests, plans, and append-only conclusions are versioned. The following
remain local and ignored:

- repository environments, downloaded upstream code, and checkpoints under
  `.venv/` or `tmp/`;
- generated crops, manifests, adapters, fonts, reports, and review pages under
  each experiment's `out/`;
- production workspaces, ledgers, and evaluation objects under `library/`.

Experiment commands must not edit a production recipe, invoke production
refresh, write to `library/factory.db`, or run proposal, promotion, or rollback
commands. Before and after a run, verify that the main `palimpsest` worktree is
clean and that the active branch is the isolated experiment branch.

## Research position

The research record establishes that writer-conditioned generation is real,
including sparse and Chinese settings, but not that damaged historical crops
can support the same claims:

| Work | Demonstrated capability | Relevance and boundary |
|---|---|---|
| [LF-Font](https://arxiv.org/abs/2009.11042) | Few-shot component-level Chinese font generation | Supports local content/style factorization; assumes clean font data. |
| [MX-Font](https://arxiv.org/abs/2104.00887) | Localized style experts, unseen-character and cross-lingual generation | Existing local control and checkpoint; visually source-font-dominant in the first P.3477 run. |
| [FontDiffuser](https://arxiv.org/abs/2312.12142) | One-shot diffusion generation with multiscale content and style-contrastive learning | Primary next architecture; trained on clean glyphs and has no historical-damage contract. |
| [CalliGAN](https://arxiv.org/abs/2005.12500) | Chinese component-aware calligraphy generation | Strong structural precedent, but its styles are learned categories rather than unseen sparse-writer adaptation. |
| [Few-shot Calligraphy Style Learning](https://arxiv.org/abs/2404.17199) | Writer-specific diffusion fine-tuning after broad pretraining | Closest adaptation precedent, but uses about 157 writer images rather than 8 sparse references. |
| [One-DM](https://arxiv.org/abs/2409.04004) | One-reference multilingual handwriting imitation | Establishes one-shot feasibility in benchmark domains; released Chinese support is incomplete and data are clean. |
| [OLHWG](https://arxiv.org/abs/2410.02309) | Full-line online Chinese handwriting for unseen writers, with layout separated from glyphs | Supports a later independent layout model; relies on online trajectory data unavailable for P.3477. |
| [DiffBrush](https://arxiv.org/abs/2508.03256) | Full-line offline Chinese handwriting with content/style decoupling | Strongest direct precedent for raster line synthesis; trained on large modern CASIA data. |
| [DiffInk](https://arxiv.org/abs/2509.23624) | Full-line online Chinese trajectory generation | Shows writer-conditioned content accuracy at scale, not recovery of trajectories from historical scans. |

Consensus across the primary implementations:

- a same-character canonical font raster is a standard content prior;
- one or several different-character references can carry style;
- content/style separation must be learned from a large population before
  sparse writer adaptation;
- fixed square canvases and geometric centering are common;
- darkness-centroid alignment to a same-character Kai raster is not standard;
- exact post-resampling ink-mass conservation is a local experimental
  constraint, not a literature convention;
- none of the surveyed systems makes automatic morphological gap repair into
  documentary or training truth;
- benchmark realism, OCR scores, FID, SSIM, LPIPS, or writer-classifier accuracy
  do not establish historical authenticity.

Therefore the experiment should answer whether the established pretrained prior
survives P.3477's domain shift, not whether glyph generation exists in general.
The generator is successful only when correct-writer style transfers to
character-disjoint real targets without importing Kai appearance or manuscript
damage.

## Causal benchmark

The primary unit is leave-character-out reconstruction:

```text
adaptation input: other P.3477 character identities only
target content:   canonical Kai for character X
prediction:       generated P.3477 form of X
ground truth:     untouched real P.3477 crop of X
```

Every held-out target identity is absent from adaptation targets and style
references. Cases are blocked by target character and manuscript line so
repeated crops cannot create false confidence.

### Compared systems

1. canonical Kai;
2. frozen, unadapted pretrained generator;
3. wrong-writer adaptation with the identical character budget;
4. P.3477 adaptation;
5. authentic exemplar or class medoid where the target was observed;
6. P.3477 font combining authentic and generated glyphs.

### Protected slices

- target absent from every specimen character;
- one-component versus multi-component characters;
- sparse versus dense character geometry;
- faint, fragmented, merged, and low-confidence source ink;
- characters whose components are seen but complete identity is unseen;
- characters whose components are also rare in the specimen;
- 8-, 16-, 32-, and 64-character specimen budgets.


## Program questions

The program separates four questions so one attractive specimen sheet cannot
answer all of them:

### Q1 — Sparse style identifiability

Can 8, 16, 32, or 64 distinct human-attested characters move a frozen
pretrained generator toward P.3477 more than an equal-budget wrong-writer
control, without changing character identity?

### Q2 — Unseen-character generation

Does the inferred style transfer to a real P.3477 character whose identity is
absent from every adaptation target and style reference? This is the core
feasibility claim. Training reconstruction alone does not answer it.

### Q3 — Recognition utility

Given a held-out real crop and a blinded candidate set containing the correct
character plus plausible visual confusions, does comparison to P.3477-style
generated candidates improve:

- top-1 and top-k character ranking;
- mean reciprocal rank;
- calibrated abstention at a fixed error rate;
- downstream diplomatic character error rate when used as one evidence score?

Controls are canonical Kai template matching, direct crop OCR/VLM, same-hand
exemplar retrieval, unadapted generation, and wrong-writer generation. The
generator must add information beyond the candidate list supplied by Gemini;
it cannot receive hidden gold or be asked only to confirm Gemini's first choice.

### Q4 — Re-typesetting utility

Can authentic and generated glyphs be combined into a readable, internally
consistent synthetic edition whose provenance remains obvious and whose
rendering does not conceal generation failures? This is a publication product,
not evidence for Q1–Q3.

## Metrics

No single pixel metric decides writer identity. The report retains separate
observations for:

- content correctness and catastrophic wrong-character structure;
- aligned image similarity to untouched real ink;
- writer-feature distance to P.3477 versus wrong-writer references;
- distance to the Kai scaffold, measuring source-style leakage;
- component geometry, foreground density, aspect ratio, and stroke-width
  distribution;
- same-character real-to-real variation where repeated authentic instances
  permit calibration;
- contour validity, clipping, coverage, and font rendering failures;
- blinded visual preference on a fixed specimen sheet.

Recognition utility is reported separately from visual generation quality:

- candidate-set top-1/top-k accuracy and mean reciprocal rank;
- correct-candidate score margin over the strongest confusable negative;
- risk/coverage curve for abstention;
- diplomatic character error rate for a fixed downstream adjudicator;
- error categories: wrong radical, added/deleted semantic stroke, variant-form
  confusion, damage imitation, Kai leakage, and unbound source ink.

## Development success gate

A writer-adapted model advances through Q1–Q2 only if all of the following hold
on frozen held-out character identities:

1. P.3477 adaptation beats both unadapted and wrong-writer generation on the
   paired writer-identity score with a positive 95% character-block-bootstrap
   interval;
2. at least 70% of held-out targets are closer to untouched P.3477 ink than the
   canonical Kai scaffold is;
3. writer adaptation improves aligned reconstruction distance by at least 10%
   over the unadapted generator without reducing content correctness by more
   than two points;
4. for repeated characters, generated-to-real distance is no more than twice
   the median real-to-real distance after translation and scale alignment;
5. a blinded comparison selects the correct-writer generation over wrong-writer
   and unadapted controls in at least 80% of reviewed targets;
6. catastrophic malformed or wrong-character outputs remain below 1%;
7. every output records model, checkpoint, specimen, target, source scaffold,
   and authentic/generated provenance.

Q3 analysis by synthesis advances toward a separate pipeline experiment only
if:

8. correct-character ranking has a positive paired held-out gain over the
   strongest non-generative visual baseline;
9. adding the frozen generator score reduces diplomatic character error rate
   for the fixed adjudicator without regressing a protected slice;
10. the generator introduces no new wrong-radical, semantic-stroke, or
    high-confidence invented-character error.

Q4 publication succeeds only if the generated TTF passes fontTools validation,
reloads through FreeType/PIL, maps every declared codepoint, renders the frozen
phrase suite without clipping or missing glyphs, and exposes provenance for
every authentic and generated character.

Failure of writer-identity gates stops model expansion. A visually attractive
Kai derivative does not pass. Failure of Q3 does not invalidate a useful
re-typesetting product; it prohibits using that product as recognition evidence.

## Model development program

Each rung changes one causal factor and preserves a terminal record. A later
model does not rewrite an earlier failed checkpoint, split, or report.

### Representation contract

The model must not overload one binary image with incompatible meanings:

1. **Immutable evidence:** native accepted crop, source image hash, bbox,
   human-label event, and accepted-crop hash. It is never modified.
2. **Geometry support:** a threshold/component mask used only to estimate
   bounds, moments, sensitivity, and the declared affine transform. It is not a
   reconstruction target.
3. **Style-encoder input:** background-normalized, continuous P.3477 darkness
   on the standard canvas. Translation plus one isotropic scale are permitted
   only in a fingerprinted ablation. No rotation, shear, nonuniform stretch, or
   clipping.
4. **Reconstruction target:** aligned continuous P.3477 darkness before any
   repair. Faint signal and uncertainty remain visible.
5. **Content input:** a same-character canonical Kai raster. Kai pixels enter
   only the content lane and never overwrite observed source pixels.
6. **Review-only repair:** every morphological close, bridge, or inferred
   stroke remains a sibling visualization. It feeds neither style encoding nor
   loss.

If smoothing is enabled, float darkness mass must be conserved within a
declared numerical tolerance and topology must remain unchanged across several
thresholds. Audit components, holes/Euler characteristic, component
correspondence, endpoints, junctions, centroid, border mass, and changed-pixel
locality. The current single-threshold aggregate count is insufficient.

### Experiment 0 — make the input path truthful

Before GPU adaptation:

1. Build one fingerprinted derived-glyph manifest consumed by `smoke.py`,
   `adapt.py`, and later model runners.
2. Recompute every accepted-crop hash immediately before decode and enforce the
   live annotation dataset/project/proposal fingerprints.
3. Remove non-isotropic square stretching and the mandatory `2×2`
   morphological close from the active training path.
4. Preserve three separately named preprocessing candidates:
   - `A`: conservative background normalization + aspect-preserving geometric
     fit and center;
   - `B`: `A` + Kai-relative darkness-centroid translation and one isotropic
     scale;
   - `C`: `B` + gated continuous-mass edge smoothing.
5. Reject, rather than merely report, clipping, border ink, non-finite
   transforms, mass failure, or smoothing topology change.
6. Record code, dependency, Kai font/render, parameters, transform, source, and
   output hashes before writing the terminal manifest.

The current `smoke.py` is not evidence for this pipeline: it reaches
`adapt.clean_writer_image`, which square-resizes, Otsu-binarizes, and closes the
crop. Until Experiment 0 is complete, a training run would test the wrong
transformation.

### Experiment 1 — cheapest architecture falsification

Use the already installed fingerprinted MX-Font checkpoint only to determine
whether the corrected data path and sparse-adaptation harness can move a style
representation at all:

1. Freeze a non-qualifying development split from human-attested
   `writer_specimen` records: 8 character identities for adaptation and 24
   character-disjoint identities for development evaluation.
2. Freeze reference identities, target identities, seed, wrong-writer records,
   label-shuffled control, preprocessing candidate, and output order before
   optimization.
3. Keep generator weights frozen and fit only the existing style/latent
   representation for the first run.
4. Compare zero-step, correct-writer, equal-budget wrong-writer, and
   label-shuffled conditions.
5. Report adaptation-character reconstruction separately from the 24 unseen
   identities. Only the latter speaks to transfer.

This smoke may reject the architecture or implementation. It cannot authorize
writer-generalization claims because its evaluation records come from the
development queue.

### Experiment 2 — primary sparse generator

FontDiffuser is the primary challenger after Experiment 1 proves the harness:

1. Reproduce the official frozen checkpoint at its native 96×96 model
   resolution and record all weight hashes.
2. Evaluate frozen one-shot inference first. Run every eligible style reference
   separately; aggregate only by a preregistered medoid or score rule rather
   than averaging pixels.
3. Evaluate multi-reference style-feature aggregation without parameter
   updates.
4. Only then add parameter-efficient adaptation:
   - freeze the content encoder;
   - freeze most of the diffusion U-Net;
   - train a small style projection or LoRA modules on style-conditioning
     attention;
   - use mixed precision, gradient checkpointing, batch size one, and gradient
     accumulation for the local 6 GiB GPU;
   - preserve the base checkpoint and write a separately fingerprinted adapter.
5. Use training tuples:

   ```text
   content:         Kai(c_target)
   style reference: P3477(c_style), where c_style != c_target
   target:          unrepaired continuous P3477(c_target)
   ```

6. Begin with the official diffusion noise objective. Add content-perceptual
   and same-writer/different-writer contrastive terms only as distinct
   challenger identities. Never hide several new losses inside one run.
7. Do not unfreeze the complete decoder or U-Net unless the small adapter fails
   cleanly and a new experiment explicitly tests the added memorization
   capacity.

MX-Font remains a frozen control. One-DM is deferred until its released
Chinese path is complete and reproducible. Full-line systems are research
references, not the first isolated-glyph implementation.

### Experiment 3 — analysis by synthesis

Test whether the winning writer generator improves recognition rather than
assuming that an attractive font does:

1. Build scorer-only candidate sets from independently adjudicated held-out
   crops. Each set contains the gold character, close component/radical
   confusions, similar-stroke-count negatives, and `unknown`.
2. Generate every candidate with identical seeds and style references.
3. Score source-to-candidate similarity with a fixed visual embedding plus
   explicit component/topology diagnostics.
4. Compare against:
   - Kai template similarity;
   - direct crop OCR/VLM top-k;
   - authentic same-hand exemplar retrieval;
   - unadapted and wrong-writer generators.
5. Fit no threshold on held-out cases. Development cases choose the similarity
   rule and abstention threshold; held-out cases measure top-k ranking and
   error/coverage.
6. Run a fixed downstream adjudicator with and without the generator score and
   compare diplomatic character error rate.

Advance toward pipeline integration only if the generator contributes a
positive paired held-out gain beyond the other visual evidence and introduces
no new wrong-radical or semantic-stroke errors.

### Experiment 4 — build and review the synthetic edition

Only after Q1–Q3 pass:

1. Keep reviewed authentic glyphs verbatim where available.
2. Generate the frozen absent-character repertoire with the winning adapter.
3. Convert masks to topology-preserving contours, retaining counters and holes.
4. Assemble `P3477-Generated.ttf` with glyph-level `authentic` or `generated`
   provenance and deterministic identities.
5. Render isolated, word-level, paragraph-level, and small-size specimens.
6. Render a source-linked manuscript passage in which every synthetic character
   opens the original crop and evidence record.
7. Evaluate layout separately. A typeface provides one normalized glyph per
   codepoint; it does not reproduce P.3477's column spacing, size variation,
   baseline drift, allographs, or physical writing process.

### Data and decision sequence

```text
development queue
-> tune representations and challengers
-> freeze winner, metrics, controls, and thresholds
-> independent held-out queue
-> reject | inconclusive | freeze research candidate
-> separate station/contract experiment if recognition integration is wanted
```

Qualification gold requires independent annotation and must never be opened for
adapter selection. The preferred standard is two independent labels plus
adjudication or expert labeling plus second-person audit. Failed cases stay in
the denominator; unknown remains unknown.

## Stop conditions

Stop and preserve the result when:

- adaptation changes labels, targets, or held-out cases;
- target characters leak into the sparse specimen;
- metrics improve only through blur, erosion, or manuscript-damage imitation;
- writer identity remains weaker than source-font identity;
- font vectorization hides raster failures;
- evidence cannot distinguish historical allographs from wrong labels;
- the available specimen is insufficient for the declared identity claim.

## Invalidated first executable result

The first MX-Font adapter, report, and development TTF remain preserved as a
failed engineering run. Human inspection found that source crops were bound to
incorrect character identities. The source was a dynamic sequence alignment
between detected manuscript cells and transcription lines; the benchmark then
treated those `silver_visual_sequence_alignment` labels as if they were exact.

Consequences:

- the 64-character training specimen was not trustworthy;
- held-out target identities were not trustworthy;
- all identity, content-preservation, and writer-similarity metrics from that
  run are invalid as model evidence;
- `out/P3477-Generated.ttf` demonstrates only font assembly and rendering, not
  writer reconstruction.

The immutable failed run is not deleted or rewritten. `benchmark.py` and
`adapt.py` now refuse to proceed unless a schema-v2 benchmark is fingerprinted
to the immutable, ready `human_image_annotation_dataset`.

## Current state and next actions

### Human evidence

`out/annotation_dataset.json` currently records:

| Queue | Reviewed | Accepted | Skipped | Remaining | Distinct labels | Ready |
|---|---:|---:|---:|---:|---:|---|
| `writer_specimen` | 122 | 78 | 44 | 98 | 51 | yes |
| `held_out_evaluation` | 0 | 0 | 0 | 220 | 0 | no |

The combined dataset is therefore not ready. The writer queue is sufficient for
a development-only 8-reference/24-target split, but no independent
generalization or recognition claim is possible until the held-out queue has at
least 16 distinct human-accepted labels. Luna suggestions remain untrusted;
every accepted label and crop is an explicit human assertion in the append-only
event history.

The canonical labeling service remains:

```text
.venv/Scripts/python.exe experiments/generative_hand_font/attest.py
http://127.0.0.1:3478/
```

Its active records are:

- `out/annotation_project.json`;
- `out/luna_first_pass.json`;
- `out/annotation_events.jsonl`;
- `out/annotation_images/`;
- `out/annotation_dataset.json`.

### Image-representation evidence

The current 24-glyph `glyph_alignment/prototype-v3` record shows:

- mean/max Kai-relative centroid residual `0.033041 / 0.077715` pixels;
- zero recorded margin breaches and zero canvas-edge contacts;
- no aggregate component/hole-count change from smoothing at the one audited
  threshold;
- three topology changes from micro-repair: `寒`, `復`, and `進`.

This makes gravity alignment and smoothing reasonable ablation candidates, not
defaults. The topology audit is too weak, float mass is not yet guaranteed
after clipping/quantization, and centroid/scale remain sensitive to detached
ink. Micro-repair stays review-only.

The page-cleanup prototype is likewise a readability study, not a selected
training transform. Its stronger white-background variants discard measurable
source-defined core ink and have not established faint-stroke safety.

### Blocking implementation facts

1. `smoke.py` does not consume the derived gravity/smoothing artifacts.
2. `adapt.py` currently non-uniformly square-resizes crops, Otsu-binarizes
   them, and applies a mandatory `2×2` close before style encoding and loss.
3. provenance and live crop-hash checks differ across `benchmark.py`,
   `smoke.py`, `adapt.py`, and `glyph_alignment.py`.
4. FontDiffuser is researched and selected but not yet integrated or
   fingerprinted locally.
5. the held-out annotation queue is empty.

### Ordered next actions

1. Implement Experiment 0's one derived-glyph builder and shared validation
   gate; add focused tests for aspect preservation, transform invariants,
   border ink, float/quantized mass, multithreshold topology, provenance, and
   deterministic regeneration.
2. Freeze a development-only manifest selecting 8 adaptation identities and 24
   character-disjoint targets from the existing 51-label writer pool, with
   wrong-writer and label-shuffled controls.
3. Run the corrected latent-only MX-Font smoke as the cheapest harness
   falsification. Inspect every character-disjoint development output and
   preserve the terminal result even if it fails.
4. Reproduce frozen FontDiffuser inference, then compare one-shot,
   multi-reference aggregation, and parameter-efficient adaptation as separate
   challenger identities.
5. Continue held-out human labeling independently; do not inspect held-out
   model outputs while selecting preprocessing, architecture, or thresholds.
6. If a writer model passes Q1–Q2, build the candidate-ranking benchmark for Q3.
7. Build the synthetic TTF and source-linked re-typeset passage only after the
   raster generator passes; vectorization must not make a failed raster model
   look successful.
