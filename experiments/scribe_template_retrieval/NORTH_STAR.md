# Scribe-Conditioned Glyph Recovery

Status: **first silver metric gate passed; human gold and page-0002 geometry pending** (2026-07-21).

## North star

**Use each manuscript as its own visual lexicon so damaged marks can be
interpreted against the same hand, while every proposed reading remains
traceable to source ink, comparative evidence, and explicit uncertainty.**

The goal is not to manufacture a cleaner-looking manuscript. The goal is to
recover more defensible characters from difficult manuscript images without
letting a language model, modern typeface, or synthetic rendering silently
replace the documentary evidence.

## Why this matters

Palimpsest exists to turn manuscript images into trustworthy, readable books.
The difficult word is *trustworthy*. A plausible transcription is not enough;
a reader must be able to distinguish what the image supports from what a model
or editor inferred.

Worn manuscripts create a visual problem before they create a language problem:

- strokes fade, merge, break, and disappear;
- stains, repairs, framing, and later marks resemble ink;
- one scribe's forms can differ sharply from modern printed glyphs;
- historical and regional forms may differ from the Unicode-era canonical
  shape;
- page-scale language context can make a wrong reading sound convincing.

A generic font is therefore a useful structural prior but a poor witness. The
manuscript itself contains a better source of visual evidence: characters and
components written repeatedly by the same hand under the same material and
photographic conditions.

## Current thesis

A manuscript should not need to teach the system “this crop is character X”
before the system can estimate broad properties of the hand. The primary thesis
is therefore a **pretrained content/style model**, not a per-character font
assembled only from labeled repetitions.

The model learns a population-level prior before seeing P.3477:

```text
many writers or fonts crossed with many known characters
-> separate reusable content structure from writer-dependent execution
```

At adaptation and inference:

```text
sparse same-hand crops, optionally unlabeled
-> writer-style representation

candidate identity + explicit content geometry
-> target-character representation

writer style + target character
-> distribution of plausible writer-conditioned glyphs
-> compare with held-out real ink
-> ranked candidates, calibrated rejection, and inspectable support
```

This is the distinction the experiment must preserve:

- the **style references** teach how the scribe tends to form strokes,
  components, spacing, compression, and terminals;
- the **content condition** supplies which character or allograph is being
  hypothesized, using a source raster, component tree, stroke graph, or
  historical exemplar;
- the **observed crop** remains the evidence against which hypotheses are
  tested.

The generator answers “what might character X look like in this hand?” It does
not answer “what character is this mark?” by itself. Identification requires
comparing competing character-conditioned hypotheses with the same real crop.

The first central claim is:

> When the target character is absent from the sparse style-reference set,
> correct-writer conditioning produces a more useful retrieval or alignment
> hypothesis for a held-out real occurrence than generic Kai, no-style
> generation, or wrong-writer conditioning, and adds measurable value beyond
> real-exemplar retrieval or direct recognizer adaptation.

Observed-character medoids and consensus prototypes remain important,
non-generative baselines. They test whether generation contributes anything
beyond retaining real ink.

If the claim survives held-out evaluation, the representation can enter the
existing `align` station as one bounded, reversible source of evidence without
changing its socket:

```text
page_image_clean + page_transcription -> page_alignment
```

A stronger `read` experiment comes later. It must compare generation-assisted
recognition with direct end-to-end adaptation on identical real labels and must
keep visual evidence separate from language-model influence.

## External research decision

The research area exists. It is usually called **few-shot font generation**,
**one-shot font generation**, **content/style disentanglement**, or
**writer-adaptive handwriting recognition**.

The strongest directly relevant results establish parts of the desired system:

- [MX-Font](https://arxiv.org/abs/2104.00887) used four reference glyphs and
  evaluated 214 Chinese characters and 28 styles excluded from training.
  Style-reference labels are unnecessary at inference, but a source-font
  raster supplies the target character and supervised population training
  teaches the factorization.
- [LF-Font](https://arxiv.org/abs/2009.11042) generates Chinese glyphs from
  eight references using localized component/style factorization. Its
  [implementation](https://github.com/clovaai/lffont) and weights are
  MIT-licensed, apart from inherited notices.
- [FontDiffuser](https://arxiv.org/abs/2312.12142) demonstrates one-reference
  generation across unseen Chinese characters and unseen styles. Its
  [checkpointed implementation](https://github.com/yeungchenwa/FontDiffuser)
  is reproducible on its documented Linux/CUDA stack, but the repository has
  no declared software license and was evaluated on clean modern fonts.
- [GC-DDPM](https://arxiv.org/abs/2305.15660) conditions Chinese handwriting
  synthesis on a Kai content glyph and writer identity, then trains recognition
  on generated unseen categories. It provides unusually direct downstream
  recognition evidence, but required hundreds of writers, millions of samples,
  and eight V100 GPUs; it is not sparse new-scribe adaptation.
- [ScrabbleGAN](https://arxiv.org/abs/2003.10557) showed modest held-out-real
  HTR gains from mixing real and synthetic Latin handwriting. Its domain
  experiment also showed that matching style alone can fail while matching the
  target lexicon helps, proving that visual plausibility is not the endpoint.
- [MetaWriter](https://arxiv.org/abs/2505.20513) adapts less than one percent of
  a recognizer using five unlabeled lines from a new writer, but its evidence is
  modern Latin IAM/RIMES rather than Chinese historical manuscripts.

No verified model combines sparse unlabeled historical-Chinese references,
unseen-character synthesis, natural-damage robustness, calibrated uncertainty,
and end-to-end transcription. The repository should therefore test a modular
hybrid rather than assume a turnkey model exists:

```text
frozen pretrained content/style generator
+ sparse writer-style conditioning
+ real-exemplar retrieval
+ direct recognizer-adaptation control
+ explicit damage model
+ calibrated candidate reranking and abstention
```

Training an end-to-end model from scratch on three P.3477 pages cannot identify
thousands of character classes. Fine-tuning a pretrained recognizer remains a
required comparator, but it does not remove the sparse-label or unseen-class
problem and can entangle visual evidence with a language prior.

## What “canonical” means here

A canonical glyph is a **writer-conditioned visual prototype**: a stable,
normalized representation distilled from several verified examples of one
character. It may be stored as a raster, distance field, contour model,
embedding, or font glyph.

It is not ground truth.

Ground truth remains:

```text
source-image crop
+ independently adjudicated character identity
+ segmentation/damage judgment
+ retained provenance
```

A synthetic glyph can support or challenge a reading. It cannot certify the
label from which it was generated. This separation prevents an epistemic loop
in which an automatic transcription labels a crop, a generator learns that
label, and similarity to the generated result is then presented as proof that
the transcription was correct.

## Evidence already established

The repository contains two working precursors.

### Hand-font prototype

`experiments/hand_font/candidate.py` harvested 875 automatically labeled P.3477
crops and produced a real 4 KB TrueType font with 15 consensus characters.
The lab record judges approximately 10 glyphs visually authentic and traces the
remaining failures to incorrect character binding or damaged crops. This proves
that crop normalization, consensus selection, contour vectorization, Unicode
mapping, font assembly, and rendering work end to end. It does not yet prove
recognition value.

### Analysis by synthesis

`experiments/synthesis/candidate.py` rewrote a P.3477 page using other instances
from the same unsupervised shape clusters. It peer-rewrote 246 of 689 cells with
median ink intersection-over-union 0.435; 443 cells remained singletons. The
synthetic page is visibly recognizable. This establishes that repeated
manuscript forms carry recoverable writer-specific structure and identifies
coverage as a primary constraint.

### Generic-font baseline

`experiments/separation2/features.py` reaches 79.7% top-1 retrieval when
synthetically perturbed Kai renders are retrieved against a 20,992-character
Kai gallery. That is a feature self-test, not manuscript recognition evidence.
The earlier `shape_prior` experiment found that almost any blob can match
something in a gallery that large. The new experiment must therefore measure
character identity on held-out manuscript crops rather than rely on raw template
similarity or visual plausibility.

## Present bottleneck

The bottleneck is trustworthy binding, not font generation.

The current annotation material contains 141 proposed crop-to-character
bindings in:

```text
experiments/align_pairing/out/ground_truth/labels.csv
```

Its `verdict` fields are unfilled. Earlier automatic audits were too corrupted
by binding noise to bootstrap reliable class means. Training or evaluating a
generator on those claims without adjudication would teach and reward the same
errors.

The immediate work is therefore to establish a held-out, human-adjudicated crop
set before adding model capacity.

## First evidence gate

Experiment name:

```text
align-scribe-template-retrieval-v1
```

Goal:

> Demonstrate that a pretrained content/style model can infer useful properties
> of the P.3477 hand from sparse same-writer crops and transfer them to a target
> character absent from those references, improving identification of untouched
> real manuscript ink without presenting generated pixels as documentary
> evidence.

Primary causal comparison:

```text
baseline:
real exemplars + direct visual adaptation

challenger:
the identical system + frozen writer-conditioned synthetic hypotheses
```

The minimum useful effect is five absolute percentage points of top-1 retrieval
accuracy on the identical held-out crops, with a positive 95% paired
block-bootstrap confidence interval. Correct-writer conditioning must also beat
no-style and wrong-writer controls. These conditions test transferred writer
signal rather than generic augmentation.

A pass authorizes preparation of a same-socket `align` challenger. It does not
authorize a production recipe change, qualification, promotion, or synthetic
replacement of source evidence.

Evidence status: the original 141 proposed crop bindings remain without human
adjudication apart from six automatic junk classifications. The executable
trial therefore creates a separate **silver** manifest by aligning the
independently produced page transcription to source ink with generic-Kai visual
costs. Every crop retains its source bbox and label provenance; generated model
pixels never supply or validate a label. Silver results may establish technical
feasibility, but they cannot authorize qualification or promotion.

Development split:

```text
writer-reference page:  page_0000
untouched held-out page: page_0001
deferred geometry audit: page_0002
```

The previous two-page/reference, page-0002/holdout proposal was not executable:
the current geometry recovered only 6 of page 0002's 40 transcription lines
because dense interlinear writing merged image columns. Page 0001 provides a
complete 28-column held-out page for the first causal test. Page 0002 remains a
required later geometry and natural-damage test rather than disappearing from
the program.

Compared systems:

1. generic Kai template;
2. nearest verified real exemplar and class medoid where available;
3. frozen generator with no writer conditioning;
4. the same generator with correct-writer style references;
5. the same generator with shuffled or wrong-writer references;
6. direct visual adaptation or retrieval using the same real samples but no
   synthetic glyphs;
7. direct adaptation or retrieval plus writer-conditioned synthetic glyphs.

Style references must come only from training pages. For the unseen-character
cell, the target identity must be absent from every style reference even if the
style encoder does not consume reference labels.

Primary observations:

- top-1 and top-5 character retrieval accuracy;
- mean reciprocal rank and true-character rank change;
- true-character margin over the nearest wrong character;
- incremental utility of synthesis over the otherwise identical no-synthesis
  system;
- correct-writer advantage over no-style and wrong-writer controls;
- covered-class rate, rejection, false-match rate, and end-to-end coverage.

Protected evidence slices:

- faint or worn ink;
- fragmented strokes;
- merged characters or neighboring ink;
- low-confidence geometry;
- visually confusable characters;
- characters with one training example;
- characters absent from training.

Unseen and uncovered characters remain in the end-to-end denominator. A system
cannot improve its score by declining to report difficult cases without also
reporting the resulting coverage loss.

Development success requires all of the following:

1. correct-writer conditioning beats generic Kai, no-style generation, and
   wrong-writer conditioning on identical held-out crops;
2. the primary paired difference has a positive 95% block-bootstrap confidence
   interval and clears the preregistered minimum useful effect;
3. adding synthetic hypotheses improves the otherwise identical real-exemplar
   or direct-adaptation system; beating Kai alone is insufficient;
4. the gain remains when target identities are absent from style references;
5. no more than two points of top-5 regression;
6. no more than five points of regression on the faint/worn protected slice;
7. no increase in high-confidence wrong answers at fixed coverage;
8. explicit coverage, rejection, provenance, and worst-failure reporting;
9. blinded visual review of the worst false matches before trusting the
   aggregate.

This first gate has no provider calls and no authorized spend.

## First silver result

The executable trial used four target-excluded P.3477 crops from page 0000 to
condition the released MX-Font checkpoint, generated hypotheses for 190
candidate characters, and evaluated 522 untouched source-ink crops from page
0001. The manifest contains 469 writer-reference crops and preserves every
source image, transcription, bbox, and SHA-256 identity. Its labels remain
silver rather than human gold.

Primary paired result:

```text
real-exemplar-or-Kai baseline top-1:       23.75%
baseline + correct-writer hypotheses:      30.08%
paired improvement:                         6.32 points
95% line-block-bootstrap interval:      [3.13, 9.67] points
```

The stronger target-absence slice contains 246 held-out crops across 108
characters absent from the entire writer-reference page. On that slice,
correct-writer conditioning beat the otherwise identical no-writer control by
2.44 points, 95% interval `[0.40, 4.61]`, and the wrong-style control by 2.03
points, interval `[0.43, 3.77]`. All four reference crops were verified to come
from page 0000 and to exclude the target's claimed identity.

The challenger also improved top-5 retrieval from 35.82% to 43.49%, reduced
high-confidence wrong answers from 5.36% to 3.45%, and reduced error at fixed
80% coverage from 72.25% to 64.59%. The faint-ink proxy moved from 11.45% to
10.69% top-1, a 0.76-point regression inside the preregistered five-point
limit. Seen-on-reference-page top-1 regressed from 35.87% to 33.70%; the
aggregate win is therefore not permission to replace real exemplars.

The claim is deliberately narrow. MX-Font generation alone did not beat every
wrong-style condition; the positive writer-specific result appears when its
hypothesis is combined with retained real/generic evidence. Visual review of
the worst apparent failures also found visibly incorrect silver bindings,
confirming that this run demonstrates technical transfer signal rather than
historical character truth. Human adjudication, page-0002 geometry repair, and
a frozen gold suite remain mandatory before any `align` challenger,
qualification, or production decision.

Reproduction:

```text
python experiments/scribe_template_retrieval/prepare.py
python experiments/scribe_template_retrieval/candidate.py
python experiments/scribe_template_retrieval/evaluate.py
```

The terminal report is written to
`experiments/scribe_template_retrieval/out/report.json`; generated pixels remain
hypotheses and are never used as labels.


## Learning ladder

We advance one causal claim at a time.

### 1. Provenance, labels, and hand attribution

Adjudicate crop identity, segmentation state, damage state, and whether the
three folios represent one hand. Preserve unknown, disputed, historical
allograph, and unencoded-sign states separately from normalized Unicode.

### 2. Non-generative controls

Establish generic Kai, nearest real exemplar, class medoid, fixed-feature
retrieval, and direct recognizer adaptation. Without these controls, a
generator can receive credit merely for adding data or denoising a template.

### 3. Frozen pretrained content/style generation

Test MX-Font/LF-Font-class localized models and a one-shot diffusion model
without training on P.3477. Compare 1, 2, 4, 8, and 16 correct-hand references,
plus no-style and wrong-hand controls. Report labeled and unlabeled-reference
conditions separately.

### 4. Unseen-character and incremental-utility gate

Exclude each target identity from the style references. Evaluate real held-out
ink, not synthetic image beauty. The decisive comparison is:

```text
real exemplars + direct adaptation + synthesis
versus
real exemplars + direct adaptation
```

Also report seen-character reconstruction, unseen combinations of seen
components, unseen components/allographs, and historical out-of-vocabulary
signs separately.

### 5. Same-socket `align` variant

Only after synthesis adds retrieval value, add its calibrated score as one
bounded term in the existing alignment cost. Test association accuracy, false
binding, unmatched-character recall, and column order while preserving the
invariant that uncertainty remains unbound.

### 6. Recognition adaptation comparison

At fixed verified-label budgets, compare frozen recognition, output-head-only
tuning, compact prompts/adapters, full fine-tuning, real-only augmentation, and
real-plus-synthetic augmentation. Keep visual-only output separate from
language-model rescoring.

### 7. Learned manuscript generator

Fine-tune or train a generator only if the frozen-generator gate shows useful
writer signal and enough real labels exist to avoid circular pseudo-training.
Learn localized writer tokens or a writer posterior rather than asserting one
fully known style vector.

### 8. Same-socket `read` variant

Use the visual prior to support diplomatic transcription on untouched pages.
Compare character error, invented-character rate, region completeness,
calibration, abstention, and downstream alignment. Language context remains
separate supporting evidence and cannot silently overrule visual
contradictions.

## Non-goals

This work does not aim to:

- repaint or “restore” the canonical source image;
- pass generated glyphs off as documentary evidence;
- force a character label when the mark is genuinely illegible;
- train on qualification or held-out cases;
- claim that a handful of references yields an evidentially authoritative
  complete Chinese font without held-out real validation;
- replace expert adjudication with visual similarity;
- optimize a beautiful specimen sheet instead of retrieval and alignment;
- create a new production artifact or station before downstream value is
  demonstrated;
- change a production recipe during development.

## Stop or redirect conditions

We stop or redirect before adding model capacity when:

- adjudication cannot establish a usable real held-out set or reliable hand
  grouping;
- correct-writer conditioning does not beat no-style and wrong-writer controls;
- synthesis beats Kai but adds nothing beyond real exemplars or direct
  adaptation;
- improvement exists only on characters present in the style references or on
  clean repeated characters;
- performance depends on test-page exposure, hidden transcription leakage, or
  language context in the visual-only arm;
- coverage is too low to affect alignment or reading materially;
- high-confidence errors, rare/allograph regressions, or calibration worsen;
- visual failure review reveals scorer artifacts or modern-template bias rather
  than real gains.

A negative result is useful. It tells us whether the next investment belongs in
segmentation, binding, exemplars, representation learning, or generation.

## Product-level definition of success

This program succeeds when Palimpsest can recover a difficult character more
reliably because it has seen how the same hand forms related evidence elsewhere,
while preserving all of the following:

- the original image remains untouched;
- the proposed identity is linked to exact source coordinates;
- supporting exemplars or prototypes are inspectable;
- confidence and rejection are explicit;
- generated evidence is labeled synthetic;
- uncertainty survives into the diplomatic and editorial record;
- improvement remains measurable on held-out manuscripts or pages.

The terminal judge is still the trustworthy book: more readable where evidence
supports recovery, more honest where it does not.

## Sources of truth

- Product purpose and evidence separation: `README.md`
- Experiment and promotion protocol: `docs/OPERATIONS.md`
- Evaluation principles: `docs/EVALUATION.md`
- Live `align` socket: `docs/CONTRACTS.md`
- Glyph geometry and alignment behavior: `docs/GLYPHS.md`
- Separation research protocol: `experiments/SEPARATION.md`
- Prior measured findings: `experiments/LOG.md`
- Existing font prototype: `experiments/hand_font/candidate.py`
- Existing synthesis prototype: `experiments/synthesis/candidate.py`
- Existing feature baseline: `experiments/separation2/features.py`
